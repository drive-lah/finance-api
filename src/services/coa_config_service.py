"""COA config service (AW-2) — the finance-owned control surface both money gates read.

Read: list_all — enumerates the WHOLE chart of accounts (finance_accounts) left-joined to config, so
      the Finance Settings grid shows every COA, blank until finance fills it in-app (no seed, no CSV).
      get(coa_code) returns one row.
Write: upsert(coa_code, fields, changed_by) — diffs each editable field against the current row and
       writes one audit row per actual change (old->new, who, when). Idempotent: a no-op edit writes
       no audit rows. history(coa_code) returns the append-only trail newest-first.

Config is entered directly in the UI (Gaurav 2026-08-09) — there is no sheet import.
Gates (door + sign-off) call require_fields()/routing() helpers so the policy lives in one place.
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.account import FinanceAccount
from src.models.coa_config import FinanceCoaConfig, FinanceCoaConfigAudit


def _norm(value) -> Optional[str]:
    """Canonical string form for audit comparison; None/'' both collapse to None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    s = str(value).strip()
    return s or None


def _coerce(field: str, raw):
    """Coerce an incoming JSON value to the column's Python type."""
    if raw is None:
        return None
    if field in ("auto_approve_ok", "needs_trip_id", "needs_intercom_id"):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("true", "1", "y", "yes")
    if field in ("approval_threshold_sgd", "second_approver_above_sgd"):
        if isinstance(raw, (int, float, Decimal)):
            return Decimal(str(raw))
        s = str(raw).strip().replace(",", "")
        if s in ("", "-", "na", "n/a"):
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    # string fields
    s = str(raw).strip()
    return s or None


def list_all(db: Session) -> list[dict]:
    """Every COA in the chart of accounts, left-joined to its config (blank if unconfigured).

    Drives off finance_accounts so the settings grid shows the whole chart ready to fill in-app —
    no pre-seeding. Accounts with no config row come back with null config fields + configured=False.
    """
    accounts = db.execute(select(FinanceAccount)).scalars().all()
    configs = {c.coa_code: c for c in db.execute(select(FinanceCoaConfig)).scalars().all()}
    out = []
    for a in accounts:
        cfg = configs.get(a.code)
        if cfg is not None:
            d = cfg.to_dict()
            d["configured"] = True
        else:
            d = {
                "id": None,
                "coa_code": a.code,
                "approval_threshold_sgd": None,
                "approver_1": None,
                "approver_2": None,
                "second_approver_above_sgd": None,
                "auto_approve_ok": False,
                "needs_trip_id": False,
                "needs_intercom_id": False,
                "other_required": None,
                "notes": None,
                "updated_by": None,
                "updated_at": None,
                "configured": False,
            }
        d["account_name"] = a.name
        acct_type = getattr(a, "account_type", None)
        d["account_type"] = getattr(acct_type, "value", acct_type) or getattr(a, "category", None)
        out.append(d)
    out.sort(key=lambda d: d["coa_code"])
    return out


def onboarded_approvers(db: Session) -> list[dict]:
    """Onboarded employees (hr_employees ⋈ users), excluding offboarded — the approver picklist.

    Onboarded = an hr_employees row exists (POL-112); offboarded = users.employment_end_date set.
    Returns [{email, name}] sorted by name. This is what the config's approver dropdown consumes so
    an approver is always a real, current employee — never free text.
    """
    from sqlalchemy import text
    rows = db.execute(text(
        """
        SELECT u.email AS email, COALESCE(u.name, u.email) AS name
        FROM hr_employees e
        JOIN users u ON u.id = e.user_id
        WHERE e.employment_end_date IS NULL
          AND (u.status IS NULL OR lower(u.status) = 'active')
          AND u.email IS NOT NULL
        ORDER BY name
        """
    )).mappings().all()
    return [{"email": r["email"], "name": r["name"]} for r in rows]


def get(db: Session, coa_code: str) -> Optional[dict]:
    r = db.execute(
        select(FinanceCoaConfig).where(FinanceCoaConfig.coa_code == coa_code)
    ).scalar_one_or_none()
    if r is None:
        return None
    d = r.to_dict()
    name = db.execute(
        select(FinanceAccount.name).where(FinanceAccount.code == coa_code)
    ).scalar_one_or_none()
    d["account_name"] = name
    return d


