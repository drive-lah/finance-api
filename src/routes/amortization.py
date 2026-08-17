"""Amortization / depreciation scheduler routes."""
import logging
from datetime import date

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule
from src.services.amortization_service import amortization_service
from src.utils.errors import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

amortization_bp = Blueprint(
    "amortization", __name__, url_prefix="/api/finance/amortization"
)


# ── Policy CRUD ──────────────────────────────────────────────────────────────

@amortization_bp.route("/policies", methods=["GET"])
def list_policies():
    """List all COA amortization/depreciation policies."""
    with db_session() as db:
        policies = (
            db.query(FinanceCOAAmortizationPolicy)
            .order_by(FinanceCOAAmortizationPolicy.asset_account_code)
            .all()
        )
        return jsonify([_policy_dict(p) for p in policies]), 200


@amortization_bp.route("/policies", methods=["POST"])
def create_policy():
    """
    Create a new COA amortization/depreciation policy.

    Body:
      asset_account_code      str  (required)
      accumulated_account_code str (required)
      expense_account_code    str  (required)
      useful_life_months      int  (required)
      policy_type             str  "amortization" | "depreciation"  (default: amortization)
      entity_id               int  (optional — NULL = global)
      notes                   str  (optional)
    """
    data = request.get_json(silent=True) or {}
    required = ["asset_account_code", "accumulated_account_code",
                "expense_account_code", "useful_life_months"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        raise BadRequestError(f"Missing required fields: {', '.join(missing)}")

    useful_life = data["useful_life_months"]
    if not isinstance(useful_life, int) or useful_life < 1:
        raise BadRequestError("useful_life_months must be a positive integer")

    with db_session() as db:
        policy = FinanceCOAAmortizationPolicy(
            asset_account_code=data["asset_account_code"],
            accumulated_account_code=data["accumulated_account_code"],
            expense_account_code=data["expense_account_code"],
            useful_life_months=useful_life,
            policy_type=data.get("policy_type", "amortization"),
            entity_id=data.get("entity_id"),
            notes=data.get("notes"),
            is_active=True,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        return jsonify(_policy_dict(policy)), 201


@amortization_bp.route("/policies/<int:policy_id>", methods=["PATCH"])
def update_policy(policy_id: int):
    """Update a policy (is_active, useful_life_months, notes)."""
    data = request.get_json(silent=True) or {}
    with db_session() as db:
        policy = db.get(FinanceCOAAmortizationPolicy, policy_id)
        if not policy:
            raise NotFoundError(f"Policy {policy_id} not found")
        allowed = ["is_active", "useful_life_months", "notes",
                   "expense_account_code", "accumulated_account_code"]
        for field in allowed:
            if field in data:
                setattr(policy, field, data[field])
        db.commit()
        db.refresh(policy)
        return jsonify(_policy_dict(policy)), 200


# ── Schedules ────────────────────────────────────────────────────────────────

@amortization_bp.route("/schedules", methods=["GET"])
def list_schedules():
    """
    List asset schedules.

    Query params: status (active|completed|cancelled), entity_id
    """
    status = request.args.get("status")
    entity_id = request.args.get("entity_id", type=int)

    with db_session() as db:
        query = db.query(FinanceAssetSchedule)
        if status:
            query = query.filter(FinanceAssetSchedule.status == status)
        if entity_id:
            query = query.filter(FinanceAssetSchedule.entity_id == entity_id)
        schedules = query.order_by(FinanceAssetSchedule.start_date.desc()).all()
        return jsonify([_schedule_dict(s) for s in schedules]), 200


# ── Scheduler run ─────────────────────────────────────────────────────────────

@amortization_bp.route("/run", methods=["POST"])
def run_scheduler():
    """
    Post all due depreciation/amortization JEs for active schedules.

    Body (optional):
      { "as_of_date": "YYYY-MM-DD" }  — defaults to today

    Response 200:
      {
        "as_of_date": "2026-04-01",
        "schedules_checked": 3,
        "months_posted": 5,
        "errors": []
      }
    """
    data = request.get_json(silent=True) or {}
    as_of_date: date | None = None

    raw_date = data.get("as_of_date")
    if raw_date:
        try:
            as_of_date = date.fromisoformat(raw_date)
        except ValueError:
            raise BadRequestError("as_of_date must be YYYY-MM-DD")

    with db_session() as db:
        # DA-13: one engine, every pending pass (adjustments -> assets -> prepaids).
        result = amortization_service.run_all(db, as_of_date=as_of_date)
        return jsonify(result), 200


# ── DA-6 unified view: assets + prepaids in one list ─────────────────────────

@amortization_bp.route("/overview", methods=["GET"])
def overview():
    """One screen, one question (DA-6): what have we paid for that hasn't hit the P&L yet,
    and when will it? Assets show cost + accumulated separately (cost never moves); prepaids
    show one balance draining to zero. Optional ?type=asset|prepaid, ?entity_id=."""
    from sqlalchemy import text as _text
    type_f = request.args.get("type")
    entity_f = request.args.get("entity_id", type=int)
    rows: list[dict] = []
    with db_session() as db:
        if type_f in (None, "", "asset"):
            for r in db.execute(_text("""
                SELECT s.id, s.entity_id, e.name AS entity, s.asset_description AS what,
                       p.asset_account_code, a.name AS account_name, p.policy_type,
                       s.total_amount, s.monthly_amount, s.months_total, s.months_posted,
                       s.start_date, s.status, p.expense_account_code, p.accumulated_account_code
                FROM finance_asset_schedules s
                JOIN finance_coa_amortization_policies p ON p.id = s.policy_id
                LEFT JOIN finance_entities e ON e.id = s.entity_id
                LEFT JOIN finance_accounts a ON a.code = p.asset_account_code AND a.entity_id IS NULL
                WHERE (:ent IS NULL OR s.entity_id = :ent) ORDER BY s.id"""),
                {"ent": entity_f}).mappings():
                # "Released" = what this schedule has CHARGED to the P&L. Sum only the engine's
                # own charges landing on the policy's expense account (2026-08-18 fix): the old
                # query summed EVERY debit line on any journal tagged with the schedule, so a
                # mid-life adjustment — which carries source_schedule_id as its idempotency
                # marker — contributed its bank leg. One asset read S$15,573 released against a
                # S$314 cost, showing a nonsense negative remaining.
                released = round(float(db.execute(_text(
                    "SELECT coalesce(sum(l.debit_amount),0) FROM finance_journal_entries je "
                    "JOIN finance_journal_lines l ON l.entry_id=je.id AND l.debit_amount>0 "
                    "WHERE je.source_schedule_id = :sid AND je.source = 'amortization_scheduler' "
                    "AND l.account_code = :exp"),
                    {"sid": r["id"], "exp": r["expense_account_code"]}).scalar() or 0), 2)
                rows.append({
                    "type": r["policy_type"], "kind": "asset", "id": r["id"],
                    "entity": r["entity"], "what": r["what"],
                    "account": f"{r['asset_account_code']} {r['account_name'] or ''}".strip(),
                    "expense_account": r["expense_account_code"],
                    "contra_account": r["accumulated_account_code"],
                    "cost": float(r["total_amount"]), "released": released,
                    "remaining": round(float(r["total_amount"]) - released, 2),
                    "monthly": float(r["monthly_amount"]),
                    "months_posted": r["months_posted"], "months_total": r["months_total"],
                    "starts": str(r["start_date"]), "status": r["status"]})
        if type_f in (None, "", "prepaid"):
            for r in db.execute(_text("""
                SELECT s.id, s.invoice_id, cp.name AS vendor, i.entity_id, e.name AS entity,
                       s.expense_account_code, a.name AS expense_name, s.prepaid_account_code,
                       s.total_amount, s.monthly_amount, s.months, s.entries_posted, s.start_month
                FROM finance_amortization_schedules s
                LEFT JOIN finance_invoices i ON i.id = s.invoice_id
                LEFT JOIN finance_counterparties cp ON cp.id = i.counterparty_id
                LEFT JOIN finance_entities e ON e.id = i.entity_id
                LEFT JOIN finance_accounts a ON a.code = s.expense_account_code AND a.entity_id IS NULL
                WHERE (:ent IS NULL OR i.entity_id = :ent) ORDER BY s.total_amount DESC"""),
                {"ent": entity_f}).mappings():
                # Same shape as the asset arm above: only the engine's own releases, only on the
                # account this schedule releases into.
                released = float(db.execute(_text(
                    "SELECT coalesce(sum(l.debit_amount),0) FROM finance_journal_entries je "
                    "JOIN finance_journal_lines l ON l.entry_id=je.id AND l.debit_amount>0 "
                    "WHERE je.source_prepaid_schedule_id = :sid AND je.source = 'prepaid_release' "
                    "AND l.account_code = :exp"),
                    {"sid": r["id"], "exp": r["expense_account_code"]}).scalar() or 0)
                released = round(released, 2)
                rows.append({
                    "type": "prepaid", "kind": "prepaid", "id": r["id"],
                    "entity": r["entity"],
                    "what": f"Invoice #{r['invoice_id']}" + (f" — {r['vendor']}" if r["vendor"] else ""),
                    "account": f"{r['prepaid_account_code']} Prepayments",
                    "expense_account": f"{r['expense_account_code']} {r['expense_name'] or ''}".strip(),
                    "contra_account": None,
                    "cost": float(r["total_amount"]), "released": released,
                    "remaining": round(float(r["total_amount"]) - released, 2),
                    "monthly": float(r["monthly_amount"]),
                    "months_posted": r["entries_posted"], "months_total": r["months"],
                    "starts": str(r["start_month"]),
                    "status": "completed" if r["entries_posted"] >= r["months"] else "active"})
    live = [r for r in rows if r["status"] not in ("completed", "disposed")]
    return jsonify({
        "rows": rows,
        "summary": {
            "count": len(rows), "live_count": len(live),
            "cost_total": round(sum(r["cost"] for r in rows), 2),
            "released_total": round(sum(r["released"] for r in rows), 2),
            "remaining_total": round(sum(r["remaining"] for r in live), 2),
            "monthly_run_rate": round(sum(r["monthly"] for r in live), 2),
        },
    }), 200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _policy_dict(p: FinanceCOAAmortizationPolicy) -> dict:
    return {
        "id": p.id,
        "asset_account_code": p.asset_account_code,
        "accumulated_account_code": p.accumulated_account_code,
        "expense_account_code": p.expense_account_code,
        "useful_life_months": p.useful_life_months,
        "policy_type": p.policy_type,
        "method": p.method,
        "entity_id": p.entity_id,
        "is_active": p.is_active,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _schedule_dict(s: FinanceAssetSchedule) -> dict:
    return {
        "id": s.id,
        "policy_id": s.policy_id,
        "transaction_id": s.transaction_id,
        "journal_entry_id": s.journal_entry_id,
        "entity_id": s.entity_id,
        "asset_description": s.asset_description,
        "total_amount": float(s.total_amount),
        "monthly_amount": float(s.monthly_amount),
        "months_total": s.months_total,
        "months_posted": s.months_posted,
        "start_date": s.start_date.isoformat() if s.start_date else None,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
