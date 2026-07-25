"""Economic-event lane: stage (facts land) -> verify -> project (JEs post).

STAGE   reads each active template's ClickHouse view for one month and upserts a
        row in finance_economic_events (STAGED). Zero ledger effect; idempotent.
        Re-staging over a POSTED row with a different amount flags MISMATCH —
        never a silent re-post.
PROJECT (explicit, human-triggered) books each STAGED row into a balanced JE
        from the finance_je_templates registry (accrual route -> POSTED) and
        links event <-> JE. Negative amounts book with debit/credit flipped.
IMPORT  payout lines land as ordinary transactions on the Stripe Platform bank
        account (cash lane) — the categorization ladder pairs them with the
        bank's settlement lines. balance_transaction_id = dedup fingerprint.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.clients.clickhouse_client import ClickHouseClient
from src.models.bank_account import FinanceBankAccount
from src.models.economic_event import FinanceEconomicEvent, FinanceJETemplate
from src.models.entity import FinanceEntity
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.journal_line import FinanceJournalLine
from src.services.economic_events.view_map import PAYOUT_LINE_VIEWS, VIEW_MAP

logger = logging.getLogger(__name__)

EVENT_SOURCE = "clickhouse_views"
JE_SOURCE = "economic_events"


def _region_for_entity(entity: FinanceEntity) -> str:
    """SG/AU exists only at the ClickHouse boundary (view family selection)."""
    n = (entity.name or "").lower()
    if "australia" in n:
        return "AU"
    return "SG"


class EconomicEventService:

    def __init__(self, clickhouse: Optional[ClickHouseClient] = None) -> None:
        self._ch = clickhouse

    @property
    def ch(self) -> ClickHouseClient:
        if self._ch is None:
            self._ch = ClickHouseClient()
        return self._ch

    # ------------------------------------------------------------------
    # STAGE
    # ------------------------------------------------------------------

    def stage_month(self, db: Session, entity_id: int, period: date) -> dict[str, Any]:
        from src.models.sync_run import start_run, finish_run
        run = start_run(db, "clickhouse_stage", entity_id=entity_id,
                        window_from=period.replace(day=1), window_to=period.replace(day=1))
        try:
            result = self._stage_month_inner(db, entity_id, period)
        except Exception as e:
            finish_run(db, run, error=e)
            raise
        finish_run(db, run, fetched=len(result["staged"]),
                   error="; ".join(q["error"] for q in result["query_errors"]) or None
                   if result["query_errors"] else None)
        return result

    def _stage_month_inner(self, db: Session, entity_id: int, period: date) -> dict[str, Any]:
        entity = db.get(FinanceEntity, entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_id}")
        region = _region_for_entity(entity)
        period = period.replace(day=1)
        month_str = period.strftime("%Y-%m-%d")

        templates = (db.query(FinanceJETemplate)
                     .filter_by(entity_id=entity_id, is_active=True).all())
        staged, skipped_no_map, skipped_empty, mismatches = [], [], [], []
        query_errors: list[dict] = []

        for t in templates:
            spec = VIEW_MAP.get((region, t.event_type))
            if spec is None:
                skipped_no_map.append(t.event_type)
                continue
            try:
                row = self.ch.execute_single(
                    f"SELECT round(sum({spec.amount_col}), 2) AS total_amount, count(*) AS n "
                    f"FROM {spec.view} WHERE {spec.date_col} >= '{month_str}' "
                    f"AND {spec.date_col} < ('{month_str}'::Date + INTERVAL 1 MONTH)"
                )
            except Exception as query_err:
                # one broken view must not abort the month — surface it instead
                logger.error(f"stage query failed for {t.event_type} ({spec.view}): {query_err}")
                query_errors.append({"event_type": t.event_type, "view": spec.view,
                                     "error": str(query_err)[:200]})
                continue
            amount = row.get("total_amount") if row else None
            if amount in (None, 0, "0", ""):
                skipped_empty.append(t.event_type)
                continue
            amount = Decimal(str(amount))

            ev = (db.query(FinanceEconomicEvent)
                  .filter_by(source=EVENT_SOURCE, entity_id=entity_id,
                             event_type=t.event_type, period=period).first())
            payload = json.dumps({"view": spec.view, "row": {k: str(v) for k, v in (row or {}).items()}})
            if ev is None:
                ev = FinanceEconomicEvent(
                    source=EVENT_SOURCE, entity_id=entity_id, event_type=t.event_type,
                    period=period, amount=amount, currency=entity.base_currency,
                    payload=payload, status="STAGED")
                db.add(ev)
            elif ev.status == "POSTED":
                if Decimal(str(ev.amount)) != amount:
                    ev.status = "MISMATCH"
                    ev.payload = payload
                    mismatches.append({"event_type": t.event_type,
                                       "posted": str(ev.amount), "source_now": str(amount)})
                # matching amount on a POSTED row: nothing to do
                continue
            else:
                ev.amount = amount
                ev.payload = payload
                ev.status = "STAGED"
            staged.append({"event_type": t.event_type, "amount": str(amount)})

        db.commit()
        if skipped_no_map:
            logger.warning(f"stage_month {entity.name} {month_str}: no view map for {skipped_no_map}")
        return {"entity_id": entity_id, "period": month_str, "staged": staged,
                "mismatches": mismatches, "skipped_empty": skipped_empty,
                "skipped_no_view_map": skipped_no_map, "query_errors": query_errors}

    # ------------------------------------------------------------------
    # PROJECT
    # ------------------------------------------------------------------

    def project_month(self, db: Session, entity_id: int, period: date) -> dict[str, Any]:
        period = period.replace(day=1)
        events = (db.query(FinanceEconomicEvent)
                  .filter_by(entity_id=entity_id, period=period, status="STAGED").all())
        templates = {t.event_type: t for t in db.query(FinanceJETemplate)
                     .filter_by(entity_id=entity_id, is_active=True).all()}
        posted, errors = [], []
        try:
            for ev in events:
                t = templates.get(ev.event_type)
                if t is None:
                    errors.append({"event_type": ev.event_type, "error": "no active template"})
                    continue
                amount = Decimal(str(ev.amount))
                debit, credit = t.debit_code, t.credit_code
                if amount < 0:
                    # Sign policy lives on the template: outflow views (refunds,
                    # host transfers) report negative but the template already
                    # encodes direction -> book the magnitude. Only flip when a
                    # negative genuinely reverses meaning (e.g. discounts).
                    if t.flip_on_negative:
                        debit, credit = credit, debit
                    amount = -amount
                je = FinanceJournalEntry(
                    entity_id=entity_id, entry_date=ev.period,
                    description=f"[{ev.event_type}] {t.description} — {ev.period.strftime('%b %Y')}",
                    status=JournalEntryStatus.POSTED, source=JE_SOURCE,
                    posted_at=datetime.now(UTC),
                )
                db.add(je)
                db.flush()
                db.add(FinanceJournalLine(entry_id=je.id, entity_id=entity_id,
                                          account_code=debit, debit_amount=amount,
                                          credit_amount=Decimal("0"), description=je.description))
                db.add(FinanceJournalLine(entry_id=je.id, entity_id=entity_id,
                                          account_code=credit, debit_amount=Decimal("0"),
                                          credit_amount=amount, description=je.description))
                ev.journal_entry_id = je.id
                ev.status = "POSTED"
                ev.posted_at = datetime.now(UTC)
                posted.append({"event_type": ev.event_type, "journal_entry_id": je.id,
                               "amount": str(ev.amount)})
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"entity_id": entity_id, "period": period.isoformat(),
                "posted": posted, "errors": errors}

    # ------------------------------------------------------------------
    # PAYOUT-LINE IMPORT (cash lane)
    # ------------------------------------------------------------------

    def import_payout_lines(self, db: Session, entity_id: int,
                            period: Optional[date] = None) -> dict[str, Any]:
        from src.models.sync_run import start_run, finish_run
        run = start_run(db, "stripe_payouts", entity_id=entity_id)
        try:
            result = self._import_payout_lines_inner(db, entity_id, period)
        except Exception as e:
            finish_run(db, run, error=e)
            raise
        finish_run(db, run, fetched=result["lines"], created=result["created"],
                   duplicates=result["duplicates"])
        return result

    def _import_payout_lines_inner(self, db: Session, entity_id: int,
                                   period: Optional[date] = None) -> dict[str, Any]:
        """Wise-style sync: with no period, brings the account fully up to speed —
        first run pulls ALL payout lines; later runs pull from the latest imported
        line minus a 3-day overlap (balance_transaction_id dedup makes the
        overlap free). A period narrows to one month (kept for backfills)."""
        from src.services.transaction_service import transaction_service
        from src.services.csv_adapters.base import NormalizedRow

        entity = db.get(FinanceEntity, entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_id}")
        region = _region_for_entity(entity)
        spec = PAYOUT_LINE_VIEWS[region]

        ba = (db.query(FinanceBankAccount)
              .filter(FinanceBankAccount.entity_id == entity_id,
                      FinanceBankAccount.bank_name == "Stripe",
                      FinanceBankAccount.account_name.ilike("%platform%")).first())
        if ba is None or not ba.coa_account_code:
            raise ValueError(f"No Stripe Platform bank account (with COA code) for entity {entity_id}")

        if period is not None:
            start = period.replace(day=1)
            window = (f"WHERE {spec.date_col} >= '{start}' "
                      f"AND {spec.date_col} < ('{start}'::Date + INTERVAL 1 MONTH)")
            window_label = start.strftime("%Y-%m")
        else:
            from datetime import timedelta
            from sqlalchemy import func
            from src.models.transaction import FinanceTransaction
            last = (db.query(func.max(FinanceTransaction.transaction_date))
                    .filter(FinanceTransaction.bank_account_id == ba.id,
                            FinanceTransaction.source == "stripe_payout_import").scalar())
            if last:
                since = last - timedelta(days=3)   # overlap; dedup eats repeats
            else:
                # standard sync rule (Gaurav 2026-07-25): never-synced = max 90
                # days back. Historical one-go backfills are run operationally
                # with an explicit period, never by the button.
                since = date.today() - timedelta(days=90)
            window = f"WHERE {spec.date_col} >= '{since}'"
            window_label = f"since {since}"
        rows = self.ch.execute_many(
            f"SELECT * FROM {spec.view} {window} ORDER BY {spec.date_col}"
        )
        normalized = []
        for r in rows:
            amt = r.get(spec.amount_col)
            btx = r.get("balance_transaction_id")
            if amt in (None, "") or not btx:
                continue
            txn_date = str(r.get(spec.date_col))[:10]
            normalized.append(NormalizedRow(
                transaction_date=date.fromisoformat(txn_date),
                description=f"Stripe payout {btx}" + (f" — {r.get('description')}" if r.get("description") else ""),
                amount=Decimal(str(amt)),
                currency=entity.base_currency,
                source_id=btx,   # stable Stripe id -> fingerprint
            ))
        result = transaction_service.import_from_rows(
            db=db, bank_account=ba, normalized_rows=normalized,
            fingerprint_fn=lambda row: [row.source_id or ""],
            import_batch_id=f"stripe-payouts-{region}-{window_label}",
            source="stripe_payout_import", auto_categorize=False)

        # Consistency with other accounts (Gaurav 2026-07-25): stamp the coverage
        # watermark, and the TRUE Stripe balance (net sum of ALL balance txns —
        # payout lines alone can't tell it) so the account view shows it like
        # any bank account.
        bt_table = "sg_stripe_balance_transactions" if region == "SG" else "au_stripe_balance_transactions"
        try:
            bal_row = self.ch.execute_single(f"SELECT round(sum(net)/100, 2) AS bal FROM {bt_table}")
            bal = bal_row.get("bal") if bal_row else None
        except Exception:
            bal = None
        state = dict(ba.api_sync_state or {})
        state["last_synced_at"] = date.today().isoformat()
        if bal is not None:
            state["latest_balance"] = str(bal)
            state["balance_as_of"] = date.today().isoformat()
        ba.api_sync_state = state
        db.commit()
        return {"entity_id": entity_id, "window": window_label,
                "lines": len(normalized),
                "created": result.get("transactions_created"),
                "duplicates": result.get("duplicates_skipped")}


economic_event_service = EconomicEventService()
