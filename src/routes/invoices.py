"""Invoice routes for the Accounts Payable system."""
import hashlib
import logging

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.schemas import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from src.services.invoice_service import invoice_service, _invoice_dict
from src.utils.errors import NotFoundError, ConflictError

logger = logging.getLogger(__name__)

invoices_bp = Blueprint("invoices", __name__, url_prefix="/api/finance/invoices")


@invoices_bp.route("/matches", methods=["GET"])
def list_matches():
    """Invoice↔payment matches (provisional/logged). Filter by counterparty_id."""
    from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
    from src.models.invoice import FinanceInvoice
    counterparty_id = request.args.get("counterparty_id", type=int)
    with db_session() as db:
        q = db.query(FinanceInvoicePaymentMatch)
        if counterparty_id is not None:
            inv_ids = (db.query(FinanceInvoice.id)
                       .filter(FinanceInvoice.counterparty_id == counterparty_id).subquery())
            q = q.filter(FinanceInvoicePaymentMatch.invoice_id.in_(inv_ids))
        return jsonify([m.to_dict() for m in q.all()])


@invoices_bp.route("/matches", methods=["POST"])
def create_match():
    """Create a PROVISIONAL invoice↔payment match (manual). Enforces 1 active match per txn+invoice."""
    from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
    from src.models.invoice import FinanceInvoice, InvoiceStatus
    body = request.get_json(force=True) or {}
    invoice_id = body.get("invoice_id"); transaction_id = body.get("transaction_id")
    if not invoice_id or not transaction_id:
        raise ConflictError("invoice_id and transaction_id are required")
    with db_session() as db:
        # a payment or an invoice already in a match can't be re-matched provisionally
        clash = (db.query(FinanceInvoicePaymentMatch)
                 .filter((FinanceInvoicePaymentMatch.transaction_id == transaction_id) |
                         (FinanceInvoicePaymentMatch.invoice_id == invoice_id)).first())
        if clash:
            raise ConflictError(
                f"already matched (invoice {clash.invoice_id} ↔ txn {clash.transaction_id}); detach first")
        m = FinanceInvoicePaymentMatch(
            invoice_id=int(invoice_id), transaction_id=int(transaction_id),
            state="provisional", source="manual", created_by=body.get("created_by") or "ui")
        db.add(m); db.flush()
        # State machine (POL-107): provisionally pairing a reconcile-arm invoice advances it
        # reconcile → paired (awaiting posting authorization; the team never posts).
        inv = db.get(FinanceInvoice, int(invoice_id))
        if inv is not None and inv.status == InvoiceStatus.RECONCILE.value:
            inv.status = InvoiceStatus.PAIRED.value
        return jsonify(m.to_dict()), 201


@invoices_bp.route("/matches/<int:match_id>", methods=["DELETE"])
def delete_match(match_id):
    """Detach (break) a match. Provisional → just delete. Logged → blocked (void the JE first)."""
    from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
    from src.models.invoice import FinanceInvoice, InvoiceStatus
    with db_session() as db:
        m = db.get(FinanceInvoicePaymentMatch, match_id)
        if not m:
            raise NotFoundError(f"match {match_id} not found")
        if m.state == "logged":
            raise ConflictError("match is logged — void the journal entry before detaching")
        inv_id = m.invoice_id
        db.delete(m); db.flush()
        # State machine (POL-107): unpairing the last provisional match sends a paired
        # invoice back to reconcile (it is no longer matched).
        inv = db.get(FinanceInvoice, inv_id)
        if inv is not None and inv.status == InvoiceStatus.PAIRED.value:
            remaining = (db.query(FinanceInvoicePaymentMatch)
                         .filter(FinanceInvoicePaymentMatch.invoice_id == inv_id).count())
            if remaining == 0:
                inv.status = InvoiceStatus.RECONCILE.value
        return jsonify({"deleted": match_id})


