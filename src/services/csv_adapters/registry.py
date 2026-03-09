"""
Bank CSV adapter registry.

Maps bank_name strings (as stored on FinanceBankAccount.bank_name)
to the adapter class that handles that bank's CSV format.

To add a new bank:
  1. Create src/services/csv_adapters/<bank>.py with a BankCSVAdapter subclass
  2. Import it here and add an entry to ADAPTER_REGISTRY

bank_name matching is case-insensitive and strips whitespace.
"""
from src.services.csv_adapters.base import BankCSVAdapter
from src.services.csv_adapters.ocbc import OCBCAdapter

# Keys are lowercase bank_name values. Values are adapter classes (not instances).
ADAPTER_REGISTRY: dict[str, type[BankCSVAdapter]] = {
    "ocbc": OCBCAdapter,
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
