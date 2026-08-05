"""DBS multi-currency PDF adapter tests.

Pins the adapter to real statements on disk (skipped when absent). The
regression of record here is the multi-PAGE currency section: DBS reprints
"Currency: XXX" + "Balance Brought Forward" + "Balance Carried Forward" on
every continuation page. Before the VR-1b fix the adapter let the last page's
brought-forward overwrite the section's true opening anchor, so the section
opening was wrong and the self-reconcile chain desynced (e.g. USD Sep-2024 was
off by exactly 65,090.00). The section opening must be the FIRST brought-forward
seen; the closing the LAST carried-forward.
"""
import os
from decimal import Decimal

import pytest

from src.services.csv_adapters.dbs_pdf import DBSPDFAdapter

# The complete corpus lives in the main checkout; the worktree carries a partial
# copy. Try both roots so the test runs wherever the fixtures actually are.
_ROOTS = [
    os.path.join(os.path.dirname(__file__), "..", "documentation", "wip",
                 "bank_statements", "DBS"),
    "/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api/"
    "documentation/wip/bank_statements/DBS",
]


def _find(rel):
    for root in _ROOTS:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return None


# USD Sep-2024: USD section spans pages 2-3; the multi-page anchor bug lived here.
PDF_MULTIPAGE_TT = _find("2024/00726694930003_C394652019G_USD_092024.pdf")


@pytest.mark.skipif(PDF_MULTIPAGE_TT is None, reason="real DBS statement fixture not on disk")
class TestDbsMultiPageSection:
    @pytest.fixture(scope="class")
    def parsed(self):
        a = DBSPDFAdapter()
        secs = a.parse_pdf(open(PDF_MULTIPAGE_TT, "rb").read())
        return a, secs

    def test_section_opening_is_first_brought_forward_not_last_page(self, parsed):
        """The USD section opens on page 2 at 155,715.10. The page-3
        continuation reprints 90,625.10 as ITS brought-forward — that must NOT
        become the section opening (the pre-fix failure mode)."""
        a, _ = parsed
        assert a.section_balances["USD"]["brought_forward"] == Decimal("155715.10")
        assert a.section_balances["USD"]["carried_forward"] == Decimal("65550.10")

    def test_usd_section_self_reconciles(self, parsed):
        a, secs = parsed
        bal = a.section_balances["USD"]
        total = sum((r.amount for r in secs["USD"]), Decimal("0"))
        assert bal["brought_forward"] + total == bal["carried_forward"]

    def test_all_sections_self_reconcile(self, parsed):
        a, secs = parsed
        for ccy, bal in a.section_balances.items():
            if "brought_forward" not in bal or "carried_forward" not in bal:
                continue
            total = sum((r.amount for r in secs.get(ccy, [])), Decimal("0"))
            assert bal["brought_forward"] + total == bal["carried_forward"], (
                f"{ccy} section did not self-reconcile")

    def test_tt_rows_signed_by_balance_chain(self, parsed):
        """Every transaction's running balance must follow from the previous
        one plus the signed amount — the sign-recovery invariant."""
        a, secs = parsed
        prev = a.section_balances["USD"]["brought_forward"]
        for r in secs["USD"]:
            assert prev + r.amount == r.running_balance, (
                f"chain broke at {r.transaction_date} {r.description[:40]!r}")
            prev = r.running_balance