@invoices_bp.route("", methods=["GET"])
def list_invoices():
    """List invoices with server-side filtering + pagination (limit/offset, X-Total-Count header)."""
    filters = dict(
        entity_id=request.args.get("entity_id", type=int),
        status=request.args.get("status", type=str),
        counterparty_id=request.args.get("counterparty_id", type=int),
        search=request.args.get("search", type=str),
        vendor_flag=request.args.get("vendor_flag", type=str),
        coa_flag=request.args.get("coa_flag", type=str),
        document_gate=request.args.get("document_gate", type=str),
        currency_flag=request.args.get("currency_flag", type=str),
        retool_status=request.args.get("retool_status", type=str),
        sub_category=request.args.get("sub_category", type=str),
        amount_match=request.args.get("amount_match", type=str),
        provisional_paid=request.args.get("provisional_paid", type=str),
        retool_id=request.args.get("retool_id", type=str),
        is_duplicate=request.args.get("is_duplicate", type=str),
        amount_min=request.args.get("amount_min", type=float),
        amount_max=request.args.get("amount_max", type=float),
        paired=request.args.get("paired", type=str),
    )
    limit = min(request.args.get("limit", default=100, type=int), 500)
    offset = request.args.get("offset", default=0, type=int)

    with db_session() as db:
        invoices = invoice_service.get_all(db, limit=limit, offset=offset, **filters)
        total = invoice_service.count_all(db, **filters)
        resp = jsonify([_invoice_dict(inv, db) for inv in invoices])
        resp.headers["X-Total-Count"] = str(total)
        return resp, 200


@invoices_bp.route("", methods=["POST"])
def create_invoice():
    """Create a new invoice."""
    data = request.get_json()
    invoice_data = InvoiceCreate(**data)

    with db_session() as db:
        invoice = invoice_service.create(db, invoice_data)
        return jsonify(_invoice_dict(invoice, db)), 201


@invoices_bp.route("/raise", methods=["POST"])
def raise_invoice():
    """Flow 2 — raise a vendor invoice: gate COA anchors, create draft, store anchors, submit."""
    data = request.get_json() or {}
    with db_session() as db:
        try:
            return jsonify(invoice_service.raise_invoice(db, data)), 201
        except ConflictError as e:
            return jsonify({"error": str(e)}), 400


@invoices_bp.route("/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id: int):
    """Get an invoice by ID."""
    with db_session() as db:
        invoice = invoice_service.get_by_id(db, invoice_id)
        return jsonify(_invoice_dict(invoice, db)), 200


@invoices_bp.route("/<int:invoice_id>", methods=["PUT"])
def update_invoice(invoice_id: int):
    """Update an invoice (draft/pending_approval only)."""
    data = request.get_json()
    update_data = InvoiceUpdate(**data)

    with db_session() as db:
        invoice = invoice_service.update(db, invoice_id, update_data)
        return jsonify(_invoice_dict(invoice, db)), 200


@invoices_bp.route("/<int:invoice_id>/attach", methods=["POST"])
def attach_invoice_document(invoice_id: int):
    """Attach a document to an EXISTING invoice (fills a no-document stub in place).
    Multipart upload: field 'file'. Extracts + backfills + dedups the same row."""
    import os
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in {'.pdf', '.jpg', '.jpeg', '.png'}:
        return jsonify({"error": f"Unsupported file type '{ext}'"}), 400
    _MAX_BYTES = 20 * 1024 * 1024
    file_bytes = file.read(_MAX_BYTES + 1)  # one over the cap to detect overflow
    if len(file_bytes) > _MAX_BYTES:
        return jsonify({"error": "File too large (max 20 MB)"}), 413
    with db_session() as db:
        invoice, verdict = invoice_service.attach_document(db, invoice_id, file_bytes, filename=file.filename)
        return jsonify({"invoice": _invoice_dict(invoice, db), "duplicate_check": verdict.as_dict()}), 200


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
        return jsonify(_invoice_dict(invoice, db)), 200


@invoices_bp.route("/<int:invoice_id>/reject", methods=["POST"])
def reject_invoice(invoice_id: int):
    """Reject an invoice with a reason."""
    data = request.get_json() or {}
    rejection_reason = data.get("rejection_reason")
    if not rejection_reason:
        return jsonify({"error": "rejection_reason is required"}), 400

    with db_session() as db:
        invoice = invoice_service.reject(db, invoice_id, rejection_reason,
                                         rejected_by=data.get("rejected_by"))
        return jsonify(_invoice_dict(invoice, db)), 200


@invoices_bp.route("/<int:invoice_id>/void", methods=["POST"])
def void_invoice(invoice_id: int):
    """Void an invoice. Body: { void_reason: str (required), voided_by: str (logged-in user) }."""
    data = request.get_json() or {}
    void_reason = data.get("void_reason")
    if not void_reason:
        return jsonify({"error": "void_reason is required"}), 400
    with db_session() as db:
        invoice = invoice_service.void(db, invoice_id,
                                       voided_by=data.get("voided_by"), void_reason=void_reason)
        return jsonify(_invoice_dict(invoice, db)), 200


# ── Pay Queue (POL-111) ──────────────────────────────────────────────────────
@invoices_bp.route("/pay-queue", methods=["GET"])
def get_pay_queue():
    """The approved-payables queue — invoices awaiting payout, sorted by manual priority then
    FIFO by approval time. Optional ?entity_id=."""
    entity_id = request.args.get("entity_id", type=int)
    with db_session() as db:
        rows = invoice_service.pay_queue(db, entity_id=entity_id)
        return jsonify({"items": [_invoice_dict(inv, db) for inv in rows], "count": len(rows)}), 200


@invoices_bp.route("/pay-queue/reorder", methods=["POST"])
def reorder_pay_queue():
    """Apply a manual drag-reorder. Body: { ordered_invoice_ids: [int], moved_by: str }.
    Writes pay_priority = 1..N in the given order and logs each moved invoice to the trail."""
    data = request.get_json() or {}
    ids = data.get("ordered_invoice_ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ordered_invoice_ids (non-empty list) is required"}), 400
    with db_session() as db:
        rows = invoice_service.reorder_pay_queue(db, ids, moved_by=data.get("moved_by"))
        return jsonify({"items": [_invoice_dict(inv, db) for inv in rows], "count": len(rows)}), 200


