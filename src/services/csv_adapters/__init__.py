"""CSV adapters for bank statement imports."""
from src.services.csv_adapters.registry import get_adapter

__all__ = ["get_adapter"]
