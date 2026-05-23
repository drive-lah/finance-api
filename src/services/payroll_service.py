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


class PayrollService:
    """Service for managing payroll runs (System 3)."""

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
