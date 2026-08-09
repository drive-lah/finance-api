"""Incident -> double-entry projection (ledger-plan STEP-3).

The single projection from an incident obligation to counterparty-tagged double-entry JE lines,
written ONCE against the IMS incident shape (so IMS cutover is a source-swap). COA per leg comes from
the finance-owned `finance_incident_coa_map` (POL-114), keyed on the IMS type_code(+sub).

Per leg (amounts in minor units -> decimal at this seam, per ledger-plan O-4):
  GUEST leg (amount_guest_minor):
    > 0 guest owes (charge/receivable):  Dr GUEST_AR [guest]  / Cr guest_coa (revenue)
    < 0 we owe guest (refund):           reverse
  HOST leg (amount_host_delta_minor):
    > 0 host credit (we pay host):       Dr host_coa (cost)   / Cr HOST_AP [host]
    < 0 host debit (host owes us):        reverse
The platform delta is implicit — each leg balances internally, so guest-revenue minus host-cost IS
the platform margin already sitting in the P&L accounts; no separate platform line is needed.

build_lines() is a pure function (balanced JE lines) — the testable accounting heart. project() then
resolves the guest/host counterparties (external namespace 'platform_user', POL-112) and posts.
"""
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.counterparty import FinanceCounterparty
from src.models.incident import FinanceIncident, FinanceIncidentCoaMap

# Default control accounts (overridable). Guest receivable + host payable.
GUEST_AR = "1100"   # Accounts Receivable (guest incident charges)
HOST_AP = "2120"    # Host payables (incident host payouts/debits)

PLATFORM_USER_SYSTEM = "platform_user"


def _minor_to_decimal(minor: int) -> Decimal:
    return (Decimal(minor) / Decimal(100)).quantize(Decimal("0.01"))


def get_coa_map(db: Session, type_code: str, sub_type_code: Optional[str]) -> Optional[FinanceIncidentCoaMap]:
    """Most-specific-first: exact (type, sub) then (type, NULL). Active rows only."""
    rows = (
        db.execute(
            select(FinanceIncidentCoaMap).where(
                FinanceIncidentCoaMap.type_code == type_code,
                FinanceIncidentCoaMap.active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    exact = [r for r in rows if r.sub_type_code == sub_type_code]
    if exact:
        return exact[0]
    generic = [r for r in rows if r.sub_type_code is None]
    return generic[0] if generic else None


def build_lines(
    incident: FinanceIncident,
    coa_map: FinanceIncidentCoaMap,
    guest_ar: str = GUEST_AR,
    host_ap: str = HOST_AP,
) -> list[dict]:
    """Pure: balanced, counterparty-role-tagged JE lines for an incident. Amounts in functional dec."""
    lines: list[dict] = []

    g = incident.amount_guest_minor or 0
    if g != 0:
        amt = _minor_to_decimal(abs(g))
        revenue = coa_map.guest_coa
        if revenue is None:
            raise ValueError(f"no guest_coa mapped for {incident.type_code}/{incident.sub_type_code}")
        if g > 0:  # guest owes -> receivable
            lines.append({"account_code": guest_ar, "debit": amt, "credit": Decimal("0.00"), "role": "guest"})
            lines.append({"account_code": revenue, "debit": Decimal("0.00"), "credit": amt, "role": "guest"})
        else:      # refund to guest -> reverse
            lines.append({"account_code": revenue, "debit": amt, "credit": Decimal("0.00"), "role": "guest"})
            lines.append({"account_code": guest_ar, "debit": Decimal("0.00"), "credit": amt, "role": "guest"})

    h = incident.amount_host_delta_minor or 0
    if h != 0:
        amt = _minor_to_decimal(abs(h))
        cost = coa_map.host_coa
        if cost is None:
            raise ValueError(f"no host_coa mapped for {incident.type_code}/{incident.sub_type_code}")
        if h > 0:  # host credit -> we owe host
            lines.append({"account_code": cost, "debit": amt, "credit": Decimal("0.00"), "role": "host"})
            lines.append({"account_code": host_ap, "debit": Decimal("0.00"), "credit": amt, "role": "host"})
        else:      # host debit -> host owes us
            lines.append({"account_code": host_ap, "debit": amt, "credit": Decimal("0.00"), "role": "host"})
            lines.append({"account_code": cost, "debit": Decimal("0.00"), "credit": amt, "role": "host"})

    return lines


def lines_balance(lines: list[dict]) -> bool:
    return sum((l["debit"] for l in lines), Decimal("0")) == sum((l["credit"] for l in lines), Decimal("0"))


def resolve_platform_counterparty(db: Session, user_id: str, name_hint: Optional[str] = None) -> FinanceCounterparty:
    """Find (never auto-pollute) the counterparty for an app user id in the platform_user namespace.

    Guest/host are keyed external_system='platform_user' + external_id=<app user id> (POL-112 pattern).
    Creates the row if absent — these are real app users (unlike vendors, which must be finance-approved).
    """
    cp = db.execute(
        select(FinanceCounterparty).where(
            FinanceCounterparty.external_system == PLATFORM_USER_SYSTEM,
            FinanceCounterparty.external_id == str(user_id),
        )
    ).scalar_one_or_none()
    if cp is None:
        cp = FinanceCounterparty(
            name=name_hint or f"platform-user-{user_id}",
            type="customer",
            status="active",
            external_system=PLATFORM_USER_SYSTEM,
            external_id=str(user_id),
        )
        db.add(cp)
        db.flush()
    return cp
