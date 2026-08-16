"""
Payroll Service — System 3

Creates payroll runs and immediately posts the complete 4-line JE:
  Dr 6000 Salaries Expense   (gross)
  Dr 6001 Employer CPF
  Cr bank_coa                (net payout = gross - employee_cpf)
  Cr 2300 CPF Payable        (employer_cpf + employee_cpf)

Bank recon Step 2.5 later matches bank payments to this run.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
import uuid

from sqlalchemy.orm import Session

from src.models.payroll import FinancePayrollRun
from src.models.bank_account import FinanceBankAccount
from src.models.journal_entry import JournalEntryStatus
from src.services.journal_service import journal_service

if TYPE_CHECKING:
    from src.models.journal_entry import FinanceJournalEntry

# IC (Intercompany) Account Codes for cross-entity payroll transfers
_IC_RECEIVABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8000",  # SG books: IC Due from AU
    ("SG", "Ventures"): "8001",  # SG books: IC Due from Ventures
    ("AU", "SG"):       "8010",  # AU books: IC Due from SG
    ("AU", "Ventures"): "8011",  # AU books: IC Due from Ventures
    ("Ventures", "SG"): "8020",  # Ventures books: IC Due from SG
    ("Ventures", "AU"): "8021",  # Ventures books: IC Due from AU
}
_IC_PAYABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8100",  # SG books: IC Due to AU
    ("SG", "Ventures"): "8101",  # SG books: IC Due to Ventures
    ("AU", "SG"):       "8110",  # AU books: IC Due to SG
    ("AU", "Ventures"): "8111",  # AU books: IC Due to Ventures
    ("Ventures", "SG"): "8120",  # Ventures books: IC Due to SG
    ("Ventures", "AU"): "8121",  # Ventures books: IC Due to AU
}


def _entity_short(name: str) -> str:
    """Extract the short entity identifier from a full name: 'DL SG' → 'SG'."""
    return name.strip().rsplit(" ", 1)[-1]

SALARY_ACCOUNT = "6000"        # Salaries & Wages
CPF_EMPLOYER_ACCOUNT = "6001"  # Employer CPF
CPF_PAYABLE_ACCOUNT = "2300"   # CPF Payable
SALARIES_PAYABLE_ACCOUNT = "2304"  # Net salaries payable (PR-4 accrue-then-pay)
# Statutory payable account → the authority counterparty it's paid to (PR-4 fan-out). Matched by name.
STATUTORY_AUTHORITY = {"2300": "CPF", "2302": "superannuation",
                       "2301": "tax office", "2305": "tax office"}  # 2301 PAYG withholding (AU) → ATO


class PayrollService:
    """Service for managing payroll runs (System 3)."""

    def transition_run(self, db: Session, run, to_status: str, *, actor=None) -> "FinancePayrollRun":
        """PR-2: move a payroll run to a new status, enforcing the lifecycle (PAYROLL_TRANSITIONS).
        Raises on an illegal transition so callers can't skip approval or resurrect a paid/void run."""
        from src.models.payroll import can_transition, PayrollRunStatus
        from src.utils.errors import BadRequestError
        valid = {s.value for s in PayrollRunStatus}
        if to_status not in valid:
            raise BadRequestError(f"Unknown payroll status '{to_status}'.")
        if not can_transition(run.status, to_status):
            raise BadRequestError(
                f"Illegal payroll transition {run.status} → {to_status}.")
        run.status = to_status
        db.flush()
        return run

    def get_approval_view(self, db: Session, run_id: int) -> dict:
        """PR-3 approval view (best-practice consolidated-per-group, Gaurav): for each salary-account
        group, the total + headcount + approver/status AND the per-employee lines AND the change-summary
        vs the prior run (new joiners / salary changes / leavers) — so an approver drills in but signs
        once, reviewing by exception."""
        from src.services.hr_payroll_service import hr_payroll_service
        from src.models.hr_payroll import HrPayrollItem
        from src.models.hr_employee import HrEmployee
        from src.models.counterparty import FinanceCounterparty
        from src.models.payroll_approval import FinancePayrollApproval
        from src.models.payroll_adjustment import FinancePayrollAdjustment
        from src.utils.errors import NotFoundError
        run = db.get(FinancePayrollRun, run_id)
        if not run:
            raise NotFoundError(f"Payroll run {run_id} not found")
        # PR-7 touch: adjustment reasons per employee, surfaced to approvers (audit visibility).
        adj_by_emp: dict[int, list] = {}
        for a in db.query(FinancePayrollAdjustment).filter(FinancePayrollAdjustment.run_id == run_id).all():
            adj_by_emp.setdefault(a.employee_id, []).append(
                {"field": a.field, "old": a.old_value, "new": a.new_value, "reason": a.reason})

        def _name(emp):
            cp = (db.query(FinanceCounterparty)
                  .filter(FinanceCounterparty.external_system == "employee",
                          FinanceCounterparty.external_id == str(emp.user_id)).first())
            return (cp.name if cp else None) or emp.designation or f"user {emp.user_id}"

        # prior run for this entity (the most recent posted/paid one before this run_date)
        prior = (db.query(FinancePayrollRun)
                 .filter(FinancePayrollRun.entity_id == run.entity_id,
                         FinancePayrollRun.id != run_id,
                         FinancePayrollRun.status.in_(["POSTED", "PAYMENT_INITIATED", "PAID"]),
                         FinancePayrollRun.run_date <= run.run_date)
                 .order_by(FinancePayrollRun.run_date.desc(), FinancePayrollRun.id.desc()).first())
        prior_gross = {}   # employee_id -> gross in the prior run
        if prior:
            for pi in db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == prior.id).all():
                prior_gross[pi.employee_id] = float(pi.gross_amount)

        items = db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == run_id).all()
        approvals = {a.salary_account_code: a for a in
                     db.query(FinancePayrollApproval).filter(FinancePayrollApproval.run_id == run_id).all()}
        groups: dict[str, dict] = {}
        seen_emp = set()
        for it in items:
            emp = db.get(HrEmployee, it.employee_id)
            if not emp:
                continue
            code = hr_payroll_service._resolve_salary_code(db, emp, it.currency)
            seen_emp.add(it.employee_id)
            g = groups.setdefault(code, {"salary_account_code": code, "lines": [], "leavers": [],
                                         "total": 0.0, "headcount": 0})
            gross = float(it.gross_amount)
            prev = prior_gross.get(it.employee_id)
            change = "new" if prev is None else ("changed" if abs(prev - gross) > 0.01 else "same")
            g["lines"].append({"employee_id": it.employee_id, "name": _name(emp), "gross": gross,
                               "net": float(it.net_amount), "change": change, "prev_gross": prev,
                               "adjustments": adj_by_emp.get(it.employee_id, [])})
            g["total"] += gross
            g["headcount"] += 1
        # leavers: anyone paid in the prior run but not in this one (attributed to their salary group)
        if prior:
            for pi in db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == prior.id).all():
                if pi.employee_id in seen_emp:
                    continue
                emp = db.get(HrEmployee, pi.employee_id)
                if not emp:
                    continue
                code = hr_payroll_service._resolve_salary_code(db, emp, pi.currency)
                if code in groups:
                    groups[code]["leavers"].append({"employee_id": pi.employee_id, "name": _name(emp),
                                                    "prev_gross": float(pi.gross_amount)})
        out_groups = []
        for code, g in groups.items():
            a = approvals.get(code)
            g["approver"] = a.approver if a else None
            g["status"] = a.status if a else "pending"
            g["changes_summary"] = {
                "new": sum(1 for l in g["lines"] if l["change"] == "new"),
                "changed": sum(1 for l in g["lines"] if l["change"] == "changed"),
                "leavers": len(g["leavers"])}
            g["total"] = round(g["total"], 2)
            out_groups.append(g)
        return {
            "run": {"id": run.id, "status": run.status, "entity_id": run.entity_id,
                    "run_date": run.run_date.isoformat() if run.run_date else None,
                    "period": [run.payroll_period_start.isoformat(), run.payroll_period_end.isoformat()]},
            "prior_run_id": prior.id if prior else None,
            "groups": out_groups,
        }

    def submit_for_approval(self, db: Session, run_id: int, actor=None) -> dict:
        """PR-3 (POL-140): submit a DRAFT run for SEGMENTED approval. Builds the balanced JE as a DRAFT
        (posted only on full approval — the draft-JE benefit), groups the payslip lines by salary account,
        and routes each group to that account's approver in the COA matrix (finance_coa_config). Run moves
        DRAFT → PENDING_APPROVAL. Returns {run, approvals}."""
        from src.services.hr_payroll_service import hr_payroll_service
        from src.services.journal_service import journal_service
        from src.services.task_service import task_service
        from src.models.hr_payroll import HrPayrollItem
        from src.models.bank_account import FinanceBankAccount
        from src.models.coa_config import FinanceCoaConfig
        from src.models.payroll_approval import FinancePayrollApproval, PayrollApprovalStatus
        from src.models.journal_entry import JournalEntryStatus
        from src.utils.errors import BadRequestError, NotFoundError
        run = db.get(FinancePayrollRun, run_id)
        if not run:
            raise NotFoundError(f"Payroll run {run_id} not found")
        if run.status != "DRAFT":
            raise BadRequestError(f"Only a DRAFT run can be submitted (is {run.status}).")
        items = db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == run_id).all()
        if not items:
            raise BadRequestError("Run has no payroll items.")
        bank = db.get(FinanceBankAccount, run.bank_account_id)
        # PR-4: accrue the net to Salaries Payable (2304) rather than crediting bank directly, so the net
        # can be fanned out into the register and settled per employee (Dr 2304 / Cr bank).
        lines, groups, desc = hr_payroll_service._build_je_lines_and_groups(
            db, run, items, bank, net_to_account=SALARIES_PAYABLE_ACCOUNT)
        # FX happens HERE (draft JE at submit-for-approval), not at draft creation: fill functional totals.
        hr_payroll_service.set_functional_totals(db, run, items)
        # DRAFT JE — the approver reviews the literal entry; posted only when all groups sign off.
        je = journal_service.create(db=db, entity_id=run.entity_id, entry_date=run.run_date,
                                    description=desc, lines=lines,
                                    created_by=(actor or {}).get("user_id"),
                                    status=JournalEntryStatus.DRAFT)
        je.source = "payroll"
        run.journal_entry_id = je.id
        approvals = []
        for salary_code, g in groups.items():
            cfg = db.query(FinanceCoaConfig).filter(FinanceCoaConfig.coa_code == salary_code).first()
            approver = cfg.approver_1 if cfg else None
            a = FinancePayrollApproval(run_id=run_id, salary_account_code=salary_code,
                                       group_total=g["total"], group_headcount=g["headcount"],
                                       approver=approver, status=PayrollApprovalStatus.PENDING.value)
            db.add(a); db.flush(); approvals.append(a)
            task_service.enqueue(
                db, type="payroll-approval", source_ref=f"payroll:{run_id}:{salary_code}",
                title=f"Approve payroll — account {salary_code} · {run.currency if hasattr(run,'currency') else ''} "
                      f"{g['total']:,.2f} ({g['headcount']} staff)",
                summary=f"Payroll run #{run_id} · salary account {salary_code}",
                body={"run_id": run_id, "salary_account_code": salary_code, "total": g["total"],
                      "headcount": g["headcount"]},
                assignee_role=approver or "finance.payroll",
                created_by=(actor or {}).get("user_id"))
        self.transition_run(db, run, "PENDING_APPROVAL", actor=actor)
        return {"run": run.to_dict() if hasattr(run, "to_dict") else {"id": run.id, "status": run.status},
                "approvals": [a.to_dict() for a in approvals]}

    def decide_group(self, db: Session, run_id: int, salary_account_code: str, decision: str,
                     actor=None, reason: str = None) -> dict:
        """PR-3: record one salary-account group's approval decision. A rejection sends the run back to
        DRAFT (and discards the draft JE). When ALL groups are approved, the run → APPROVED and the draft
        JE is POSTED (→ POSTED), after which the register fan-out (PR-4) + settlement run."""
        from src.services.journal_service import journal_service
        from src.models.payroll_approval import FinancePayrollApproval, PayrollApprovalStatus
        from src.models.journal_entry import JournalEntryStatus
        from src.utils.errors import BadRequestError, NotFoundError
        from datetime import datetime
        run = db.get(FinancePayrollRun, run_id)
        if not run:
            raise NotFoundError(f"Payroll run {run_id} not found")
        if run.status != "PENDING_APPROVAL":
            raise BadRequestError(f"Run is {run.status}, not awaiting approval.")
        a = (db.query(FinancePayrollApproval)
             .filter(FinancePayrollApproval.run_id == run_id,
                     FinancePayrollApproval.salary_account_code == salary_account_code).first())
        if not a:
            raise NotFoundError(f"No approval group {salary_account_code} on run {run_id}.")
        if a.status != PayrollApprovalStatus.PENDING.value:
            raise BadRequestError(f"Group {salary_account_code} already {a.status}.")
        if decision not in ("approved", "rejected"):
            raise BadRequestError("decision must be 'approved' or 'rejected'.")
        a.status = decision
        a.decided_by = (actor or {}).get("user_id")
        a.decided_at = datetime.utcnow()
        a.reason = reason
        db.flush()
        if decision == "rejected":
            # discard the draft JE and send the run back to DRAFT for fixes
            if run.journal_entry_id:
                from src.models.journal_entry import FinanceJournalEntry
                je = db.get(FinanceJournalEntry, run.journal_entry_id)
                if je and je.status == JournalEntryStatus.DRAFT:
                    db.delete(je)
                run.journal_entry_id = None
            self.transition_run(db, run, "DRAFT", actor=actor)
            return {"run_id": run_id, "status": run.status, "group": a.to_dict()}
        # approved — all groups done?
        remaining = (db.query(FinancePayrollApproval)
                     .filter(FinancePayrollApproval.run_id == run_id,
                             FinancePayrollApproval.status != PayrollApprovalStatus.APPROVED.value).count())
        if remaining == 0:
            self.transition_run(db, run, "APPROVED", actor=actor)
            journal_service.post_entry(db, run.journal_entry_id,
                                       posting_user_id=(actor or {}).get("user_id"))
            self.transition_run(db, run, "POSTED", actor=actor)
        return {"run_id": run_id, "status": run.status, "group": a.to_dict(),
                "groups_remaining": remaining}

    def fan_out_to_register(self, db: Session, run_id: int, actor=None) -> dict:
        """PR-4 (POL-139/140): fan a POSTED run OUT into the payout register — one net-salary payable per
        employee (payee = the employee counterparty) + one statutory payable per accrued authority account
        (CPF 2300 / super 2302 / PAYG 2305 → the CPF Board / super fund / tax office counterparty). Each
        row is a `payable_type='payroll'` payout awaiting payment (settled Dr <liability> / Cr bank later).
        Idempotent: re-running skips employees/statutory already fanned out."""
        from src.models.hr_payroll import HrPayrollItem
        from src.models.hr_employee import HrEmployee
        from src.models.counterparty import FinanceCounterparty
        from src.models.vendor_payout import FinancePayout, PayoutState
        from src.services.payout_service import DRY_RUN
        from src.utils.errors import BadRequestError, NotFoundError
        from datetime import datetime
        run = db.get(FinancePayrollRun, run_id)
        if not run:
            raise NotFoundError(f"Payroll run {run_id} not found")
        if run.status not in ("POSTED", "PAYMENT_INITIATED"):
            raise BadRequestError(f"Run must be POSTED to fan out (is {run.status}).")
        existing = {(p.payable_type, p.payable_id, p.counterparty_id) for p in
                    db.query(FinancePayout).filter(FinancePayout.payable_type == "payroll",
                                                   FinancePayout.payable_id == run_id).all()}
        items = db.query(HrPayrollItem).filter(HrPayrollItem.finance_payroll_run_id == run_id).all()
        net_payouts, statutory = [], {}
        for it in items:
            emp = db.get(HrEmployee, it.employee_id)
            cp = (db.query(FinanceCounterparty)
                  .filter(FinanceCounterparty.external_system == "employee",
                          FinanceCounterparty.external_id == str(emp.user_id)).first()) if emp else None
            if cp and ("payroll", run_id, cp.id) not in existing:
                p = FinancePayout(
                    invoice_id=None, payable_type="payroll", payable_id=run_id, method="system_wise",
                    counterparty_id=cp.id, entity_id=run.entity_id, amount=round(float(it.net_amount), 2),
                    currency=it.currency, state=PayoutState.DRAFT.value, is_dry_run=DRY_RUN,
                    requested_by=(actor or {}).get("user_id"), requested_at=datetime.utcnow())
                db.add(p); db.flush(); net_payouts.append(p.id)
            # accrue statutory obligations by credit account (tax_treatment=internal only)
            if emp and (emp.tax_treatment or "").lower() == "internal":
                for line in (it.deduction_lines or []):
                    code = line["coa_credit_code"]
                    if code in STATUTORY_AUTHORITY:
                        statutory[code] = statutory.get(code, 0.0) + float(line["amount"])
        stat_payouts = []
        for code, amount in statutory.items():
            if amount <= 0:
                continue
            authority = (db.query(FinanceCounterparty)
                         .filter(FinanceCounterparty.name.ilike(f"%{STATUTORY_AUTHORITY[code]}%")).first())
            if not authority or ("payroll", run_id, authority.id) in existing:
                continue
            p = FinancePayout(
                invoice_id=None, payable_type="payroll", payable_id=run_id, method="system_wise",
                counterparty_id=authority.id, entity_id=run.entity_id, amount=round(amount, 2),
                currency=(items[0].currency if items else "SGD"),
                external_reference=f"statutory:{code}", state=PayoutState.DRAFT.value, is_dry_run=DRY_RUN,
                requested_by=(actor or {}).get("user_id"), requested_at=datetime.utcnow())
            db.add(p); db.flush(); stat_payouts.append({"account": code, "payout_id": p.id, "amount": round(amount, 2)})
        db.commit()
        return {"run_id": run_id, "net_payouts": net_payouts, "statutory_payouts": stat_payouts,
                "net_count": len(net_payouts)}

    def create_run(self, db: Session, data: dict) -> FinancePayrollRun:
        """
        Create a payroll run and immediately post the 4-line JE.

        Validates that:
          - net_amount = gross - employee_cpf (must be positive)
          - cpf_payable = employer_cpf + employee_cpf
          - bank account belongs to the given entity and has a COA code

        Raises:
            ValueError: on validation failure or missing bank account.
        """
        entity_id = data["entity_id"]
        gross = Decimal(str(data["gross_amount"]))
        employer_cpf = Decimal(str(data["employer_cpf_amount"]))
        employee_cpf = Decimal(str(data["employee_cpf_amount"]))
        net = gross - employee_cpf
        cpf_payable = employer_cpf + employee_cpf

        if net < 0:
            raise ValueError(
                "net_amount would be negative: employee_cpf_amount exceeds gross_amount"
            )
        if gross <= 0:
            raise ValueError("gross_amount must be positive")

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == data["bank_account_id"]
        ).first()
        if not bank_account:
            raise ValueError(f"Bank account {data['bank_account_id']} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                "has no COA account code configured"
            )
        if bank_account.entity_id != entity_id:
            raise ValueError(
                f"Bank account {bank_account.id} belongs to entity "
                f"{bank_account.entity_id}, not {entity_id}"
            )

        run_date = data["run_date"]
        description = data.get("description") or f"Payroll run {run_date}"

        lines = [
            {
                "account_code": SALARY_ACCOUNT,
                "debit_amount": float(gross),
                "credit_amount": 0.0,
                "description": description,
            },
            {
                "account_code": CPF_EMPLOYER_ACCOUNT,
                "debit_amount": float(employer_cpf),
                "credit_amount": 0.0,
                "description": description,
            },
            {
                "account_code": bank_account.coa_account_code,
                "debit_amount": 0.0,
                "credit_amount": float(net),
                "description": description,
            },
            {
                "account_code": CPF_PAYABLE_ACCOUNT,
                "debit_amount": 0.0,
                "credit_amount": float(cpf_payable),
                "description": description,
            },
        ]

        je = journal_service.create(
            db=db,
            entity_id=entity_id,
            entry_date=run_date,
            description=description,
            lines=lines,
            reference_number=data.get("reference_number"),
            created_by=data.get("submitted_by"),
            status=JournalEntryStatus.POSTED,
        )
        je.source = "payroll"

        run = FinancePayrollRun(
            entity_id=entity_id,
            payroll_period_start=data["payroll_period_start"],
            payroll_period_end=data["payroll_period_end"],
            run_date=run_date,
            headcount=data.get("headcount"),
            gross_amount=gross,
            employer_cpf_amount=employer_cpf,
            employee_cpf_amount=employee_cpf,
            net_amount=net,
            cpf_payable_amount=cpf_payable,
            bank_account_id=data["bank_account_id"],
            description=description,
            reference_number=data.get("reference_number"),
            submitted_by=data.get("submitted_by"),
            status="POSTED",
            journal_entry_id=je.id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
    ) -> list[FinancePayrollRun]:
        query = db.query(FinancePayrollRun)
        if entity_id is not None:
            query = query.filter(FinancePayrollRun.entity_id == entity_id)
        return query.order_by(FinancePayrollRun.run_date.desc()).all()

    def get_by_id(self, db: Session, run_id: int) -> Optional[FinancePayrollRun]:
        return db.query(FinancePayrollRun).filter(
            FinancePayrollRun.id == run_id
        ).first()

    def create_payroll_payment_entries(
        self,
        db: Session,
        bank_account: FinanceBankAccount,
        payroll_run: FinancePayrollRun,
        txn_date: date,
        abs_amount: Decimal,
        match_type: str,
    ) -> "FinanceJournalEntry":
        """
        Create payroll payment journal entry(ies) for a matching transaction.

        Same-entity: Returns the existing payroll JE (already created by create_run).

        Cross-entity: Creates paired JEs with intercompany accounts:
          Payroll entity: Dr 6000 Salary / Dr 6001 CPF / Cr 8100 IC Payable / Cr 2300 CPF Payable
          Bank entity: Dr 8000 IC Receivable / Cr Bank

        Both JEs share an intercompany_group_id when cross-entity.

        Args:
            db: Database session
            bank_account: Bank account making the payment
            payroll_run: Payroll run being matched
            txn_date: Date of transaction
            abs_amount: Absolute payment amount
            match_type: "net" or "cpf"

        Returns:
            Primary journal entry (payroll JE if same-entity, bank JE if cross-entity)

        Raises:
            ValueError: If cross-entity codes cannot be resolved
        """
        from src.models.journal_entry import FinanceJournalEntry

        bank_entity_id = bank_account.entity_id
        payroll_entity_id = payroll_run.entity_id
        bank_coa = bank_account.coa_account_code
        run_ref = f"Payroll run {payroll_run.id}"

        if bank_entity_id == payroll_entity_id:
            # Same-entity: return existing payroll JE
            from src.models.journal_entry import FinanceJournalEntry
            return db.get(FinanceJournalEntry, payroll_run.journal_entry_id)

        # ── Cross-entity: Create paired JEs ───────────────────────────
        ic_codes = self._get_ic_codes(db, bank_entity_id, payroll_entity_id)
        if not ic_codes:
            raise ValueError(
                f"Cannot create cross-entity payroll payment: "
                f"no IC codes found for bank entity {bank_entity_id} / payroll entity {payroll_entity_id}."
            )

        ic_receivable, ic_payable = ic_codes
        ic_group_id = str(uuid.uuid4())

        # Payroll entity JE: Dr Salary / Dr CPF / Cr IC Payable / Cr CPF Payable
        payroll_entry = journal_service.create(
            db=db,
            entity_id=payroll_entity_id,
            entry_date=txn_date,
            description=f"Payroll payment (IC) — {run_ref}",
            lines=[
                {
                    "account_code": SALARY_ACCOUNT,
                    "debit_amount": float(payroll_run.gross_amount),
                    "credit_amount": 0.0,
                    "description": run_ref,
                },
                {
                    "account_code": CPF_EMPLOYER_ACCOUNT,
                    "debit_amount": float(payroll_run.employer_cpf_amount),
                    "credit_amount": 0.0,
                    "description": "Employer CPF",
                },
                {
                    "account_code": ic_payable,
                    "debit_amount": 0.0,
                    "credit_amount": float(payroll_run.net_amount),
                    "description": f"IC Due to entity {bank_entity_id}",
                },
                {
                    "account_code": CPF_PAYABLE_ACCOUNT,
                    "debit_amount": 0.0,
                    "credit_amount": float(payroll_run.cpf_payable_amount),
                    "description": "CPF Payable",
                },
            ],
        )
        payroll_entry.source = "payroll_knockoff_cross_entity"
        payroll_entry.intercompany_group_id = ic_group_id

        # Bank entity JE: Dr IC Receivable / Cr Bank
        bank_entry = journal_service.create(
            db=db,
            entity_id=bank_entity_id,
            entry_date=txn_date,
            description=f"Payroll payment (IC) — {run_ref}",
            lines=[
                {
                    "account_code": ic_receivable,
                    "debit_amount": float(abs_amount),
                    "credit_amount": 0.0,
                    "description": f"IC Due from entity {payroll_entity_id}",
                },
                {
                    "account_code": bank_coa,
                    "debit_amount": 0.0,
                    "credit_amount": float(abs_amount),
                    "description": run_ref,
                },
            ],
        )
        bank_entry.source = "payroll_knockoff_cross_entity"
        bank_entry.intercompany_group_id = ic_group_id

        db.flush()
        return bank_entry  # Return primary (bank) JE

    def run_retroactive_knockoff(self, db: Session, run: FinancePayrollRun) -> list[dict]:
        """
        After a payroll run is POSTED, re-open any bank transactions that were
        already (mis)categorized BEFORE the run existed but actually settle this
        run's net pay or CPF, and link them to the run.

        Why: the run JE credits the bank directly for net pay. A salary paid before
        its run is created gets booked as a standalone expense (also Cr Bank) — so
        once the run posts, the expense AND the bank outflow are double-counted.
        This re-opens that premature txn (voids its wrong JE) and links it to the
        run instead — the analog of invoice_service.run_retroactive_knockoff, and
        essential for the historical reconciliation where runs are created after
        the payments landed.

        Same-entity → links to the existing run JE (no new JE). Cross-entity →
        paired IC JEs. Skips txns already settled via a payroll knock-off.
        """
        from src.models.transaction import FinanceTransaction, TransactionStatus
        from src.models.bank_account import FinanceBankAccount
        from datetime import timedelta, datetime, UTC

        results: list[dict] = []
        if run.status != "POSTED" or not run.journal_entry_id:
            return results

        ba_ids = [
            r[0] for r in db.query(FinanceBankAccount.id)
            .filter(FinanceBankAccount.entity_id == run.entity_id).all()
        ]
        if not ba_ids:
            return results

        date_low = run.run_date - timedelta(days=7)
        date_high = run.run_date + timedelta(days=7)

        slots: list[tuple[str, float]] = []
        if run.net_payment_transaction_id is None and float(run.net_amount) > 0:
            slots.append(("net", float(run.net_amount)))
        if run.cpf_payment_transaction_id is None and float(run.cpf_payable_amount) > 0:
            slots.append(("cpf", float(run.cpf_payable_amount)))

        for match_type, target in slots:
            candidates = (
                db.query(FinanceTransaction)
                .filter(
                    FinanceTransaction.bank_account_id.in_(ba_ids),
                    FinanceTransaction.amount < 0,
                    FinanceTransaction.transaction_date.between(date_low, date_high),
                    FinanceTransaction.status.in_([
                        TransactionStatus.PENDING,
                        TransactionStatus.MATCHED,
                        TransactionStatus.RECONCILED,
                    ]),
                )
                .order_by(FinanceTransaction.transaction_date.asc(), FinanceTransaction.id.asc())
                .all()
            )
            match = None
            for txn in candidates:
                if txn.categorized_by_logic == "payroll_knockoff":
                    continue  # already settled via a payroll knock-off
                amt = abs(float(txn.amount))
                if abs(amt - target) / target <= 0.02:
                    match = txn
                    break
            if match is None:
                continue

            # Re-open: void the premature (wrong) JE so it can't double-count.
            if match.reconciled_journal_entry_id:
                journal_service.void_entry(
                    db, match.reconciled_journal_entry_id,
                    reason=f"retroactive_payroll_knockoff: run {run.id}",
                )

            bank_account = db.get(FinanceBankAccount, match.bank_account_id)
            if bank_account is None:
                continue
            primary_je = self.create_payroll_payment_entries(
                db=db, bank_account=bank_account, payroll_run=run,
                txn_date=match.transaction_date,
                abs_amount=Decimal(str(abs(float(match.amount)))),
                match_type=match_type,
            )
            match.status = TransactionStatus.MATCHED
            match.reconciled_journal_entry_id = primary_je.id
            match.matched_at = datetime.now(UTC)
            match.categorized_by_logic = "payroll_knockoff"
            if match_type == "net":
                run.net_payment_transaction_id = match.id
            else:
                run.cpf_payment_transaction_id = match.id
            results.append({
                "transaction_id": match.id, "match_type": match_type,
                "journal_entry_id": primary_je.id,
            })

        if results:
            db.commit()
        return results

    def _get_ic_codes(
        self,
        db: Session,
        from_entity_id: int,
        to_entity_id: int,
    ) -> Optional[tuple[str, str]]:
        """
        Get IC receivable and payable account codes for an entity pair.

        Args:
            from_entity_id: Entity making the payment (bank entity)
            to_entity_id: Entity receiving the payment (payroll entity)

        Returns:
            Tuple of (ic_receivable_code, ic_payable_code) or None if codes not found
        """
        from src.models.entity import FinanceEntity

        from_entity = db.get(FinanceEntity, from_entity_id)
        to_entity = db.get(FinanceEntity, to_entity_id)

        if not from_entity or not to_entity:
            return None

        from_short = _entity_short(from_entity.name)
        to_short = _entity_short(to_entity.name)

        rec_code = _IC_RECEIVABLE_CODES.get((from_short, to_short))
        pay_code = _IC_PAYABLE_CODES.get((to_short, from_short))

        if not rec_code or not pay_code:
            return None

        return (rec_code, pay_code)


# Singleton instance
payroll_service = PayrollService()
