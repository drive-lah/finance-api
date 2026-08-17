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

    include_prepaids = data.get("include_prepaids", True)
    with db_session() as db:
        result = amortization_service.run(db, as_of_date=as_of_date)
        if include_prepaids:
            # Same scheduled-postings family (Gaurav 2026-08-17): assets age, prepaids release.
            result["prepaids"] = amortization_service.run_prepaids(db, as_of_date=as_of_date)
        return jsonify(result), 200


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
