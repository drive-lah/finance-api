"""Bank account routes."""
import logging
from datetime import date, timedelta

from flask import Blueprint, request, jsonify

from src.database import db_session
from src.models.bank_account import FinanceBankAccount
from src.services.bank_account_service import bank_account_service
from src.services.transaction_service import transaction_service
from src.services.categorization_service import categorization_service
from src.services.wise_service import wise_service
from src.services.csv_adapters.registry import ADAPTER_REGISTRY, ADAPTER_META, get_adapter
from src.models.schemas import BankAccountCreate, BankAccountResponse
from src.utils.errors import NotFoundError, ConflictError

_WISE_DEFAULT_HISTORY_DAYS = 90

logger = logging.getLogger(__name__)

bank_accounts_bp = Blueprint('bank_accounts', __name__, url_prefix='/api/finance/bank-accounts')


@bank_accounts_bp.route('', methods=['GET'])
def list_bank_accounts():
    """
    List all bank accounts, optionally filtered by entity_id.

    Query Parameters:
        entity_id (optional): Filter by entity ID

    Returns:
        200: List of bank accounts
        400: Invalid query parameters
        500: Server error
    """
    # Get optional entity_id filter
    entity_id = request.args.get('entity_id', type=int)

    with db_session() as db:
        bank_accounts = bank_account_service.get_all(db, entity_id=entity_id)
        response_data = [BankAccountResponse.model_validate(ba).model_dump() for ba in bank_accounts]
        return jsonify(response_data), 200


@bank_accounts_bp.route('', methods=['POST'])
def create_bank_account():
    """
    Create a new bank account.

    Request Body:
        entity_id: Entity ID (required)
        bank_name: Bank name (required)
        account_number: Account number (required)
        account_name: Account name (required)
        currency: ISO 4217 currency code (required)
        status: Account status (optional, defaults to ACTIVE)

    Returns:
        201: Created bank account
        400: Validation error or invalid entity_id
        500: Server error
    """
    # Parse and validate request data (Pydantic will raise ValidationError on invalid data)
    data = request.get_json()
    bank_account_data = BankAccountCreate(**data)

    with db_session() as db:
        try:
            bank_account = bank_account_service.create(db, bank_account_data)
        except ValueError as e:
            # Service layer raises ValueError for business logic errors (e.g., invalid entity)
            raise ConflictError(str(e))

        response_data = BankAccountResponse.model_validate(bank_account).model_dump()
        return jsonify(response_data), 201


@bank_accounts_bp.route('/<int:bank_account_id>', methods=['GET'])
def get_bank_account(bank_account_id: int):
    """
    Get a bank account by ID.

    Path Parameters:
        bank_account_id: Bank account ID

    Returns:
        200: Bank account details
        404: Bank account not found
        500: Server error
    """
    with db_session() as db:
        bank_account = bank_account_service.get_by_id(db, bank_account_id)

        if not bank_account:
            raise NotFoundError(f"Bank account with ID {bank_account_id} not found")

        response_data = BankAccountResponse.model_validate(bank_account).model_dump()
        return jsonify(response_data), 200


@bank_accounts_bp.route('/file-adapters', methods=['GET'])
def list_file_adapters():
    """
    List all registered file import adapters with their labels and accepted file types.

    Used by the frontend to populate the file_adapter dropdown in the
    bank account create/edit form and set the file input's accept attribute.

    Returns:
        200: List of { key, label, accepts } objects (unique adapters only).
    """
    seen = set()
    adapters = []
    for key, meta in ADAPTER_META.items():
        label = meta["label"]
        if label not in seen:
            seen.add(label)
            adapters.append({
                "key": key,
                "label": label,
                "accepts": meta["accepts"],
            })
    return jsonify(adapters), 200


@bank_accounts_bp.route('/wise/profiles', methods=['GET'])
def wise_list_profiles():
    """
    List all Wise business profiles available under the configured API key.

    Returns profile_id and name for each business profile so the caller
    can choose which profile to connect to an entity.

    Returns:
        200: List of { profile_id, name } objects.
        400: Wise API error (e.g. bad key).
    """
    try:
        profiles = wise_service.get_business_profiles()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify([
        {"profile_id": p["id"], "name": p.get("businessName") or p.get("fullName", "")}
        for p in profiles
    ]), 200


