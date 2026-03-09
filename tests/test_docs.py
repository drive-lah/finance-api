"""
Documentation consistency tests.

These tests enforce that documentation stays in sync with the code.
They are intentionally simple and fast — they parse plain text, not
the database or HTTP layer.

If a test here fails, update the relevant documentation file.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
API_MD = REPO_ROOT / "documentation" / "API.md"
SYSTEM_OVERVIEW_MD = REPO_ROOT / "documentation" / "SYSTEM_OVERVIEW.md"


def test_every_registered_adapter_documented_in_api_md():
    """
    Every bank in ADAPTER_REGISTRY must appear in API.md.

    When you add a new adapter, add a row to the bank adapter table in API.md.
    """
    from src.services.csv_adapters.registry import ADAPTER_REGISTRY

    api_content = API_MD.read_text()

    for bank_name in ADAPTER_REGISTRY:
        assert bank_name.upper() in api_content.upper(), (
            f"Bank '{bank_name}' is registered in ADAPTER_REGISTRY but not documented in "
            f"documentation/API.md. Add a row to the 'Supported bank adapters' table."
        )


def test_every_registered_adapter_documented_in_system_overview():
    """
    Every bank in ADAPTER_REGISTRY must appear in SYSTEM_OVERVIEW.md.

    When you add a new adapter, add a row to the bank adapter table in SYSTEM_OVERVIEW.md.
    """
    from src.services.csv_adapters.registry import ADAPTER_REGISTRY

    overview_content = SYSTEM_OVERVIEW_MD.read_text()

    for bank_name in ADAPTER_REGISTRY:
        assert bank_name.upper() in overview_content.upper(), (
            f"Bank '{bank_name}' is registered in ADAPTER_REGISTRY but not documented in "
            f"documentation/SYSTEM_OVERVIEW.md. Add a row to the bank adapter table."
        )


def test_api_md_has_maintenance_rule():
    """API.md must contain the maintenance rule reminding devs to update docs."""
    content = API_MD.read_text()
    assert "same commit" in content.lower() or "maintenance rule" in content.lower(), (
        "API.md appears to be missing its maintenance rule. "
        "Ensure the file instructs developers to update docs in the same commit as code changes."
    )


def test_flask_routes_documented_in_api_md():
    """
    Every registered Flask route prefix must appear in API.md.

    This catches new blueprints that were added without documentation.
    Only checks the URL prefix of each blueprint — not individual endpoints.
    """
    from src.app import create_app

    app = create_app()
    api_content = API_MD.read_text()

    # Collect unique /api/finance/<module> prefixes from registered routes
    prefixes: set[str] = set()
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/api/finance/"):
            # e.g. /api/finance/accounts/... → accounts
            parts = rule.rule.split("/")
            if len(parts) >= 4:
                prefixes.add(parts[3])  # the module segment

    undocumented = [p for p in prefixes if f"/api/finance/{p}" not in api_content]

    assert not undocumented, (
        f"These route prefixes exist in the app but are not documented in API.md: "
        f"{sorted(undocumented)}. Add endpoint documentation for each."
    )