def upsert(db: Session, coa_code: str, fields: dict, changed_by: Optional[str] = None) -> dict:
    """Create or update the config row for a COA, writing one audit row per changed field.

    Only keys in EDITABLE_FIELDS are considered; unknown keys are ignored. A field present in
    `fields` with an unchanged value writes no audit row. Returns the fresh row dict.
    """
    row = db.execute(
        select(FinanceCoaConfig).where(FinanceCoaConfig.coa_code == coa_code)
    ).scalar_one_or_none()
    created = row is None
    if created:
        row = FinanceCoaConfig(coa_code=coa_code)
        db.add(row)

    # Approver 2 cannot be the same person as approver 1 (Gaurav 2026-08-09) — resolve the effective
    # values (incoming or existing) and reject a clash before writing.
    from src.utils.errors import ConflictError
    eff_a1 = _coerce("approver_1", fields["approver_1"]) if "approver_1" in fields else row.approver_1
    eff_a2 = _coerce("approver_2", fields["approver_2"]) if "approver_2" in fields else row.approver_2
    if eff_a1 and eff_a2 and str(eff_a1).lower() == str(eff_a2).lower():
        raise ConflictError("Approver 2 must be a different person from Approver 1.")

    changes: list[tuple[str, Optional[str], Optional[str]]] = []
    for field in FinanceCoaConfig.EDITABLE_FIELDS:
        if field not in fields:
            continue
        new_val = _coerce(field, fields[field])
        old_val = getattr(row, field)
        if _norm(old_val) == _norm(new_val):
            continue
        setattr(row, field, new_val)
        changes.append((field, _norm(old_val), _norm(new_val)))

    if changes:
        row.updated_by = changed_by
        from datetime import datetime, UTC

        row.updated_at = datetime.now(UTC)
        for field, old_s, new_s in changes:
            db.add(
                FinanceCoaConfigAudit(
                    coa_code=coa_code,
                    field=field,
                    old_value=old_s,
                    new_value=new_s,
                    changed_by=changed_by,
                )
            )

    db.flush()
    db.commit()
    result = get(db, coa_code)
    assert result is not None
    return result


def history(db: Session, coa_code: str) -> list[dict]:
    rows = (
        db.execute(
            select(FinanceCoaConfigAudit)
            .where(FinanceCoaConfigAudit.coa_code == coa_code)
            .order_by(FinanceCoaConfigAudit.changed_at.desc(), FinanceCoaConfigAudit.id.desc())
        )
        .scalars()
        .all()
    )
    return [r.to_dict() for r in rows]


# ---- Gate helpers: single home for the door + sign-off policy ----------------------------------

def require_fields(db: Session, coa_code: str) -> dict:
    """DOOR gate: which anchors a COA demands. Returns {'trip_id','intercom_id','other'}.

    Absent config = nothing required (a COA with no row is un-gated). This is what the upload/raise
    validator consults before creating a draft.
    """
    r = db.execute(
        select(FinanceCoaConfig).where(FinanceCoaConfig.coa_code == coa_code)
    ).scalar_one_or_none()
    if r is None:
        return {"trip_id": False, "intercom_id": False, "other": None}
    return {
        "trip_id": r.needs_trip_id,
        "intercom_id": r.needs_intercom_id,
        "other": r.other_required,
    }


def routing(db: Session, coa_code: str, amount_sgd: Optional[Decimal] = None) -> dict:
    """SIGN-OFF gate: how many approvals + who, for this COA at this amount.

    Returns {'steps': 0|1|2, 'approver_1', 'approver_2', 'auto': bool}. Absent config falls back to
    a single-approval requirement (conservative: never auto-approve an unconfigured COA).
    """
    r = db.execute(
        select(FinanceCoaConfig).where(FinanceCoaConfig.coa_code == coa_code)
    ).scalar_one_or_none()
    if r is None:
        return {"steps": 1, "approver_1": None, "approver_2": None, "auto": False}

    amt = Decimal(str(amount_sgd)) if amount_sgd is not None else None

    # Below the auto threshold with auto allowed -> no sign-off.
    if r.auto_approve_ok and r.approval_threshold_sgd is not None and amt is not None:
        if amt < r.approval_threshold_sgd:
            return {"steps": 0, "approver_1": None, "approver_2": None, "auto": True}

    steps = 1 if r.approver_1 else 0
    if r.approver_2 and r.second_approver_above_sgd is not None and amt is not None:
        if amt >= r.second_approver_above_sgd:
            steps = 2
    elif r.approver_2 and r.second_approver_above_sgd is None:
        # A second approver with no threshold means "always two steps".
        steps = 2
    return {
        "steps": steps,
        "approver_1": r.approver_1,
        "approver_2": r.approver_2,
        "auto": False,
    }