@bank_accounts_bp.route('/wise/connect', methods=['POST'])
def wise_connect():
    """
    Connect a Wise profile to an entity, auto-creating one bank account per currency balance.

    Idempotent: if a bank account for a balance already exists under the entity,
    it is returned in `already_exists` rather than created again.

    Request Body:
        entity_id      (int, required):  Entity to associate the accounts with.
        profile_id     (int, required):  Wise profile ID (from GET /wise/profiles).
        sync_from_date (str, optional):  Earliest date to sync transactions from (YYYY-MM-DD).
                                         Defaults to 90 days ago.

    Returns:
        200: { created, already_exists, skipped, profile_id, message }
    """
    body = request.get_json() or {}
    entity_id = body.get("entity_id")
    profile_id = body.get("profile_id")
    sync_from_date_str = body.get("sync_from_date")

    if not entity_id:
        return jsonify({"error": "entity_id is required"}), 400
    if not profile_id:
        return jsonify({
            "error": "profile_id is required. Call GET /bank-accounts/wise/profiles to list options."
        }), 400

    # Resolve sync_from_date (default: 90 days ago)
    if sync_from_date_str:
        try:
            sync_from_date = date.fromisoformat(sync_from_date_str)
        except ValueError:
            return jsonify({"error": "Invalid sync_from_date — use YYYY-MM-DD"}), 400
    else:
        sync_from_date = date.today() - timedelta(days=_WISE_DEFAULT_HISTORY_DAYS)

    try:
        balances = wise_service.get_balances(profile_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    created = []
    already_exists = []
    skipped = []

    with db_session() as db:
        for balance in balances:
            balance_id = balance.get("id")
            currency = (balance.get("currency") or "").upper()

            if not balance_id or not currency:
                continue

            account_number = f"WISE-{balance_id}"

            # Deduplication: check if this balance is already connected
            existing = (
                db.query(FinanceBankAccount)
                .filter(
                    FinanceBankAccount.entity_id == entity_id,
                    FinanceBankAccount.account_number == account_number,
                )
                .first()
            )
            if existing:
                already_exists.append({
                    "id": existing.id,
                    "currency": currency,
                    "account_name": existing.account_name,
                })
                continue

            bank_account_data = BankAccountCreate(
                entity_id=entity_id,
                bank_name="Wise",
                account_number=account_number,
                account_name=f"Wise {currency}",
                currency=currency,
                file_adapter=None,
                api_config={
                    "provider": "wise",
                    "profile_id": profile_id,
                    "balance_id": balance_id,
                    "sync_from_date": sync_from_date.isoformat(),
                },
            )

            try:
                bank_account = bank_account_service.create(db, bank_account_data)
                created.append(BankAccountResponse.model_validate(bank_account).model_dump())
            except Exception as e:
                skipped.append({"currency": currency, "reason": str(e)})

    total = len(created)
    return jsonify({
        "profile_id": profile_id,
        "sync_from_date": sync_from_date.isoformat(),
        "created": created,
        "already_exists": already_exists,
        "skipped": skipped,
        "message": f"{total} Wise bank account(s) created"
                   + (f", {len(already_exists)} already connected" if already_exists else ""),
    }), 200


@bank_accounts_bp.route('/<int:bank_account_id>/sync', methods=['POST'])
def sync_bank_account(bank_account_id: int):
    """
    Sync transactions for a Wise-connected bank account.

    Date range is auto-calculated from api_credentials:
      - First sync:       sync_from_date  → today
      - Subsequent syncs: last_synced_at - 1 day → today  (1-day overlap catches late-posting)
    Manual override: pass date_from and date_to in the body.

    Deduplication: each Wise transaction has a unique TransferWise ID used as
    the fingerprint — re-syncing the same period creates zero duplicates.

    Request Body (all optional):
        date_from (str): Override start date YYYY-MM-DD.
        date_to   (str): Override end date YYYY-MM-DD.

    Returns:
        200: { transactions_created, duplicates_skipped, errors, date_from, date_to,
               import_batch_id, categorization }
        400: Missing credentials or date error.
        404: Bank account not found.
    """
    body = request.get_json() or {}
    date_from_str = body.get("date_from")
    date_to_str = body.get("date_to")

    with db_session() as db:
        bank_account = bank_account_service.get_by_id(db, bank_account_id)
        if not bank_account:
            raise NotFoundError(f"Bank account with ID {bank_account_id} not found")

        api_config = bank_account.api_config or {}
        api_sync_state = bank_account.api_sync_state or {}
        profile_id = api_config.get("profile_id")
        balance_id = api_config.get("balance_id")

        if not profile_id or not balance_id:
            return jsonify({
                "error": "This bank account has no Wise API credentials. "
                         "Use POST /bank-accounts/wise/connect to set up."
            }), 400

        # ── Resolve date range ────────────────────────────────────────────────
        date_to = date.today()

        if date_from_str:
            try:
                date_from = date.fromisoformat(date_from_str)
            except ValueError:
                return jsonify({"error": "Invalid date_from — use YYYY-MM-DD"}), 400
        elif api_sync_state.get("last_synced_at"):
            # Overlap by 1 day to catch transactions that post a day late
            date_from = date.fromisoformat(api_sync_state["last_synced_at"]) - timedelta(days=1)
        elif api_config.get("sync_from_date"):
            date_from = date.fromisoformat(api_config["sync_from_date"])
        else:
            date_from = date_to - timedelta(days=_WISE_DEFAULT_HISTORY_DAYS)

        if date_to_str:
            try:
                date_to = date.fromisoformat(date_to_str)
            except ValueError:
                return jsonify({"error": "Invalid date_to — use YYYY-MM-DD"}), 400

        # ── Fetch + import ────────────────────────────────────────────────────
        try:
            statement = wise_service.get_statement(profile_id, balance_id, date_from, date_to)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        normalized_rows, parse_errors = wise_service.normalize_statement(
            statement, bank_account.currency
        )

        result = transaction_service.import_from_rows(
            db=db,
            bank_account=bank_account,
            normalized_rows=normalized_rows,
            fingerprint_fn=lambda row: [row.source_id or ""],
            source="wise_api_sync",
            extra_errors=parse_errors,
        )

        # ── Update last_synced_at in sync state ───────────────────────────────
        bank_account.api_sync_state = {"last_synced_at": date_to.isoformat()}
        db.commit()

        # ── Auto-categorize new transactions ──────────────────────────────────
        if result.get("transactions_created", 0) > 0:
            try:
                cat = categorization_service.run(db, bank_account_id=bank_account_id)
                result["categorization"] = {
                    "categorized": cat["categorized"],
                    "uncategorized": cat["uncategorized"],
                    "errors": cat["errors"],
                }
            except Exception as e:
                logger.warning(f"Auto-categorization failed after Wise sync: {e}", exc_info=True)
                result["categorization"] = {"error": str(e)}

        result["date_from"] = date_from.isoformat()
        result["date_to"] = date_to.isoformat()

        return jsonify(result), 200


@bank_accounts_bp.route('/dbs/import', methods=['POST'])
def dbs_import():
    """
    Import a DBS Business Multi-Currency Account PDF statement.

    Parses the PDF, finds all currency sections, and routes each section's
    transactions to the matching DBS bank account for the entity
    (matched by entity_id + bank_name='dbs' + currency, case-insensitive).

    Request: multipart/form-data
        entity_id (int, required): Entity to import into.
        file      (file, required): DBS PDF statement file.

    Returns:
        200: {
            currencies_found: ["SGD", "USD", ...],
            results: {
                "SGD": { transactions_created, duplicates_skipped, errors,
                         import_batch_id, bank_account_id, categorization },
                "USD": { "skipped": "No DBS USD bank account found for this entity" },
                "EUR": { "skipped": "No transactions in statement" },
            },
            parse_warnings: [ ... ],
        }
        400: Missing entity_id or file, or PDF parse error.
    """
    entity_id = request.form.get('entity_id', type=int)
    if not entity_id:
        return jsonify({"error": "entity_id is required"}), 400

    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "File must be a PDF (.pdf)"}), 400

    pdf_bytes = file.read()

    # ── Parse PDF via registry adapter ────────────────────────────────────────
    try:
        dbs_adapter = get_adapter("dbs")
        sections = dbs_adapter.parse_pdf(pdf_bytes)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    parse_warnings = list(dbs_adapter.errors)
    currencies_found = list(sections.keys())

    # ── Route each currency section to the matching bank account ──────────────
    import_results: dict = {}

    with db_session() as db:
        for currency, rows in sections.items():
            if not rows:
                import_results[currency] = {"skipped": "No transactions in statement"}
                continue

            # Find matching DBS bank account for this entity + currency
            bank_account = (
                db.query(FinanceBankAccount)
                .filter(
                    FinanceBankAccount.entity_id == entity_id,
                    FinanceBankAccount.bank_name.ilike('dbs'),
                    FinanceBankAccount.currency == currency,
                )
                .first()
            )

            if not bank_account:
                import_results[currency] = {
                    "skipped": (
                        f"No DBS {currency} bank account found for this entity. "
                        f"Create one first in Bank Accounts."
                    )
                }
                continue

            result = transaction_service.import_from_rows(
                db=db,
                bank_account=bank_account,
                normalized_rows=rows,
                fingerprint_fn=dbs_adapter.fingerprint_fields,
                source="dbs_pdf_import",
            )

            # Auto-categorize new transactions
            if result.get("transactions_created", 0) > 0:
                try:
                    cat = categorization_service.run(
                        db, bank_account_id=bank_account.id
                    )
                    result["categorization"] = {
                        "categorized": cat["categorized"],
                        "uncategorized": cat["uncategorized"],
                        "errors": cat["errors"],
                    }
                except Exception as e:
                    logger.warning(
                        f"Auto-categorization failed after DBS import ({currency}): {e}",
                        exc_info=True,
                    )
                    result["categorization"] = {"error": str(e)}

            result["bank_account_id"] = bank_account.id
            import_results[currency] = result

    return jsonify({
        "currencies_found": currencies_found,
        "results": import_results,
        "parse_warnings": parse_warnings,
    }), 200
