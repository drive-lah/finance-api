"""
Bank file import adapter registry.

Maps file_adapter strings (as stored on FinanceBankAccount.file_adapter)
to the adapter class that handles that bank's CSV/PDF format.

To add a new bank:
  1. Create src/services/csv_adapters/<bank>.py with a BankCSVAdapter subclass
  2. Import it here and add an entry to ADAPTER_REGISTRY

file_adapter matching is case-insensitive and strips whitespace.
"""
from src.services.csv_adapters.base import BankCSVAdapter
from src.services.csv_adapters.ocbc import OCBCCsvAdapter
from src.services.csv_adapters.ocbc_pdf import OCBCPdfAdapter
from src.services.csv_adapters.cba import CBAAdapter
from src.services.csv_adapters.dbs_pdf import DBSPDFAdapter


class OCBCAdapter(BankCSVAdapter):
    """
    Smart wrapper adapter for OCBC Bank that auto-detects CSV vs PDF format.

    Accepts either CSV string or PDF bytes as input and automatically dispatches
    to the appropriate parser (OCBCCsvAdapter or OCBCPdfAdapter).
    """

    def __init__(self):
        self.errors = []
        self._csv_adapter = OCBCCsvAdapter()
        self._pdf_adapter = OCBCPdfAdapter()

    @property
    def bank_name(self) -> str:
        return "OCBC"

    def parse(self, content: str | bytes) -> list:
        """
        Auto-detect format and parse accordingly.

        Args:
            content: CSV string or PDF bytes. Detects format automatically.

        Returns:
            List of NormalizedRow instances.
        """
        self.errors = []

        # Detect format: PDF files start with b'%PDF' or '%PDF' string
        is_pdf = False
        if isinstance(content, bytes):
            is_pdf = content.startswith(b'%PDF')
        else:
            is_pdf = content.startswith('%PDF')

        try:
            if is_pdf:
                # PDF format: content should be bytes or convertible to bytes
                if isinstance(content, str):
                    content_bytes = content.encode('latin-1')
                else:
                    content_bytes = content

                rows = self._pdf_adapter.parse(content_bytes)
                self.errors = list(self._pdf_adapter.errors)
            else:
                # CSV format: content should be string
                if isinstance(content, bytes):
                    content_str = content.decode('utf-8')
                else:
                    content_str = content

                rows = self._csv_adapter.parse(content_str)
                self.errors = list(self._csv_adapter.errors)

            return rows

        except Exception as e:
            self.errors.append(f"Parse failed: {str(e)}")
            return []

    def fingerprint_fields(self, row) -> list[str]:
        """
        Fingerprint: [date, amount, reference, running_balance]
        Both CSV and PDF use the same fingerprinting scheme.
        """
        return [
            row.transaction_date.isoformat(),
            f"{row.amount:.2f}",
            (row.reference_number or "").strip().lower() if hasattr(row, 'reference_number') else "",
            f"{row.running_balance:.2f}" if row.running_balance is not None else "",
        ]

# Keys are lowercase file_adapter values. Values are adapter classes (not instances).
ADAPTER_REGISTRY: dict[str, type[BankCSVAdapter]] = {
    "ocbc": OCBCAdapter,
    "cba": CBAAdapter,
    "commonwealth": CBAAdapter,
    "commonwealth bank": CBAAdapter,
    "dbs": DBSPDFAdapter,
}

# Metadata for each adapter: label shown in UI and accepted file types
ADAPTER_META: dict[str, dict] = {
    "ocbc": {"label": "OCBC", "accepts": ".csv,.pdf"},
    "cba": {"label": "CBA / Commonwealth Bank", "accepts": ".csv,.pdf"},
    "commonwealth": {"label": "CBA / Commonwealth Bank", "accepts": ".csv,.pdf"},
    "commonwealth bank": {"label": "CBA / Commonwealth Bank", "accepts": ".csv,.pdf"},
    "dbs": {"label": "DBS", "accepts": ".pdf"},
}


def get_adapter(bank_name: str) -> BankCSVAdapter:
    """
    Return an instantiated adapter for the given bank_name.

    Args:
        bank_name: The bank_name field from FinanceBankAccount (e.g. "OCBC").

    Returns:
        An instantiated BankCSVAdapter subclass.

    Raises:
        ValueError: If no adapter is registered for bank_name.
                    Message includes the list of supported banks.
    """
    key = bank_name.strip().lower()
    adapter_class = ADAPTER_REGISTRY.get(key)

    if adapter_class is None:
        supported = sorted(ADAPTER_REGISTRY.keys())
        raise ValueError(
            f"No CSV adapter registered for bank '{bank_name}'. "
            f"Supported banks: {supported}. "
            f"Add an adapter in src/services/csv_adapters/ to support this bank."
        )

    return adapter_class()