@invoices_bp.route("/pay-queue/moves", methods=["GET"])
def get_pay_queue_moves():
    """The append-only reorder audit trail. Optional ?invoice_id= to scope to one invoice."""
    from src.models.pay_queue_move import FinancePayQueueMove
    invoice_id = request.args.get("invoice_id", type=int)
    with db_session() as db:
        q = db.query(FinancePayQueueMove)
        if invoice_id is not None:
            q = q.filter(FinancePayQueueMove.invoice_id == invoice_id)
        moves = q.order_by(FinancePayQueueMove.moved_at.desc()).limit(500).all()
        return jsonify({"items": [m.to_dict() for m in moves], "count": len(moves)}), 200


@invoices_bp.route("/<int:invoice_id>/pay", methods=["POST"])
def pay_invoice(invoice_id: int):
    """Pay an approved invoice by pairing an outgoing bank transaction to it (approval-arm
    payment). Books the AP payment JE (Dr 2000 AP / Cr bank), records the payment, and flips
    the invoice approved → paid / partially_paid. Body: { transaction_id: int (required),
    matched_by: str }."""
    data = request.get_json() or {}
    transaction_id = data.get("transaction_id")
    if not transaction_id:
        return jsonify({"error": "transaction_id is required"}), 400
    with db_session() as db:
        result = invoice_service.match_transaction(
            db, invoice_id, int(transaction_id), matched_by=data.get("matched_by") or "manual"
        )
        return jsonify(result), 200


@invoices_bp.route("/<int:invoice_id>/submit", methods=["POST"])
def submit_invoice(invoice_id: int):
    """
    Submit a draft invoice for approval.

    Evaluates approval rules to determine routing:
    - Validates required fields are present
    - Applies override rules (new_vendor, ai/unset COA) → pending_approval
    - Evaluates approval rules: auto_approve or require_approval
    - Defaults to pending_approval if no rule matches

    Body: {} (empty)

    Response 200:
      {
        "status": "pending_approval" | "approved",
        "message": "Human-readable explanation",
        "invoice": { ...InvoiceResponse... }
      }
    """
    data = request.get_json() or {}

    with db_session() as db:
        result = invoice_service.submit(db, invoice_id, confirmed=False,
                                        submitted_by=data.get("submitted_by"),
                                        override_reason=data.get("override_reason"))
        return jsonify(result), 200


