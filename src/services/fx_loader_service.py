"""FX rate loader — the recurring mechanism that keeps finance_fx_rates populated.

Origin of the problem (POL, 2026-08-16): the table was backfilled ONCE by hand from ECB/Frankfurter
(history through 2026-07) with no recurring job, so every new month was empty and foreign JEs refused
to post (POL-26). This service is the mechanism: it derives the REQUIRED currency pairs from live data
and pulls the month's rates from ECB/Frankfurter, both ways, idempotently.

Required pairs (Gaurav, 2026-08-16): for every ENTITY functional currency F (finance_entities.base_
currency) and every OPERATING currency O we actually use (salary currencies + bank-account currencies +
the entity currencies themselves), store F->O and O->F for the month. That guarantees any salary/bank
amount can always be converted to its entity's functional currency.

Source: ECB via the Frankfurter API (api.frankfurter.dev). ECB covers ~30 currencies; anything it does
NOT cover (e.g. BDT, PKR) is reported back as `unsupported` so finance enters those rates manually.
"""
from __future__ import annotations
import json
import logging
import urllib.request
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FRANKFURTER = "https://api.frankfurter.dev/v1"
_TIMEOUT = 15


class FxLoaderService:

    def required_currencies(self, db: Session) -> tuple[set[str], set[str]]:
        """(functional, operating). functional = distinct entity base currencies. operating = every
        currency we hold value in: salary currencies + bank-account currencies + the entity currencies."""
        func = {r[0] for r in db.execute(text(
            "SELECT DISTINCT base_currency FROM finance_entities WHERE base_currency IS NOT NULL")).all()}
        oper = set(func)
        oper |= {r[0] for r in db.execute(text(
            "SELECT DISTINCT currency FROM hr_compensation WHERE currency IS NOT NULL")).all()}
        oper |= {r[0] for r in db.execute(text(
            "SELECT DISTINCT currency FROM finance_bank_accounts WHERE currency IS NOT NULL")).all()}
        return func, oper

    def required_pairs(self, db: Session) -> set[tuple[str, str]]:
        """Every (from, to) that must exist: functional<->operating both ways (excluding identity)."""
        func, oper = self.required_currencies(db)
        pairs: set[tuple[str, str]] = set()
        for f in func:
            for o in oper:
                if o != f:
                    pairs.add((f, o))
                    pairs.add((o, f))
        return pairs

    def _fetch(self, base: str, symbols: list[str], on: str) -> tuple[dict, str]:
        """Fetch base->symbols from ECB/Frankfurter for date `on` (YYYY-MM-DD). Frankfurter returns the
        nearest ECB business day at or before `on`; we use the date it actually reports. Returns
        (rates_dict, rate_date)."""
        if not symbols:
            return {}, on
        from urllib.parse import urlencode
        url = f"{FRANKFURTER}/{on}?{urlencode({'base': base, 'symbols': ','.join(sorted(symbols))})}"
        req = urllib.request.Request(url, headers={"User-Agent": "drivelah-finance-api"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        return data.get("rates", {}) or {}, data.get("date", on)

    def _upsert(self, db: Session, ym: str, frm: str, to: str, rate: Decimal, source: str) -> None:
        # Atomic upsert on the uq_fx_rates_month_pair unique constraint — no check-then-act race between
        # the cron rerun and a manual /load.
        db.execute(text("""
            INSERT INTO finance_fx_rates (year_month, from_currency, to_currency, rate, source, created_at)
            VALUES (:ym, :f, :t, :r, :s, now())
            ON CONFLICT (year_month, from_currency, to_currency)
            DO UPDATE SET rate = EXCLUDED.rate, source = EXCLUDED.source
        """), {"ym": ym, "f": frm, "t": to, "r": rate, "s": source})

    def load_month(self, db: Session, year_month: str | None = None) -> dict:
        """Load/refresh all required pairs for `year_month` (YYYY-MM; default current month) from ECB.
        Stores F->O and O->F for every functional F, deriving one direction as the inverse of the other.
        Returns {month, rate_date, loaded, updated, unsupported, missing_after}."""
        if year_month is None:
            year_month = date.today().strftime("%Y-%m")
        on = f"{year_month}-01"
        func, oper = self.required_currencies(db)
        if not func:
            raise ValueError("No entity functional currencies found — cannot determine required rates.")

        loaded, unsupported, fetch_failed, rate_date = [], set(), [], on
        for f in sorted(func):
            targets = [o for o in oper if o != f]
            # ask ECB for f->targets; a network/upstream blip on ONE base must NOT abort the whole month —
            # isolate it, keep what the others loaded, and report the failed base distinctly.
            try:
                rates, rate_date = self._fetch(f, targets, on)
            except Exception as e:  # noqa: BLE001 — urllib/JSON/timeout are not SQLAlchemyError
                logger.warning("FX fetch failed for base=%s on %s: %s", f, on, e)
                fetch_failed.append(f)
                continue
            for o in targets:
                if o not in rates:
                    unsupported.add(o)
                    continue
                r_fo = Decimal(str(rates[o]))
                src = f"ECB/frankfurter {rate_date}"
                self._upsert(db, year_month, f, o, r_fo, src)                       # F->O
                self._upsert(db, year_month, o, f,
                             (Decimal("1") / r_fo).quantize(Decimal("0.000001"), ROUND_HALF_UP), src)  # O->F
                loaded.append(f"{f}->{o}"); loaded.append(f"{o}->{f}")
        db.commit()  # persist everything that succeeded (partial months are better than an empty one)

        # which required pairs are STILL missing after the load (the manual-entry worklist)
        have = {(r[0], r[1]) for r in db.execute(text(
            "SELECT from_currency, to_currency FROM finance_fx_rates WHERE year_month=:ym"),
            {"ym": year_month}).all()}
        missing = sorted({f"{a}->{b}" for (a, b) in self.required_pairs(db) if (a, b) not in have})
        return {
            "month": year_month, "rate_date": rate_date,
            "loaded_count": len(loaded), "loaded": sorted(set(loaded)),
            "unsupported": sorted(unsupported),
            "fetch_failed": sorted(fetch_failed),   # ECB bases that errored — retry needed, distinct from unsupported
            "missing_after": missing,
        }

    def upsert_manual(self, db: Session, year_month: str, frm: str, to: str, rate,
                      both_ways: bool = True) -> dict:
        """Manually set one rate (for currencies ECB does not cover, e.g. BDT/PKR). Stores the inverse
        too by default so 'both ways' holds. Stamped source='manual'."""
        r = Decimal(str(rate))
        if r <= 0:
            raise ValueError("rate must be positive")
        frm, to = frm.upper(), to.upper()
        self._upsert(db, year_month, frm, to, r, "manual")
        if both_ways and frm != to:
            self._upsert(db, year_month, to, frm,
                         (Decimal("1") / r).quantize(Decimal("0.000001"), ROUND_HALF_UP), "manual")
        db.commit()
        return {"month": year_month, "from": frm, "to": to, "rate": float(r), "both_ways": both_ways}

    def status(self, db: Session, year_month: str | None = None) -> dict:
        """Coverage report for a month: required pairs, which are present, which are missing."""
        if year_month is None:
            year_month = date.today().strftime("%Y-%m")
        required = self.required_pairs(db)
        have = {(r[0], r[1]) for r in db.execute(text(
            "SELECT from_currency, to_currency FROM finance_fx_rates WHERE year_month=:ym"),
            {"ym": year_month}).all()}
        present = sorted({f"{a}->{b}" for (a, b) in required if (a, b) in have})
        missing = sorted({f"{a}->{b}" for (a, b) in required if (a, b) not in have})
        return {"month": year_month, "required_count": len(required),
                "present": present, "missing": missing, "complete": not missing}


fx_loader_service = FxLoaderService()
