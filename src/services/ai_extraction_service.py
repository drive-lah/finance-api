"""
AI Invoice Extraction Service

Uses pdfplumber to extract text from invoice PDFs, then sends the text
to Claude API to extract structured invoice data (vendor, amounts, dates,
service period, COA suggestion).
"""
import json
import logging
import os
from typing import Optional
import pdfplumber
import io
import anthropic

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are an expert accounting assistant. Extract structured data from this invoice.

Return ONLY a JSON object with these exact fields (use null for missing fields):
{
  "vendor_name": "string - company name on invoice",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "total_amount": number (numeric only, no currency symbols),
  "currency": "3-letter ISO code e.g. SGD, USD, EUR, AUD",
  "service_period_start": "YYYY-MM-DD or null - start of billing/service period",
  "service_period_end": "YYYY-MM-DD or null - end of billing/service period",
  "description": "string - brief description of what was invoiced",
  "suggested_coa_account": "one of: 6000 (Salaries), 6100 (Marketing), 6200 (HR), 6300 (Office/Rent), 6400 (Travel), 6500 (Professional Fees/Legal/Accounting), 6600 (Banking Fees), 6700 (Technology/Software/Cloud), 6800 (Other OpEx), 5010 (Payment Processing), 5030 (Device Costs), 7100 (FX), or null if unclear",
  "confidence": number between 0 and 1
}

Rules:
- For service period: look for phrases like "for the period", "subscription period", "billing period", date ranges in the invoice
- For COA: AWS/GCP/Azure/Cloudflare/Digital Ocean → 6700. Legal/accounting → 6500. Office rent → 6300. Payroll/salary → 6000. Marketing/ads → 6100.
- Return ONLY the JSON, no explanation

Invoice text:
{invoice_text}"""


class AIExtractionService:
    """Extracts structured data from invoice PDFs using Claude AI."""

    def __init__(self):
        self._client: Optional[anthropic.Anthropic] = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract plain text from all pages of a PDF."""
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text(x_tolerance=3, y_tolerance=3)
                if text:
                    text_parts.append(text)
        return "\n\n".join(text_parts)

    def extract_invoice_data(self, pdf_bytes: bytes) -> dict:
        """
        Extract structured invoice data from PDF bytes.

        Returns a dict with extracted fields plus:
        - raw_text: the extracted PDF text
        - extraction_error: error message if extraction failed (None on success)
        """
        try:
            raw_text = self.extract_text_from_pdf(pdf_bytes)
            if not raw_text.strip():
                return {
                    "vendor_name": None, "invoice_number": None, "invoice_date": None,
                    "due_date": None, "total_amount": None, "currency": None,
                    "service_period_start": None, "service_period_end": None,
                    "description": None, "suggested_coa_account": None,
                    "confidence": 0.0, "raw_text": raw_text,
                    "extraction_error": "PDF appears to have no extractable text (may be a scanned image)",
                }

            client = self._get_client()
            prompt = EXTRACTION_PROMPT.format(invoice_text=raw_text[:8000])  # cap at 8k chars

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",  # fast, cheap, good at structured extraction
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text.strip()
            # Strip markdown code fences if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            extracted = json.loads(response_text)
            extracted["raw_text"] = raw_text
            extracted["extraction_error"] = None
            return extracted

        except json.JSONDecodeError as e:
            logger.error(f"AI returned invalid JSON: {e}")
            return {
                "vendor_name": None, "invoice_number": None, "invoice_date": None,
                "due_date": None, "total_amount": None, "currency": None,
                "service_period_start": None, "service_period_end": None,
                "description": None, "suggested_coa_account": None,
                "confidence": 0.0, "raw_text": "",
                "extraction_error": f"AI extraction failed: invalid JSON response",
            }
        except Exception as e:
            logger.error(f"AI extraction error: {e}", exc_info=True)
            return {
                "vendor_name": None, "invoice_number": None, "invoice_date": None,
                "due_date": None, "total_amount": None, "currency": None,
                "service_period_start": None, "service_period_end": None,
                "description": None, "suggested_coa_account": None,
                "confidence": 0.0, "raw_text": "",
                "extraction_error": str(e),
            }


ai_extraction_service = AIExtractionService()
