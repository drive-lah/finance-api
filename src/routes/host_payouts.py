"""Host / guest payout paid-status lookup routes (POL-110 — Requests → Track data).

GET /api/finance/host-payouts?q=<term>&market=au|sg|both&limit=N
Returns each matching payout line with a paid vs not-paid flag. `payoutStatus='paid'` is the
source of truth (no Stripe/bank reconciliation).
"""
from flask import Blueprint, request, jsonify

from src.services.host_payout_service import host_payout_service

host_payouts_bp = Blueprint("host_payouts", __name__, url_prefix="/api/finance/host-payouts")


@host_payouts_bp.route("", methods=["GET"])
def lookup():
    q = request.args.get("q", "")
    market = request.args.get("market", "both")
    limit = request.args.get("limit", default=100, type=int)
    return jsonify(host_payout_service.lookup(q, market, limit))
