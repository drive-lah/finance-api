"""Employee Claim service — self-submit, manager-approved, own-scoped (use cases #5/#6).

Own-scoping: an employee sees only their own claims; a manager also sees claims routed to
them for approval; admin sees all. Approval posts Dr claim-COA / Cr 2303 Employee Claims
Payable, then reimbursement runs through the payout rails.
"""
from datetime import datetime, date
from decimal import Decimal

from src.models.employee_claim import (
    FinanceEmployeeClaim, ClaimStatus, CATEGORY_COA, EMPLOYEE_CLAIMS_PAYABLE,
)
from src.models.user import User
from src.services.journal_service import journal_service
from src.models.journal_entry import JournalEntryStatus
from src.utils.errors import NotFoundError, BadRequestError, ConflictError


class ClaimService:

    def _manager_of(self, db, user_id: int):
        u = db.get(User, user_id)
        return u.manager_id if u else None

    def create(self, db, owner_user_id: int, data: dict) -> FinanceEmployeeClaim:
        category = (data.get("category") or "other").lower()
        coa = data.get("coa_account_code") or CATEGORY_COA.get(category, "6014")
        claim = FinanceEmployeeClaim(
            owner_user_id=owner_user_id,
            entity_id=int(data["entity_id"]),
            manager_user_id=self._manager_of(db, owner_user_id),
            amount=Decimal(str(data["amount"])),
            currency=data.get("currency") or "SGD",
            category=category, coa_account_code=coa,
            description=data.get("description"),
            expense_date=data.get("expense_date"),
            trip_id=data.get("trip_id"), intercom_ticket_id=data.get("intercom_ticket_id"),
            receipt_s3_key=data.get("receipt_s3_key"), receipt_filename=data.get("receipt_filename"),
            status=ClaimStatus.DRAFT.value)
        db.add(claim); db.flush()
        return claim

    def list_scoped(self, db, caller_user_id: int, is_admin: bool,
                    status: str = None, mine_only: bool = False):
        q = db.query(FinanceEmployeeClaim)
        if not is_admin:
            if mine_only:
                q = q.filter(FinanceEmployeeClaim.owner_user_id == caller_user_id)
            else:
                # own claims + claims awaiting my approval (I'm the manager)
                q = q.filter((FinanceEmployeeClaim.owner_user_id == caller_user_id) |
                             (FinanceEmployeeClaim.manager_user_id == caller_user_id))
        elif mine_only:
            q = q.filter(FinanceEmployeeClaim.owner_user_id == caller_user_id)
        if status:
            q = q.filter(FinanceEmployeeClaim.status == status)
        return q.order_by(FinanceEmployeeClaim.created_at.desc()).all()

    def _get_visible(self, db, claim_id, caller_user_id, is_admin) -> FinanceEmployeeClaim:
        c = db.get(FinanceEmployeeClaim, claim_id)
        if not c:
            raise NotFoundError(f"Claim {claim_id} not found")
        if not is_admin and caller_user_id not in (c.owner_user_id, c.manager_user_id):
            raise BadRequestError("You don't have access to this claim.")
        return c

    def get(self, db, claim_id, caller_user_id, is_admin):
        return self._get_visible(db, claim_id, caller_user_id, is_admin)

    def submit(self, db, claim_id, caller_user_id, is_admin):
        c = self._get_visible(db, claim_id, caller_user_id, is_admin)
        if c.owner_user_id != caller_user_id and not is_admin:
            raise BadRequestError("Only the claimant can submit their claim.")
        if c.status != ClaimStatus.DRAFT.value:
            raise ConflictError(f"Claim is {c.status}, not draft.")
        c.manager_user_id = self._manager_of(db, c.owner_user_id)
        c.status = ClaimStatus.SUBMITTED.value
        c.submitted_at = datetime.utcnow()
        # enqueue an approval task for the manager (company-wide My Tasks queue)
        from src.services.task_service import task_service
        owner = db.get(User, c.owner_user_id)
        task_service.enqueue(
            db, type="claim-approval", source_ref=f"claim:{c.id}",
            title=f"Approve expense claim — {(owner.name if owner else 'employee')} · "
                  f"{c.currency} {float(c.amount):,.2f}",
            summary=c.description or c.category,
            body={"claim_id": c.id, "category": c.category, "coa": c.coa_account_code,
                  "amount": float(c.amount), "currency": c.currency,
                  "claimant": owner.name if owner else None, "entity_id": c.entity_id},
            amount=c.amount, currency=c.currency,
            assignee_user_id=c.manager_user_id, assignee_role="finance.expenses",
            created_by=str(c.owner_user_id))
        return c

    def approve(self, db, claim_id, caller_user_id, is_admin) -> FinanceEmployeeClaim:
        c = db.get(FinanceEmployeeClaim, claim_id)
        if not c:
            raise NotFoundError(f"Claim {claim_id} not found")
        if c.status != ClaimStatus.SUBMITTED.value:
            raise ConflictError(f"Claim is {c.status}, not submitted.")
        # manager-only approval (org hierarchy); admin override
        if not is_admin and caller_user_id != c.manager_user_id:
            raise BadRequestError("Only the claimant's manager can approve this claim.")
        if caller_user_id == c.owner_user_id and not is_admin:
            raise BadRequestError("You cannot approve your own claim.")
        # bill JE: Dr claim-COA / Cr 2303 Employee Claims Payable
        amt = float(c.amount)
        lines = [
            {"account_code": c.coa_account_code, "debit_amount": amt, "credit_amount": 0.0,
             "description": f"Employee claim #{c.id} ({c.category})"},
            {"account_code": EMPLOYEE_CLAIMS_PAYABLE, "debit_amount": 0.0, "credit_amount": amt,
             "description": f"Employee claim #{c.id} payable"},
        ]
        je = journal_service.create(
            db, entity_id=c.entity_id, entry_date=date.today(),
            description=f"Employee claim #{c.id} — {c.description or c.category}",
            lines=lines, reference_number=f"CLAIM-{c.id}",
            status=JournalEntryStatus.POSTED)
        c.journal_entry_id = je.id
        c.status = ClaimStatus.APPROVED.value
        c.approved_by = str(caller_user_id)
        c.approved_at = datetime.utcnow()
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"claim:{c.id}", "done",
                                      acted_by=caller_user_id, action="approve")
        return c

    def create_claim_payment_entries(self, db, bank_account, claim: FinanceEmployeeClaim,
                                     txn_date, abs_amount, source: str, description: str):
        """Settle an approved claim on a matched reimbursement txn (POL-139, category 4). Mirrors
        invoice AP knock-off / payroll payment entries: posts Dr 2303 Employee Claims Payable / Cr bank
        (clearing the SAME liability the approval JE credited), flips the claim PAID, links the txn.
        Same-entity only for v1 (claims are within the employee's entity); cross-entity raises."""
        from src.models.journal_entry import FinanceJournalEntry
        if bank_account.entity_id != claim.entity_id:
            raise BadRequestError(
                f"Cross-entity claim reimbursement not supported (bank entity {bank_account.entity_id} "
                f"≠ claim entity {claim.entity_id}).")
        amt = float(abs_amount)
        ref = f"CLAIM-{claim.id}"
        je = journal_service.create(
            db, entity_id=claim.entity_id, entry_date=txn_date, description=description,
            lines=[
                {"account_code": EMPLOYEE_CLAIMS_PAYABLE, "debit_amount": amt, "credit_amount": 0.0,
                 "description": f"Reimburse employee claim #{claim.id}"},
                {"account_code": bank_account.coa_account_code, "debit_amount": 0.0, "credit_amount": amt,
                 "description": f"Reimburse employee claim #{claim.id}"},
            ],
            reference_number=ref, status=JournalEntryStatus.POSTED)
        je.source = source
        claim.status = ClaimStatus.PAID.value
        db.flush()
        return je

    def reject(self, db, claim_id, caller_user_id, is_admin, reason: str):
        c = db.get(FinanceEmployeeClaim, claim_id)
        if not c:
            raise NotFoundError(f"Claim {claim_id} not found")
        if c.status != ClaimStatus.SUBMITTED.value:
            raise ConflictError(f"Claim is {c.status}, not submitted.")
        if not is_admin and caller_user_id != c.manager_user_id:
            raise BadRequestError("Only the claimant's manager can reject this claim.")
        c.status = ClaimStatus.REJECTED.value
        c.rejected_by = str(caller_user_id)
        c.rejection_reason = reason
        from src.services.task_service import task_service
        task_service.close_for_source(db, f"claim:{c.id}", "returned",
                                      acted_by=caller_user_id, action="reject", notes=reason)
        return c


claim_service = ClaimService()
