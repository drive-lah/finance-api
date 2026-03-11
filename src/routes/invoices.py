"""Invoice routes for the Accounts Payable system."""
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.schemas import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from src.services.invoice_service import invoice_service
from src.utils.errors import NotFoundError, ConflictError

logger = logging.getLogger(__name__)

invoices_bp = Blueprint("invoices", __name__, url_prefix="/api/finance/invoices")


@invoices_bp.route("", methods=["GET"])
def list_invoices():
    """List invoices with optional filtering by entity_id, status, counterparty_id."""
    entity_id = request.args.get("entity_id", type=int)
    status = request.args.get("status", type=str)
    counterparty_id = request.args.get("counterparty_id", type=int)

    with db_session() as db:
        invoices = invoice_service.get_all(
            db, entity_id=entity_id, status=status, counterparty_id=counterparty_id,
        )
        return jsonify([InvoiceResponse.model_validate(inv).model_dump() for inv in invoices]), 200


@invoices_bp.route("", methods=["POST"])
def create_invoice():
    """Create a new invoice."""
    data = request.get_json()
    invoice_data = InvoiceCreate(**data)

    with db_session() as db:
        invoice = invoice_service.create(db, invoice_data)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 201


@invoices_bp.route("/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id: int):
    """Get an invoice by ID."""
    with db_session() as db:
        invoice = invoice_service.get_by_id(db, invoice_id)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 200


@invoices_bp.route("/<int:invoice_id>", methods=["PUT"])
def update_invoice(invoice_id: int):
    """Update an invoice (draft/pending_approval only)."""
    data = request.get_json()
    update_data = InvoiceUpdate(**data)

    with db_session() as db:
        invoice = invoice_service.update(db, invoice_id, update_data)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 200


@invoices_bp.route("/<int:invoice_id>/approve", methods=["POST"])
def approve_invoice(invoice_id: int):
    """Approve an invoice, creating the AP journal entry."""
    data = request.get_json() or {}
    approved_by = data.get("approved_by")
    if not approved_by:
        return jsonify({"error": "approved_by is required"}), 400

    with db_session() as db:
        invoice = invoice_service.approve(db, invoice_id, approved_by)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 200


@invoices_bp.route("/<int:invoice_id>/reject", methods=["POST"])
def reject_invoice(invoice_id: int):
    """Reject an invoice with a reason."""
    data = request.get_json() or {}
    rejection_reason = data.get("rejection_reason")
    if not rejection_reason:
        return jsonify({"error": "rejection_reason is required"}), 400

    with db_session() as db:
        invoice = invoice_service.reject(db, invoice_id, rejection_reason)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 200


@invoices_bp.route("/<int:invoice_id>/void", methods=["POST"])
def void_invoice(invoice_id: int):
    """Void an invoice."""
    with db_session() as db:
        invoice = invoice_service.void(db, invoice_id)
        return jsonify(InvoiceResponse.model_validate(invoice).model_dump()), 200


@invoices_bp.route('/extract', methods=['POST'])
def extract_invoice():
    """
    Extract structured data from an uploaded invoice PDF using AI.

    Accepts multipart/form-data with a 'file' field (PDF).
    Returns extracted fields for review before creating the invoice.
    Does NOT create an invoice -- just extracts data for the frontend to display.

    Request: multipart/form-data, field 'file' = PDF
    Response 200: extracted invoice data dict
    Response 400: no file / not PDF
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "File must be a PDF"}), 400

    pdf_bytes = file.read()

    from src.services.ai_extraction_service import ai_extraction_service
    from src.services.s3_service import s3_service

    result = ai_extraction_service.extract_invoice_data(pdf_bytes)

    # Upload to S3 (best-effort — failure does not block extraction)
    s3_key = s3_service.upload_invoice_pdf(pdf_bytes, filename=file.filename or "invoice.pdf")
    result["pdf_s3_key"] = s3_key

    return jsonify(result), 200
