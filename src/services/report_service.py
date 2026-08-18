"""
Report Service

Business logic for generating financial reports.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from src.models import (
    FinanceJournalLine,
    FinanceJournalEntry,
    FinanceAccount,
    JournalEntryStatus,
    AccountType,
)


class ReportService:
    """Service for generating financial reports."""
    
    def get_trial_balance(
        self,
        db: Session,
        entity_id: int,
        as_of_date: Optional[date] = None
    ) -> dict:
        """
        Generate a trial balance report showing account balances.
        
        The trial balance lists all accounts with their debit and credit balances,
        ensuring that total debits equal total credits.
        
        Args:
            db: Database session
            entity_id: ID of the entity to generate report for
            as_of_date: Report as of this date (defaults to today if None)
        
        Returns:
            dict with structure:
            {
                "entity_id": int,
                "as_of_date": str,
                "accounts": [
                    {
                        "account_code": str,
                        "account_name": str,
                        "account_type": str,
                        "debit_balance": float,
                        "credit_balance": float,
                        "net_balance": float
                    },
                    ...
                ],
                "totals": {
                    "total_debits": float,
                    "total_credits": float
                }
            }
        """
        if as_of_date is None:
            as_of_date = date.today()
        
        # Query all Posted journal lines up to as_of_date
        # Join with journal_entry to filter by status and entry_date
        # Join with finance_accounts to get account details
        query = (
            db.query(
                FinanceJournalLine.account_code,
                FinanceAccount.name.label('account_name'),
                FinanceAccount.account_type,
                func.sum(FinanceJournalLine.debit_amount).label('total_debit'),
                func.sum(FinanceJournalLine.credit_amount).label('total_credit')
            )
            .join(
                FinanceJournalEntry,
                FinanceJournalLine.entry_id == FinanceJournalEntry.id
            )
            .join(
                FinanceAccount,
                and_(
                    FinanceJournalLine.account_code == FinanceAccount.code,
                    or_(
                        FinanceAccount.entity_id == entity_id,   # entity-specific (bank accounts)
                        FinanceAccount.entity_id == None          # group-level (all other accounts)
                    )
                )
            )
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalEntry.status == JournalEntryStatus.POSTED)
            .filter(FinanceJournalEntry.entry_date <= as_of_date)
            .group_by(
                FinanceJournalLine.account_code,
                FinanceAccount.name,
                FinanceAccount.account_type
            )
            .order_by(FinanceJournalLine.account_code)
        )
        
        results = query.all()
        
        # Process results into structured output
        accounts = []
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")
        
        for row in results:
            debit_balance = row.total_debit or Decimal("0.00")
            credit_balance = row.total_credit or Decimal("0.00")
            net_balance = debit_balance - credit_balance
            
            accounts.append({
                "account_code": row.account_code,
                "account_name": row.account_name,
                "account_type": row.account_type.value,
                "debit_balance": float(debit_balance),
                "credit_balance": float(credit_balance),
                "net_balance": float(net_balance)
            })
            
            total_debits += debit_balance
            total_credits += credit_balance
        
        # Group accounts by type
        grouped_accounts: dict[str, list[dict]] = {}
        for account_type in AccountType:
            grouped_accounts[account_type.value] = []
        
        for account in accounts:
            account_type = account["account_type"]
            grouped_accounts[account_type].append(account)
        
        # Remove empty groups
        grouped_accounts = {
            k: v for k, v in grouped_accounts.items() if v
        }
        
        return {
            "entity_id": entity_id,
            "as_of_date": as_of_date.isoformat(),
            "accounts_by_type": grouped_accounts,
            "accounts": accounts,  # Flat list for backward compatibility
            "totals": {
                "total_debits": float(total_debits),
                "total_credits": float(total_credits)
            }
        }


    # ------------------------------------------------------------------
    # Three-statement reporting (A-7, 2026-07-26)
    # ------------------------------------------------------------------
    #
    # basis='posted'  → POSTED JEs only (the approved books — default)
    # basis='all'     → POSTED + DRAFT (the "if I approved everything" view
    #                   while a period is mid-review). VOID never counts.
    #
    # POL-25: functional currency comes from finance_entities.base_currency
    # (SG=SGD, AU=AUD, Ventures=SGD).
    def _functional(self, db: Session, entity_id: int) -> str:
        from src.models.entity import FinanceEntity
        e = db.get(FinanceEntity, entity_id)
        return e.base_currency if e else "SGD"

    _PNL_TYPES = (AccountType.REVENUE, AccountType.COST_OF_SALES, AccountType.EXPENSE)

    def _statuses(self, basis: str) -> list:
        return ([JournalEntryStatus.POSTED, JournalEntryStatus.DRAFT]
                if basis == "all" else [JournalEntryStatus.POSTED])

    def _account_nets(
        self,
        db: Session,
        entity_id: int,
        date_from: Optional[date],
        date_to: date,
        basis: str,
        account_types: Optional[tuple] = None,
    ) -> list[dict]:
        """Per-account (debit−credit) sums over the window, with account meta."""
        q = (
            db.query(
                FinanceJournalLine.account_code,
                FinanceAccount.name.label("account_name"),
                FinanceAccount.account_type,
                FinanceAccount.category,
                FinanceAccount.sub_category,
                func.sum(FinanceJournalLine.debit_amount).label("dr"),
                func.sum(FinanceJournalLine.credit_amount).label("cr"),
            )
            .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
            .join(FinanceAccount, and_(
                FinanceJournalLine.account_code == FinanceAccount.code,
                or_(FinanceAccount.entity_id == entity_id, FinanceAccount.entity_id == None),
            ))
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalEntry.status.in_(self._statuses(basis)))
            .filter(FinanceJournalEntry.entry_date <= date_to)
        )
        if date_from is not None:
            q = q.filter(FinanceJournalEntry.entry_date >= date_from)
        if account_types:
            q = q.filter(FinanceAccount.account_type.in_(account_types))
        rows = (q.group_by(FinanceJournalLine.account_code, FinanceAccount.name,
                           FinanceAccount.account_type, FinanceAccount.category,
                           FinanceAccount.sub_category)
                 .order_by(FinanceJournalLine.account_code).all())
        return [
            {
                "account_code": r.account_code,
                "account_name": r.account_name,
                "account_type": r.account_type.value,
                "category": r.category,
                "sub_category": r.sub_category,
                "net_debit": (r.dr or Decimal("0")) - (r.cr or Decimal("0")),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Hierarchical rendering: category is the SECTION selector; within a
    # section, lines group by sub_category (subtotalled) and order by GL code.
    # All subtotals/totals are computed here at runtime off the ledger nets.
    # ------------------------------------------------------------------
    @staticmethod
    def _group_lines(rows: list[dict], credit_normal: bool) -> dict:
        """Group a set of account nets by sub_category → account, code-ordered,
        with a subtotal per sub_category and a section total. Signs: credit_normal
        sections (revenue/liability/equity) flip net_debit; others keep it."""
        from collections import OrderedDict
        buckets: "OrderedDict[str, list]" = OrderedDict()
        total = Decimal("0")
        for n in sorted(rows, key=lambda x: x["account_code"]):
            amt = -n["net_debit"] if credit_normal else n["net_debit"]
            if amt == 0:
                continue
            sub = n.get("sub_category") or n.get("category") or "Uncategorised"
            buckets.setdefault(sub, []).append({
                "account_code": n["account_code"], "account_name": n["account_name"],
                "category": n["category"], "sub_category": n["sub_category"],
                "amount": float(amt)})
            total += amt
        groups = []
        for sub, lines in buckets.items():
            sub_tot = sum((Decimal(str(l["amount"])) for l in lines), Decimal("0"))
            groups.append({"sub_category": sub, "lines": lines,
                           "subtotal": float(sub_tot),
                           "_mincode": lines[0]["account_code"]})
        groups.sort(key=lambda g: g["_mincode"])
        for g in groups:
            g.pop("_mincode", None)
        return {"groups": groups, "total": float(total)}

    def get_profit_and_loss(
        self, db: Session, entity_id: int, date_from: date, date_to: date,
        basis: str = "posted",
    ) -> dict:
        """Multi-step P&L for the period, grouped category → sub_category → account:

            Revenue − Cost of Sales           = Gross Profit
            Gross Profit − Operating Expenses = Operating Income
            + Other Income − Other Expense    = Profit Before Tax
            − Tax                             = Net Income

        Sections are selected by account CATEGORY (Revenue / Other Income /
        Cost of Sales / Operating Expenses / Other Expense / Tax); lines within
        each roll up by sub_category. All subtotals computed at runtime.
        """
        nets = self._account_nets(db, entity_id, date_from, date_to, basis,
                                  account_types=self._PNL_TYPES)

        def by_cat(category: str, credit_normal: bool) -> dict:
            return self._group_lines(
                [n for n in nets if n["category"] == category], credit_normal)

        revenue = by_cat("Revenue", credit_normal=True)
        other_income = by_cat("Other Income", credit_normal=True)
        cogs = by_cat("Cost of Sales", credit_normal=False)
        opex = by_cat("Operating Expenses", credit_normal=False)
        other_expense = by_cat("Other Expense", credit_normal=False)
        tax = by_cat("Tax", credit_normal=False)

        gross = Decimal(str(revenue["total"])) - Decimal(str(cogs["total"]))
        operating_income = gross - Decimal(str(opex["total"]))
        pbt = operating_income + Decimal(str(other_income["total"])) - Decimal(str(other_expense["total"]))
        net = pbt - Decimal(str(tax["total"]))
        return {
            "entity_id": entity_id,
            "currency": self._functional(db, entity_id),
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "basis": basis,
            "revenue": revenue,
            "cost_of_sales": cogs,
            "gross_profit": float(gross),
            "operating_expenses": opex,
            "operating_income": float(operating_income),
            "other_income": other_income,
            "other_expense": other_expense,
            "profit_before_tax": float(pbt),
            "tax": tax,
            "net_income": float(net),
        }

    def get_balance_sheet(
        self, db: Session, entity_id: int, as_of: date, basis: str = "posted",
    ) -> dict:
        """Balance sheet as at a date. All P&L accounts since inception roll
        into a single 'Retained earnings (system)' equity line, so the sheet
        balances by construction — any residual imbalance is a data defect and
        is surfaced, never hidden."""
        nets = self._account_nets(db, entity_id, None, as_of, basis)

        def by_type(t: AccountType, debit_normal: bool) -> dict:
            return self._group_lines(
                [n for n in nets if n["account_type"] == t.value], credit_normal=not debit_normal)

        assets = by_type(AccountType.ASSET, debit_normal=True)
        liabilities = by_type(AccountType.LIABILITY, debit_normal=False)
        equity = by_type(AccountType.EQUITY, debit_normal=False)
        retained = sum(
            (-n["net_debit"] for n in nets
             if n["account_type"] in (t.value for t in self._PNL_TYPES)),
            Decimal("0"))
        equity["retained_earnings_system"] = float(retained)
        equity["total_with_retained"] = float(Decimal(str(equity["total"])) + retained)
        imbalance = Decimal(str(assets["total"])) - (
            Decimal(str(liabilities["total"])) + Decimal(str(equity["total"])) + retained)
        return {
            "entity_id": entity_id,
            "currency": self._functional(db, entity_id),
            "as_of": as_of.isoformat(), "basis": basis,
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "balanced": imbalance == 0,
            "imbalance": float(imbalance),
        }

    def _cash_codes(self, db: Session, entity_id: int) -> set[str]:
        from src.models.bank_account import FinanceBankAccount
        rows = (db.query(FinanceBankAccount.coa_account_code)
                  .filter(FinanceBankAccount.entity_id == entity_id,
                          FinanceBankAccount.coa_account_code != None).all())
        return {r[0] for r in rows}

    def get_cash_flow(
        self, db: Session, entity_id: int, date_from: date, date_to: date,
        basis: str = "posted",
    ) -> dict:
        """Direct-method cash flow: every JE touching a cash (bank) account
        contributes its cash delta, bucketed by the dominant counter-account:
        REVENUE → operating in; EXPENSE/COGS → operating out; non-cash ASSET →
        investing; LIABILITY/EQUITY → financing; Intercompany → intercompany;
        cash↔cash only → internal transfers (nets to zero consolidated)."""
        cash_codes = self._cash_codes(db, entity_id)
        statuses = self._statuses(basis)

        lines = (
            db.query(
                FinanceJournalLine.entry_id,
                FinanceJournalLine.account_code,
                FinanceJournalLine.debit_amount,
                FinanceJournalLine.credit_amount,
                FinanceAccount.account_type,
                FinanceAccount.category,
                FinanceAccount.name.label("account_name"),
                FinanceJournalEntry.source.label("je_source"),
            )
            .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
            .join(FinanceAccount, and_(
                FinanceJournalLine.account_code == FinanceAccount.code,
                or_(FinanceAccount.entity_id == entity_id, FinanceAccount.entity_id == None),
            ))
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalEntry.status.in_(statuses))
            .filter(FinanceJournalEntry.entry_date >= date_from)
            .filter(FinanceJournalEntry.entry_date <= date_to)
            .all()
        )

        by_entry: dict[int, list] = {}
        for l in lines:
            by_entry.setdefault(l.entry_id, []).append(l)

        buckets: dict[str, dict[str, Decimal]] = {
            "operating_in": {}, "operating_out": {}, "investing": {},
            "financing": {}, "intercompany": {}, "internal_transfers": {},
        }

        # Working-capital law (Gaurav, 2026-07-27): operating liabilities
        # (deferred revenue, deposits held, payables, payroll/GST) and current
        # assets (receivables, deposits paid, clearing) are OPERATING flows —
        # financing is reserved for true financing (loans 2400, equity), and
        # investing for long-term assets (15xx) + loans made to others (1320).
        # 2405 Related-Party / Director Loans added 2026-08-16 (Gaurav, FY2019 review):
        # director loans are TRUE financing, not working capital — the account postdates
        # the working-capital law and was falling through to operating.
        FINANCING_LIABILITY_CODES = {"2400", "2405"}
        INVESTING_ASSET_CODES = {"1320"}

        def bucket_for(counter, cash_delta: Decimal) -> str:
            if counter.category == "Intercompany":
                return "intercompany"
            t = counter.account_type
            if t == AccountType.REVENUE:
                return "operating_in"
            if t in (AccountType.EXPENSE, AccountType.COST_OF_SALES):
                return "operating_out"
            code = counter.account_code or ""
            if t == AccountType.ASSET:
                if code.startswith("15") or code in INVESTING_ASSET_CODES:
                    return "investing"
                return "operating_in" if cash_delta > 0 else "operating_out"
            if t == AccountType.LIABILITY and code not in FINANCING_LIABILITY_CODES:
                return "operating_in" if cash_delta > 0 else "operating_out"
            return "financing"  # loans / EQUITY

        net_change = Decimal("0")
        opening_je_cash = Decimal("0")
        for entry_lines in by_entry.values():
            cash_delta = sum(
                ((l.debit_amount or Decimal("0")) - (l.credit_amount or Decimal("0"))
                 for l in entry_lines if l.account_code in cash_codes),
                Decimal("0"))
            if cash_delta == 0:
                continue
            # POL-28 conversion balances ARE the opening cash, not a financing
            # inflow of the period — surface them in opening_cash instead.
            if entry_lines[0].je_source == "opening_balance":
                opening_je_cash += cash_delta
                continue
            net_change += cash_delta
            counters = [l for l in entry_lines if l.account_code not in cash_codes]
            if not counters:
                key = "internal transfers between own accounts"
                buckets["internal_transfers"][key] = (
                    buckets["internal_transfers"].get(key, Decimal("0")) + cash_delta)
                continue
            dominant = max(
                counters,
                key=lambda l: abs((l.debit_amount or Decimal("0")) - (l.credit_amount or Decimal("0"))))
            b = bucket_for(dominant, cash_delta)
            label = f"{dominant.account_code} {dominant.account_name}"
            buckets[b][label] = buckets[b].get(label, Decimal("0")) + cash_delta

        opening = Decimal("0")
        opening_rows = (
            db.query(func.sum(FinanceJournalLine.debit_amount - FinanceJournalLine.credit_amount))
            .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalLine.account_code.in_(cash_codes) if cash_codes else False)
            .filter(FinanceJournalEntry.status.in_(statuses))
            .filter(FinanceJournalEntry.entry_date < date_from)
            .scalar())
        opening = (opening_rows or Decimal("0")) + opening_je_cash

        def render(b: dict[str, Decimal]):
            items = [{"label": k, "amount": float(v)} for k, v in b.items() if v != 0]
            items.sort(key=lambda x: -abs(x["amount"]))
            return {"lines": items, "total": float(sum(b.values(), Decimal("0")))}

        return {
            "entity_id": entity_id,
            "currency": self._functional(db, entity_id),
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "basis": basis,
            "opening_cash": float(opening),
            "buckets": {k: render(v) for k, v in buckets.items()},
            "net_change_in_cash": float(net_change),
            "closing_cash": float(opening + net_change),
        }

    def get_account_ledger(
        self, db: Session, entity_id: int, account_code: str,
        date_from: date, date_to: date, basis: str = "posted",
    ) -> dict:
        """The account register: one COA account in one entity's book —
        opening balance, every composing journal line with a running balance,
        closing balance. The drill-down behind every report number."""
        statuses = self._statuses(basis)

        account = (db.query(FinanceAccount)
                     .filter(FinanceAccount.code == account_code,
                             or_(FinanceAccount.entity_id == entity_id,
                                 FinanceAccount.entity_id == None))
                     .order_by(FinanceAccount.entity_id.desc().nulls_last())
                     .first())

        opening = (db.query(func.coalesce(func.sum(
                        FinanceJournalLine.debit_amount - FinanceJournalLine.credit_amount), 0))
                     .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
                     .filter(FinanceJournalLine.entity_id == entity_id,
                             FinanceJournalLine.account_code == account_code,
                             FinanceJournalEntry.status.in_(statuses),
                             FinanceJournalEntry.entry_date < date_from)
                     .scalar()) or Decimal("0")

        from src.models.transaction import FinanceTransaction
        rows = (db.query(FinanceJournalLine, FinanceJournalEntry)
                  .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
                  .filter(FinanceJournalLine.entity_id == entity_id,
                          FinanceJournalLine.account_code == account_code,
                          FinanceJournalEntry.status.in_(statuses),
                          FinanceJournalEntry.entry_date >= date_from,
                          FinanceJournalEntry.entry_date <= date_to)
                  .order_by(FinanceJournalEntry.entry_date,
                            # opening JEs lead their day — running balance must
                            # start FROM the opening, not dip through it
                            (FinanceJournalEntry.source != 'opening_balance'),
                            FinanceJournalEntry.id,
                            FinanceJournalLine.id)
                  .all())

        # one bank txn may link each JE — map je_id → txn id for click-through
        je_ids = list({je.id for _, je in rows})
        txn_by_je: dict[int, int] = {}
        if je_ids:
            for t in (db.query(FinanceTransaction.id,
                               FinanceTransaction.reconciled_journal_entry_id)
                        .filter(FinanceTransaction.reconciled_journal_entry_id.in_(je_ids)).all()):
                txn_by_je.setdefault(t.reconciled_journal_entry_id, t.id)

        running = opening
        lines = []
        for line, je in rows:
            delta = (line.debit_amount or Decimal("0")) - (line.credit_amount or Decimal("0"))
            running += delta
            lines.append({
                "date": je.entry_date.isoformat(),
                "journal_entry_id": je.id,
                "je_status": je.status.value,
                "je_source": je.source,
                "description": line.description or je.description,
                "debit": float(line.debit_amount or 0),
                "credit": float(line.credit_amount or 0),
                "running_balance": float(running),
                "currency": line.currency,
                "native_amount": float(line.native_amount) if line.native_amount is not None else None,
                "fx_rate": float(line.fx_rate) if line.fx_rate is not None else None,
                "transaction_id": txn_by_je.get(je.id),
            })

        return {
            "entity_id": entity_id,
            "account_code": account_code,
            "account_name": account.name if account else None,
            "account_type": account.account_type.value if account else None,
            "functional_currency": self._functional(db, entity_id),
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "basis": basis,
            "opening_balance": float(opening),
            "lines": lines,
            "closing_balance": float(running),
        }

    def get_bank_recon(self, db: Session) -> dict[int, dict]:
        """A-10 recon checkpoint: per bank account, the identity

            statement balance @ watermark =
                posted ledger + draft JEs + unbooked txns + RESIDUAL

        computed AS AT each account's own watermark (the date of its newest
        imported line carrying a running balance) — never "now". Foreign-
        currency accounts (account currency != entity functional) compare on
        the NATIVE side via journal-line native_amounts. residual != 0 is a
        real defect in already-imported data; a stale watermark is merely a
        freshness problem. Returns {} entry (None) for accounts with no
        statement-balance feed (Stripe platform/Connect).
        """
        from sqlalchemy import text as _text
        from src.models.bank_account import FinanceBankAccount
        from src.models.entity import FinanceEntity

        marks = {r[0]: (r[1], r[2]) for r in db.execute(_text("""
            SELECT bank_account_id, running_balance, transaction_date FROM (
                SELECT bank_account_id, running_balance, transaction_date,
                       ROW_NUMBER() OVER (PARTITION BY bank_account_id
                                          ORDER BY transaction_date DESC, id DESC) AS rn
                FROM finance_transactions WHERE running_balance IS NOT NULL
            ) ranked WHERE rn = 1"""))}

        functional = {e.id: e.base_currency for e in db.query(FinanceEntity).all()}
        out: dict[int, dict] = {}
        for ba in db.query(FinanceBankAccount).all():
            mark = marks.get(ba.id)
            balance_source = "statement"
            if not ba.coa_account_code:
                out[ba.id] = None
                continue
            if not mark:
                # Stamp fallback (Gaurav, 2026-07-27): accounts with no running-
                # balance feed but a provider-stamped balance (Stripe Platform's
                # ClickHouse sum(net); DBS C/F on zero-txn currencies) reconcile
                # against the stamp. Same identity, different statement source.
                state = ba.api_sync_state or {}
                if state.get("latest_balance") is None or not state.get("balance_as_of"):
                    out[ba.id] = None
                    continue
                mark = (state["latest_balance"], date.fromisoformat(state["balance_as_of"]))
                balance_source = "provider_stamp"
            stmt_balance, watermark = Decimal(mark[0]), mark[1]
            native_basis = ba.currency != functional.get(ba.entity_id)

            if native_basis:
                ledger_expr = ("COALESCE(SUM(CASE WHEN l.debit_amount > 0 "
                               "THEN l.native_amount ELSE -l.native_amount END), 0)")
            else:
                ledger_expr = "COALESCE(SUM(l.debit_amount - l.credit_amount), 0)"

            def _je_sum(status: str) -> Decimal:
                # An unpaired transfer JE (claimed on the OTHER side, waiting)
                # pre-books this account's line while THIS account's txn is
                # still unbooked — excluding those lines prevents the double
                # count; once pairing completes the exclusion no longer applies.
                return Decimal(db.execute(_text(f"""
                    SELECT {ledger_expr}
                    FROM finance_journal_lines l
                    JOIN finance_journal_entries je ON je.id = l.entry_id
                    WHERE l.account_code = :coa AND l.entity_id = :ent
                      AND je.status = :st AND je.entry_date <= :w
                      AND NOT EXISTS (
                          SELECT 1 FROM finance_transactions t
                          WHERE t.reconciled_journal_entry_id = je.id
                            AND t.status = 'AWAITING_MATCH'
                            AND t.bank_account_id != :ba)"""),
                    {"coa": ba.coa_account_code, "ent": ba.entity_id,
                     "st": status, "w": watermark, "ba": ba.id}).scalar() or 0)

            posted = _je_sum("POSTED")
            drafts = _je_sum("DRAFT")
            # Claim-only AWAITING waiters have no JE yet — their cash effect is
            # still unbooked; rule-claimed waiters' JEs already count in drafts.
            # Lower bound = the books' opening date (POL-28): rows dated before
            # 2026-01-01 are already inside the opening JE (e.g. the all-history
            # Stripe payout pull) — counting them again double-counts history.
            unbooked = Decimal(db.execute(_text("""
                SELECT COALESCE(SUM(amount), 0) FROM finance_transactions
                WHERE bank_account_id = :ba AND transaction_date <= :w
                  AND transaction_date >= '2026-01-01'
                  AND (status IN ('IMPORTED', 'PENDING', 'NEEDS_REVIEW')
                       OR (status = 'AWAITING_MATCH' AND reconciled_journal_entry_id IS NULL))"""),
                {"ba": ba.id, "w": watermark}).scalar() or 0)

            # Staged-but-unprojected economic events that would hit this
            # account's COA (Stripe Platform's activity side): their cash
            # effect is real at Stripe but not yet in the ledger — a distinct
            # bucket so the recon reads "reviewed → will book", not "missing".
            staged = Decimal(db.execute(_text("""
                SELECT COALESCE(SUM(CASE WHEN t.debit_code = :coa THEN e.amount
                                         ELSE -e.amount END), 0)
                FROM finance_economic_events e
                JOIN finance_je_templates t
                  ON t.entity_id = e.entity_id AND t.event_type = e.event_type
                 AND t.is_active
                WHERE e.entity_id = :ent AND e.status = 'STAGED'
                  AND (t.debit_code = :coa OR t.credit_code = :coa)
                  AND e.period <= :w"""),
                {"coa": ba.coa_account_code, "ent": ba.entity_id,
                 "w": watermark}).scalar() or 0)

            residual = stmt_balance - (posted + drafts + unbooked + staged)
            out[ba.id] = {
                "watermark": watermark.isoformat(),
                "statement_balance": float(stmt_balance),
                "posted": float(posted),
                "drafts": float(drafts),
                "unbooked": float(unbooked),
                "staged": float(staged),
                "residual": float(residual),
                "basis": "native" if native_basis else "functional",
                "currency": ba.currency,
                "balance_source": balance_source,
            }
        return out

    def get_consolidated(
        self, db: Session, report: str, date_from: Optional[date], date_to: date,
        basis: str = "posted", sgd_usd_rate: float = 0.74, aud_usd_rate: float = 0.62,
    ) -> dict:
        """Consolidation is ALWAYS presented in USD (Gaurav, 2026-07-26):
        each entity's functional-currency report is translated at the caller-
        provided rates (v1 manual, no FX reval). Intercompany-category accounts
        are eliminated; the elimination residual is reported, never absorbed."""
        from src.models.entity import FinanceEntity
        entities = db.query(FinanceEntity).order_by(FinanceEntity.id).all()

        per_entity = {}
        for e in entities:
            if report == "pnl":
                per_entity[e.id] = self.get_profit_and_loss(db, e.id, date_from, date_to, basis)
            elif report == "balance_sheet":
                per_entity[e.id] = self.get_balance_sheet(db, e.id, date_to, basis)
            else:
                per_entity[e.id] = self.get_cash_flow(db, e.id, date_from, date_to, basis)

        def rate_for(eid: int) -> Decimal:
            ccy = self._functional(db, eid)
            return Decimal(str(aud_usd_rate)) if ccy == "AUD" else Decimal(str(sgd_usd_rate))

        # Merge across entities. Grouped sections (P&L/BS) merge by account code
        # and re-group by sub_category; cash-flow buckets stay flat by label.
        # Intercompany-category lines split out for elimination.
        merged_accts: dict[str, dict] = {}   # path -> code -> {name,cat,sub,amount}
        merged_flat: dict[str, dict] = {}    # path -> label -> amount (cash flow)
        ic_lines: dict[str, Decimal] = {}

        def merge_grouped(path: str, section: dict, rate: Decimal):
            tgt = merged_accts.setdefault(path, {})
            for grp in section.get("groups", []):
                for line in grp.get("lines", []):
                    amt = Decimal(str(line["amount"])) * rate
                    if line.get("category") == "Intercompany":
                        label = f"{line['account_code']} {line['account_name']}".strip()
                        ic_lines[label] = ic_lines.get(label, Decimal("0")) + amt
                        continue
                    code = line["account_code"]
                    a = tgt.setdefault(code, {"account_code": code,
                                              "account_name": line["account_name"],
                                              "category": line["category"],
                                              "sub_category": line["sub_category"],
                                              "amount": Decimal("0")})
                    a["amount"] += amt

        def merge_flat(path: str, section: dict, rate: Decimal):
            tgt = merged_flat.setdefault(path, {})
            for line in section.get("lines", []):
                label = line.get("label") or f"{line.get('account_code','')} {line.get('account_name','')}".strip()
                tgt[label] = tgt.get(label, Decimal("0")) + Decimal(str(line["amount"])) * rate

        GROUPED = {"revenue", "cost_of_sales", "operating_expenses", "other_income",
                   "other_expense", "tax", "assets", "liabilities", "equity"}
        for eid, rep in per_entity.items():
            rate = rate_for(eid)
            for key, val in rep.items():
                if key in GROUPED and isinstance(val, dict) and "groups" in val:
                    merge_grouped(key, val, rate)
                elif key == "buckets":
                    for bname, bval in val.items():
                        merge_flat(f"buckets.{bname}", bval, rate)

        def render_grouped(path: str) -> dict:
            accts = merged_accts.get(path, {})
            # re-use the sub_category grouping on already-signed display amounts
            rows = [{"account_code": a["account_code"], "account_name": a["account_name"],
                     "category": a["category"], "sub_category": a["sub_category"],
                     "net_debit": a["amount"]} for a in accts.values()]
            return self._group_lines(rows, credit_normal=False)

        def render_flat(path: str) -> dict:
            m = merged_flat.get(path, {})
            items = [{"label": k, "amount": float(v)} for k, v in m.items() if v != 0]
            items.sort(key=lambda x: -abs(x["amount"]))
            return {"lines": items, "total": float(sum(m.values(), Decimal("0")))}

        ic_residual = float(sum(ic_lines.values(), Decimal("0")))
        out: dict = {
            "report": report, "basis": basis, "currency": "USD",
            "sgd_usd_rate": sgd_usd_rate, "aud_usd_rate": aud_usd_rate,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat(),
            "entities": {str(k): v for k, v in per_entity.items()},
            "eliminations": {
                "lines": sorted(
                    ({"label": k, "amount": float(v)} for k, v in ic_lines.items() if v != 0),
                    key=lambda x: -abs(x["amount"])),
                "residual": ic_residual,
            },
        }
        if report == "pnl":
            c = {k: render_grouped(k) for k in
                 ("revenue", "cost_of_sales", "operating_expenses",
                  "other_income", "other_expense", "tax")}
            c["gross_profit"] = c["revenue"]["total"] - c["cost_of_sales"]["total"]
            c["operating_income"] = c["gross_profit"] - c["operating_expenses"]["total"]
            c["profit_before_tax"] = (c["operating_income"] + c["other_income"]["total"]
                                      - c["other_expense"]["total"])
            c["net_income"] = c["profit_before_tax"] - c["tax"]["total"]
            out["consolidated"] = c
        elif report == "balance_sheet":
            retained = float(sum(
                (Decimal(str(rep["equity"]["retained_earnings_system"])) * rate_for(eid)
                 for eid, rep in per_entity.items()), Decimal("0")))
            eq = render_grouped("equity")
            eq["retained_earnings_system"] = retained
            eq["total_with_retained"] = eq["total"] + retained
            out["consolidated"] = {
                "assets": render_grouped("assets"),
                "liabilities": render_grouped("liabilities"),
                "equity": eq,
                "retained_earnings_system": retained,
            }
        else:
            render = render_flat  # cash flow keeps flat buckets
            out["consolidated"] = {
                "buckets": {b: render(f"buckets.{b}") for b in
                            ("operating_in", "operating_out", "investing",
                             "financing", "internal_transfers")},
                "opening_cash": float(sum(
                    (Decimal(str(rep["opening_cash"])) * rate_for(eid)
                     for eid, rep in per_entity.items()), Decimal("0"))),
                "net_change_in_cash": float(sum(
                    (Decimal(str(rep["net_change_in_cash"])) * rate_for(eid)
                     for eid, rep in per_entity.items()), Decimal("0"))),
            }
        return out


    def _bank_coa_codes(self, db: Session) -> list[str]:
        """Bank-account COA codes from finance_bank_accounts — the single source of truth for
        'this leg is cash' (shared with the GST engine's bank-leg gate, POL-123)."""
        from src.models.bank_account import FinanceBankAccount
        return [r[0] for r in db.query(FinanceBankAccount.coa_account_code)
                .filter(FinanceBankAccount.coa_account_code.isnot(None)).distinct().all()]

    def get_bas(
        self,
        db: Session,
        entity_id: int,
        date_from: date,
        date_to: date,
        basis: str = "posted",
    ) -> dict:
        """Australian Business Activity Statement (BAS) — GST + PAYG withholding.

        Cash-basis two-account GST model: output GST accrues to 2500 (GST Payable),
        input GST to 1350 (GST Receivable), only when cash moves. BAS labels map
        directly to the ATO Activity Statement:
          1A = GST on sales      = net credit movement on 2500 over the period
          1B = GST on purchases  = net debit movement on 1350 over the period
          G1 = total sales (GST-inclusive) = 1A * 11 (10% GST -> 1/11 of gross)
          box7 = net GST         = 1A - 1B
          W1 = total wages       = debit movement on wage COAs
          W2 = amount withheld   = credit movement on 2301 (PAYG Withholding Payable)
          8A = owed to ATO       = 1A + W2
          8B = ATO credits       = 1B
          box9 = net amount      = 8A - 8B
        """
        WAGE_CODES = ["6000", "6003", "5061", "5063"]

        # A period BAS measures GST on the period's SALES and PURCHASES only.
        # ATO/BAS settlements (paying or receiving the net GST) move the GST control
        # account directly against a bank account — they settle the liability, they are
        # not trading GST. Any JE that touches both a GST control account and a bank
        # account is a settlement, so exclude those lines from 1A/1B.
        # re-review F10: the real bank set comes from finance_bank_accounts (same source as the
        # GST engine's bank-leg gate), not a hard-coded 10xx range.
        bank_codes = self._bank_coa_codes(db)
        settlement_entries = (
            db.query(FinanceJournalLine.entry_id)
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalLine.account_code.in_(bank_codes))
        ).subquery()

        def movement(codes: list[str], side: str) -> float:
            """Net (debit-credit) or (credit-debit) trading GST movement for codes."""
            q = (
                db.query(
                    func.coalesce(func.sum(FinanceJournalLine.debit_amount), 0).label("dr"),
                    func.coalesce(func.sum(FinanceJournalLine.credit_amount), 0).label("cr"),
                )
                .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
                .filter(FinanceJournalLine.entity_id == entity_id)
                .filter(FinanceJournalEntry.status.in_(self._statuses(basis)))
                .filter(FinanceJournalEntry.entry_date >= date_from)
                .filter(FinanceJournalEntry.entry_date <= date_to)
                .filter(FinanceJournalLine.account_code.in_(codes))
                .filter(FinanceJournalLine.entry_id.notin_(db.query(settlement_entries.c.entry_id)))
            )
            r = q.one()
            dr, cr = Decimal(str(r.dr or 0)), Decimal(str(r.cr or 0))
            return float(round(dr - cr if side == "dr" else cr - dr, 2))

        output_gst = movement(["2500"], "cr")   # 1A
        input_gst = movement(["1350"], "dr")     # 1B
        net_gst = round(output_gst - input_gst, 2)          # box 7
        g1 = round(output_gst * 11, 2)                      # total GST-inclusive sales
        wages = movement(WAGE_CODES, "dr")                  # W1
        withheld = movement(["2301"], "cr")                 # W2
        owed = round(output_gst + withheld, 2)              # 8A
        ato_credits = round(input_gst, 2)                   # 8B
        net_amount = round(owed - ato_credits, 2)           # box 9

        return {
            "entity_id": entity_id,
            "currency": self._functional(db, entity_id),
            "basis": basis,
            "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
            "gst": {
                "G1_total_sales": g1,
                "1A_gst_on_sales": output_gst,
                "1B_gst_on_purchases": input_gst,
                "7_net_gst": net_gst,
                "source": {
                    "1A": "credit movement on 2500 (GST Payable)",
                    "1B": "debit movement on 1350 (GST Receivable)",
                },
            },
            "payg_withholding": {
                "W1_total_wages": wages,
                "W2_amount_withheld": withheld,
                "source": {
                    "W1": "debit movement on wage COAs " + ", ".join(WAGE_CODES),
                    "W2": "credit movement on 2301 (PAYG Withholding Payable)",
                },
            },
            "summary": {
                "8A_owed_to_ato": owed,
                "8B_ato_credits": ato_credits,
                "9_net_amount": net_amount,
                "direction": "payable" if net_amount >= 0 else "refund",
            },
        }

    def get_bas_detail(
        self,
        db: Session,
        entity_id: int,
        date_from: date,
        date_to: date,
        box: str,
        basis: str = "posted",
    ) -> dict:
        """Per-transaction detail behind one BAS box — every contributing ledger line,
        each carrying its journal_entry_id for click-through. Self-sums to the box total.
          1A/G1 = 2500 credit lines · 1B = 1350 debit lines · W1 = wage-COA debit lines ·
          W2 = 2301 credit lines. Same settlement exclusion as get_bas (bank-touching JEs).
        """
        BOX = {
            "1A": (["2500"], "cr", 1), "G1": (["2500"], "cr", 11), "1B": (["1350"], "dr", 1),
            "W1": (["6000", "6003", "5061", "5063"], "dr", 1), "W2": (["2301"], "cr", 1),
        }
        if box not in BOX:
            raise ValueError(f"unknown BAS box '{box}' (expected one of {sorted(BOX)})")
        codes, side, mult = BOX[box]
        # re-review F10: the real bank set comes from finance_bank_accounts (same source as the
        # GST engine's bank-leg gate), not a hard-coded 10xx range.
        bank_codes = self._bank_coa_codes(db)
        settlement_entries = (
            db.query(FinanceJournalLine.entry_id)
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalLine.account_code.in_(bank_codes))
        ).subquery()
        q = (
            db.query(
                FinanceJournalEntry.entry_date, FinanceJournalEntry.id.label("je_id"),
                FinanceJournalEntry.source, FinanceJournalEntry.reference_number,
                FinanceJournalEntry.description, FinanceJournalLine.account_code,
                FinanceAccount.name.label("account_name"),
                FinanceJournalLine.debit_amount, FinanceJournalLine.credit_amount,
            )
            .join(FinanceJournalEntry, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
            .outerjoin(FinanceAccount, and_(
                FinanceJournalLine.account_code == FinanceAccount.code,
                or_(FinanceAccount.entity_id == entity_id, FinanceAccount.entity_id == None),
            ))
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalEntry.status.in_(self._statuses(basis)))
            .filter(FinanceJournalEntry.entry_date >= date_from)
            .filter(FinanceJournalEntry.entry_date <= date_to)
            .filter(FinanceJournalLine.account_code.in_(codes))
            .filter(FinanceJournalLine.entry_id.notin_(db.query(settlement_entries.c.entry_id)))
            .order_by(FinanceJournalEntry.entry_date, FinanceJournalEntry.id)
        )
        rows, total = [], Decimal("0")
        for r in q.all():
            dr, cr = Decimal(str(r.debit_amount or 0)), Decimal(str(r.credit_amount or 0))
            amt = (cr - dr) if side == "cr" else (dr - cr)
            if amt == 0:
                continue
            val = amt * mult
            total += val
            rows.append({
                "date": r.entry_date.isoformat(),
                "journal_entry_id": r.je_id,
                "reference": r.reference_number,
                "source": r.source,
                "description": r.description,
                "account_code": r.account_code,
                "account_name": r.account_name,
                "amount": float(round(val, 2)),
            })
        return {
            "box": box,
            "entity_id": entity_id,
            "currency": self._functional(db, entity_id),
            "basis": basis,
            "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
            "rows": rows,
            "count": len(rows),
            "total": float(round(total, 2)),
        }


# Singleton instance
report_service = ReportService()
