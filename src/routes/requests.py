"""My Requests (Track) route — the raiser's own worklist across types, live from source tables.

GET /api/finance/my-requests?who=<uploaded_by>&user_id=<claims owner id>
"""
from flask import Blueprint, request, jsonify

from src.database import db_session
from src.services import requests_service

requests_bp = Blueprint("requests", __name__, url_prefix="/api/finance")


@requests_bp.route("/my-requests", methods=["GET"])
def my_requests():
    who = request.args.get("who")
    user_id = request.args.get("user_id", type=int)
    with db_session() as db:
        return jsonify(requests_service.my_requests(db, identifier=who, user_id=user_id))