@invoices_bp.route('/extract', methods=['POST'])
def extract_invoice():
    """
    Extract structured data from an uploaded invoice PDF or image using AI.

    - Checks file hash against existing invoices (blocks exact duplicates)
    - Passes entity names to AI for Bill-To matching
    - Uploads file to S3 (best-effort)
    - Returns extracted fields + pdf_s3_key + pdf_content_hash for frontend review

    Request: multipart/form-data, field 'file' = PDF or image (JPEG, PNG)
    Response 200: extracted invoice data dict
    Response 409: duplicate file already exists in system
    Response 400: no file / unsupported file type
    """
    import os

    logger.info(f"Invoice extract request received. Files: {request.files.keys()}")

    if 'file' not in request.files:
        logger.error("No 'file' field in request")
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    logger.info(f"File received: filename={file.filename}, content_type={file.content_type}")

    ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
    ext = os.path.splitext(file.filename.lower())[1] if file.filename else ''
    logger.info(f"File extension extracted: '{ext}' from filename '{file.filename}'")

    if not ext or ext not in ALLOWED_EXTENSIONS:
        logger.error(f"Invalid extension. ext='{ext}', allowed={ALLOWED_EXTENSIONS}")
        return jsonify({"error": "File must be a PDF or image (JPEG, PNG)"}), 400

    _MAX_BYTES = 20 * 1024 * 1024
    file_bytes = file.read(_MAX_BYTES + 1)  # one over the cap to detect overflow
    if len(file_bytes) > _MAX_BYTES:
        return jsonify({"error": "File too large (max 20 MB)"}), 413
    logger.info(f"File read successfully. Size: {len(file_bytes)} bytes")

    # --- Duplicate detection: ADVISORY only (Gaurav 2026-08-01) ---
    # Duplicates are allowed at draft, flagged, and blocked at promotion — so we no
    # longer 409 here. The same-file hint is surfaced (result["duplicate_check"] below,
    # + this exact-file note) so the reviewer can proceed knowingly.
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    exact_dup = None
    with db_session() as db:
        existing = invoice_service.find_by_pdf_hash(db, file_hash)
        if existing and existing.status != 'void':
            exact_dup = {"duplicate_of": existing.id, "level": "hash",
                         "detail": f"Identical file already on invoice #{existing.invoice_number or existing.id} ({existing.status})."}

    from src.services.ai_extraction_service import ai_extraction_service
    from src.services.s3_service import s3_service

    # Load entity names so AI can match Bill-To
    with db_session() as db:
        from src.models.entity import FinanceEntity
        entities = db.query(FinanceEntity).filter(FinanceEntity.status == "active").all()
        entity_names = [e.name for e in entities]

    logger.info(f"Calling AI extraction service with file_extension={ext}...")
    result = ai_extraction_service.extract_invoice_data(file_bytes, entity_names=entity_names, file_extension=ext)
    logger.info(f"AI extraction complete. Result keys: {list(result.keys())}, extraction_error: {result.get('extraction_error')}")

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

    # Advisory duplicate check (post-extraction): the identical-file case already 409'd
    # above; this surfaces the SEMANTIC signal (same vendor + invoice number, or same
    # vendor + amount + date without a number) so the reviewer sees it before saving.
    try:
        from src.services.duplicate_detection_service import duplicate_detection_service
        from datetime import date as _date
        raw_dt = result.get("invoice_date")
        inv_dt = _date.fromisoformat(raw_dt) if isinstance(raw_dt, str) and len(raw_dt) >= 10 else None
        with db_session() as db:
            verdict = duplicate_detection_service.detect(
                db,
                entity_id=result.get("entity_id"),
                counterparty_id=vendor_match["counterparty_id"],
                invoice_number=result.get("invoice_number"),
                total_amount=result.get("total_amount"),
                invoice_date=inv_dt,
                currency=result.get("currency"),
                pdf_content_hash=file_hash,
            )
        result["duplicate_check"] = verdict.as_dict()
    except Exception as e:
        logger.warning(f"Duplicate advisory check failed (non-fatal): {e}")
        result["duplicate_check"] = None
    result["exact_file_match"] = exact_dup   # identical-file hint (advisory, never blocks)

    # Upload to S3 (best-effort)
    logger.info("Starting S3 upload...")
    s3_key = s3_service.upload_invoice_pdf(file_bytes, filename=file.filename or "invoice.pdf")
    result["pdf_s3_key"] = s3_key
    result["pdf_content_hash"] = file_hash
    logger.info(f"S3 upload complete. s3_key={s3_key}")

    logger.info(f"Returning extraction result. Keys: {list(result.keys())}")
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
            "invoices": [_invoice_dict(inv, db) for inv in invoices],
        }), 200


@invoices_bp.route("/<int:invoice_id>/download", methods=["GET"])
def download_invoice(invoice_id: int):
    """
    Download the uploaded invoice file (PDF or image).

    Returns a pre-signed URL for the S3 object if available.
    If S3 is not configured, returns a 404.
    """
    with db_session() as db:
        invoice = invoice_service.get_by_id(db, invoice_id)
        if not invoice.pdf_s3_key:
            return jsonify({"error": "No file attached to this invoice"}), 404

        from src.services.s3_service import s3_service
        presigned_url = s3_service.get_presigned_url(invoice.pdf_s3_key, expiration_seconds=3600)

        if not presigned_url:
            return jsonify({"error": "Cannot generate download link — S3 not configured"}), 503

        return jsonify({
            "download_url": presigned_url,
            "invoice_id": invoice_id,
            "file_key": invoice.pdf_s3_key,
        }), 200
