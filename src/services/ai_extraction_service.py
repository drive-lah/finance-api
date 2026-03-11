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

Our company entities are:
{entity_list}

Return ONLY a JSON object with these exact fields (use null for missing fields):
{{
  "vendor_name": "string - company name on invoice (the seller/from party)",
  "vendor_tax_id": "string or null - vendor ABN, UEN, GST reg, VAT number if present",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "total_amount": number (numeric only, no currency symbols),
  "subtotal_amount": number or null (amount before tax),
  "tax_amount": number or null (GST/VAT amount),
  "currency": "3-letter ISO code e.g. SGD, USD, EUR, AUD",
  "service_period_start": "YYYY-MM-DD or null - start of billing/service period",
  "service_period_end": "YYYY-MM-DD or null - end of billing/service period",
  "description": "string - 1-2 sentence description of what was invoiced",
  "suggested_coa_account": "one of: 6000 (Salaries), 6100 (Marketing), 6200 (HR), 6300 (Office/Rent), 6400 (Travel), 6500 (Professional Fees/Legal/Accounting), 6600 (Banking Fees), 6700 (Technology/Software/Cloud), 6800 (Other OpEx), 5010 (Payment Processing), 5030 (Device Costs), 7100 (FX), or null if unclear",
  "bill_to_entity_hint": "string or null - which of our entities is this billed to, based on the Bill To / To / Attention field",
  "confidence": number between 0 and 1
}}

Rules:
- vendor_name: the company SENDING the invoice (not us)
- bill_to_entity_hint: look for our entity names in the Bill To section. Return the exact entity name if matched, or null
- For service period: look for "for the period", "subscription period", "billing period", "invoice for [month]", month names, or date ranges. If invoice says "for February 2026" or "February 2026 subscription", set service_period_start = first day of that month, service_period_end = last day of that month.
- For COA: AWS/GCP/Azure/Cloudflare/Digital Ocean/GitHub → 6700. Legal/accounting → 6500. Office rent → 6300. Payroll/salary → 6000. Marketing/ads → 6100. Bank charges → 6600.
- tax_amount: extract the GST/VAT line item amount (not the total). Look for "GST", "VAT", "Tax" line. If no tax line exists, set to null — do NOT guess.
- subtotal_amount: the pre-tax amount if shown separately, otherwise null
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

    def _empty_result(self, raw_text: str = "", error: str = "") -> dict:
        return {
            "vendor_name": None, "vendor_tax_id": None,
            "invoice_number": None, "invoice_date": None, "due_date": None,
            "total_amount": None, "subtotal_amount": None, "tax_amount": None,
            "currency": None, "service_period_start": None, "service_period_end": None,
            "description": None, "suggested_coa_account": None,
            "bill_to_entity_hint": None,
            "confidence": 0.0, "raw_text": raw_text, "extraction_error": error or None,
        }

    def extract_invoice_data(self, pdf_bytes: bytes, entity_names: list[str] | None = None) -> dict:
        """
        Extract structured invoice data from PDF bytes.

        entity_names: list of entity names to pass to the prompt for Bill-To matching.
        Returns a dict with extracted fields plus raw_text and extraction_error.
        """
        try:
            raw_text = self.extract_text_from_pdf(pdf_bytes)
            if not raw_text.strip():
                return self._empty_result(
                    raw_text,
                    "PDF appears to have no extractable text (may be a scanned image)",
                )

            entity_list = "\n".join(f"- {n}" for n in (entity_names or [])) or "- (none provided)"
            client = self._get_client()
            prompt = EXTRACTION_PROMPT.format(
                entity_list=entity_list,
                invoice_text=raw_text[:8000],
            )

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text.strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            extracted = json.loads(response_text)
            extracted["raw_text"] = raw_text
            extracted["extraction_error"] = None
            return extracted

        except json.JSONDecodeError as e:
            logger.error(f"AI returned invalid JSON: {e}")
            return self._empty_result("", "AI extraction failed: invalid JSON response")
        except Exception as e:
            logger.error(f"AI extraction error: {e}", exc_info=True)
            return self._empty_result("", str(e))


ai_extraction_service = AIExtractionService()
