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
        entity = db.get(FinanceEntity, entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_id}")
        region = _region_for_entity(entity)
        period = period.replace(day=1)
        month_str = period.strftime("%Y-%m-%d")

        templates = (db.query(FinanceJETemplate)
                     .filter_by(entity_id=entity_id, is_active=True).all())
        staged, skipped_no_map, skipped_empty, mismatches = [], [], [], []

        for t in templates:
            spec = VIEW_MAP.get((region, t.event_type))
            if spec is None:
                skipped_no_map.append(t.event_type)
                continue
            row = self.ch.execute_single(
                f"SELECT round(sum({spec.amount_col}), 2) AS amount, count(*) AS n "
                f"FROM {spec.view} WHERE {spec.date_col} >= '{month_str}' "
                f"AND {spec.date_col} < ('{month_str}'::Date + INTERVAL 1 MONTH)"
            )
            amount = row.get("amount") if row else None
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
                "skipped_no_view_map": skipped_no_map}

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
                    # negative fact (discounts, refund-like rows) books flipped
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

    def import_payout_lines(self, db: Session, entity_id: int, period: date) -> dict[str, Any]:
        from src.services.transaction_service import transaction_service
        from src.services.csv_adapters.base import NormalizedRow

        entity = db.get(FinanceEntity, entity_id)
        if entity is None:
            raise ValueError(f"Unknown entity {entity_id}")
        region = _region_for_entity(entity)
        spec = PAYOUT_LINE_VIEWS[region]
        period = period.replace(day=1)
        month_str = period.strftime("%Y-%m-%d")

        ba = (db.query(FinanceBankAccount)
              .filter(FinanceBankAccount.entity_id == entity_id,
                      FinanceBankAccount.bank_name == "Stripe",
                      FinanceBankAccount.account_name.ilike("%platform%")).first())
        if ba is None or not ba.coa_account_code:
            raise ValueError(f"No Stripe Platform bank account (with COA code) for entity {entity_id}")

        rows = self.ch.execute_many(
            f"SELECT * FROM {spec.view} "
            f"WHERE {spec.date_col} >= '{month_str}' "
            f"AND {spec.date_col} < ('{month_str}'::Date + INTERVAL 1 MONTH) "
            f"ORDER BY {spec.date_col}"
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
            import_batch_id=f"stripe-payouts-{region}-{month_str}",
            source="stripe_payout_import", auto_categorize=False)
        return {"entity_id": entity_id, "period": month_str,
                "lines": len(normalized),
                "created": result.get("transactions_created"),
                "duplicates": result.get("duplicates_skipped")}


economic_event_service = EconomicEventService()
