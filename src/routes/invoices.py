"""Invoice routes for the Accounts Payable system."""
import hashlib
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
    """Approve an invoice, creating the AP journal entry.

    Body:
      approved_by: str  (required)
      contra_account_code: str  (optional — approver can confirm/change the COA)
    """
    data = request.get_json() or {}
    approved_by = data.get("approved_by")
    if not approved_by:
        return jsonify({"error": "approved_by is required"}), 400
    contra_account_code = data.get("contra_account_code") or None

    with db_session() as db:
        invoice = invoice_service.approve(db, invoice_id, approved_by, contra_account_code=contra_account_code)
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


@invoices_bp.route("/<int:invoice_id>/submit", methods=["POST"])
def submit_invoice(invoice_id: int):
    """
    Submit a draft invoice for approval.

    Runs the AI contract review gate:
    - Validates required fields are present
    - Compares invoice against known contract (if any)
    - Returns assessment: pass | flag | no_contract

    Body (optional):
      { "confirmed": true }  — human override after seeing a flag

    Response 200:
      {
        "assessment": "pass" | "flag" | "no_contract",
        "message": "Human-readable explanation",
        "concerns": ["..."],           # only when assessment = flag
        "invoice": { ...InvoiceResponse... }  # populated when status changed
      }
    """
    data = request.get_json() or {}
    confirmed = bool(data.get("confirmed", False))

    with db_session() as db:
        result = invoice_service.submit(db, invoice_id, confirmed=confirmed)
        return jsonify(result), 200


@invoices_bp.route('/extract', methods=['POST'])
def extract_invoice():
    """
    Extract structured data from an uploaded invoice PDF using AI.

    - Checks PDF hash against existing invoices (blocks exact duplicates)
    - Passes entity names to AI for Bill-To matching
    - Uploads PDF to S3 (best-effort)
    - Returns extracted fields + pdf_s3_key + pdf_content_hash for frontend review

    Request: multipart/form-data, field 'file' = PDF
    Response 200: extracted invoice data dict
    Response 409: duplicate PDF already exists in system
    Response 400: no file / not PDF
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "File must be a PDF"}), 400

    pdf_bytes = file.read()

    # --- Duplicate detection: exact PDF hash check ---
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    with db_session() as db:
        existing = invoice_service.find_by_pdf_hash(db, pdf_hash)
        if existing:
            return jsonify({
                "error": "Duplicate invoice",
                "detail": f"This PDF has already been uploaded (Invoice #{existing.invoice_number or existing.id}, status: {existing.status}).",
                "existing_invoice_id": existing.id,
            }), 409

    from src.services.ai_extraction_service import ai_extraction_service
    from src.services.s3_service import s3_service

    # Load entity names so AI can match Bill-To
    with db_session() as db:
        from src.models.entity import FinanceEntity
        entities = db.query(FinanceEntity).filter(FinanceEntity.status == "active").all()
        entity_names = [e.name for e in entities]

    result = ai_extraction_service.extract_invoice_data(pdf_bytes, entity_names=entity_names)

    # Vendor matching — find or prepare auto-create
    vendor_match = {"counterparty_id": None, "counterparty_name": None,
                    "is_new_vendor": False, "match_confidence": 0.0}
    if result.get("vendor_name"):
        from src.services.vendor_matching_service import vendor_matching_service
        with db_session() as db:
            cp, is_new, confidence = vendor_matching_service.match_or_create(
                db, result["vendor_name"], result.get("vendor_tax_id")
            )
            if cp:
                vendor_match = {
                    "counterparty_id": cp.id,
                    "counterparty_name": cp.name,
                    "is_new_vendor": is_new,
                    "match_confidence": round(confidence, 2),
                }
    result["vendor_match"] = vendor_match

    # Upload to S3 (best-effort)
    s3_key = s3_service.upload_invoice_pdf(pdf_bytes, filename=file.filename or "invoice.pdf")
    result["pdf_s3_key"] = s3_key
    result["pdf_content_hash"] = pdf_hash

    return jsonify(result), 200


@invoices_bp.route("/<int:invoice_id>/match-transaction", methods=["POST"])
def match_transaction(invoice_id: int):
    """
    Manually match an open invoice against a bank transaction.

    Body: { "transaction_id": <int>, "matched_by": "<username>" }

    Creates the AP payment JE, updates invoice.amount_paid / status,
    and marks the transaction as MATCHED.
    """
    body = request.get_json(silent=True) or {}
    transaction_id = body.get("transaction_id")
    matched_by = body.get("matched_by", "manual")

    if not transaction_id:
        return jsonify({"error": "transaction_id is required"}), 400

    try:
        with db_session() as db:
            result = invoice_service.match_transaction(
                db, invoice_id, transaction_id, matched_by=matched_by
            )
            return jsonify(result), 200
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        from src.utils.errors import BadRequestError
        if isinstance(e, BadRequestError):
            return jsonify({"error": str(e)}), 400
        logger.error(f"Manual match error: {e}", exc_info=True)
        return jsonify({"error": "Internal error during manual match"}), 500


@invoices_bp.route("/open-for-transaction/<int:transaction_id>", methods=["GET"])
def list_open_for_transaction(transaction_id: int):
    """
    List open invoices that could be manually matched against a transaction.

    Returns invoices for the transaction's counterparty, same currency,
    dated on or before the transaction date — same eligibility rules as auto-match.
    """
    from src.models.transaction import FinanceTransaction

    with db_session() as db:
        txn = db.get(FinanceTransaction, transaction_id)
        if not txn:
            return jsonify({"error": f"Transaction {transaction_id} not found"}), 404

        if not txn.counterparty_id:
            return jsonify({"invoices": [], "note": "Transaction has no linked counterparty"}), 200

        invoices = invoice_service.get_open_for_match(
            db,
            counterparty_id=txn.counterparty_id,
            currency=txn.currency,
            transaction_date=txn.transaction_date,
        )
        return jsonify({
            "transaction_id": transaction_id,
            "counterparty_id": txn.counterparty_id,
            "invoices": [InvoiceResponse.model_validate(inv).model_dump() for inv in invoices],
        }), 200
