"""Enrichment routes — real-time resolution of operational anchors for the ratify form.

GET /api/finance/enrichment/validate?trip_id=TA…&ticket_ids=123,456
    → { trip: {found,label,…}, tickets: [{ticket,found,label}, …] }

Read-only ClickHouse lookups (the shared enrichment_service). Used by the upload/ratify UI to catch a
bad trip / ticket at the door instead of surfacing it three steps later on the approver's card.
"""
import logging

from flask import Blueprint, request, jsonify

from src.services import enrichment_service

logger = logging.getLogger(__name__)

enrichment_bp = Blueprint("enrichment", __name__, url_prefix="/api/finance/enrichment")


@enrichment_bp.route("/validate", methods=["GET"])
def validate():
    trip_id = request.args.get("trip_id")
    ticket_ids = request.args.get("ticket_ids")
    rego = request.args.get("rego")
    try:
        return jsonify(enrichment_service.validate_anchors(trip_id=trip_id, ticket_ids=ticket_ids, rego=rego))
    except Exception as e:  # never fail the form on a lookup hiccup
        logger.warning("enrichment.validate failed: %s", e)
        return jsonify({"error": "resolution unavailable"}), 200
