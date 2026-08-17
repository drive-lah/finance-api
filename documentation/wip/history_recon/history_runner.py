"""Previous-years reconciliation harness (POL-124) — runner + invariant checker + HTML scorecard.

Thin orchestration around the EXISTING categorization engine. Draft-only: the engine books DRAFT JEs;
nothing posts here. Run on the CLONE for shadow years; prod posting happens later, supervised, after
the scorecard is approved (VR-1c).

Subcommands:
  run       --year YYYY --bank-account-ids 1,2,3 [--limit N]   shadow-run the engine on that year
  check     --year YYYY --bank-account-ids 1,2,3               month-end running-balance invariants
  scorecard --year YYYY --bank-account-ids 1,2,3 --out FILE    self-contained HTML scorecard

Usage: PYTHONPATH=. ./venv/bin/python documentation/wip/history_recon/history_runner.py <cmd> ...
"""
import argparse
import html
import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import text
from src.database import db_session


def d(x):
    return Decimal(str(x or 0))


def year_window(year: int):
    return date(year, 1, 1), date(year, 12, 31)


def target_txn_ids(db, year, ba_ids):
    y0, y1 = year_window(year)
    return [r[0] for r in db.execute(text("""
        SELECT id FROM finance_transactions
        WHERE bank_account_id = ANY(:ba) AND transaction_date BETWEEN :y0 AND :y1
          AND status IN ('IMPORTED','PENDING')
        ORDER BY transaction_date, id"""), {"ba": ba_ids, "y0": y0, "y1": y1}).fetchall()]


def cmd_run(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    import src.services.categorization_service as cs
    # POL-124: lift the POL-28 books-open floor ONLY inside this bounded run.
    cs.BOOKS_OPEN_DATE = date(1900, 1, 1)
    with db_session() as db:
        ids = target_txn_ids(db, args.year, ba_ids)
        print(f"year {args.year} accounts {ba_ids}: {len(ids)} txns to shadow-run (draft-only)")
        if not ids:
            return
        total = {"categorized": 0, "uncategorized": 0, "errors": 0}
        for i in range(0, len(ids), args.limit):
            chunk = ids[i:i + args.limit]
            summary = cs.categorization_service.run(db, txn_ids=chunk, limit=len(chunk))
            for k in total:
                total[k] += summary.get(k) or 0
            print(f"  chunk {i//args.limit+1}: {summary.get('categorized')} categorized, "
                  f"{summary.get('uncategorized')} uncategorized, {summary.get('errors')} errors")
        print("TOTAL:", total)


def month_ends(year):
    out = []
    for m in range(1, 13):
        nxt = date(year + (m == 12), (m % 12) + 1, 1)
        out.append(nxt - timedelta(days=1))
    return out


def gather_check(db, year, ba_ids):
    """Per account x month-end: statement running balance vs ledger (posted+draft) balance.
    Ledger side excludes opening/park JEs (they represent the SAME history being rebuilt)."""
    rows = []
    for ba in ba_ids:
        meta = db.execute(text("""
            SELECT ba.account_name, ba.coa_account_code, ba.entity_id, ba.currency, e.base_currency
            FROM finance_bank_accounts ba JOIN finance_entities e ON e.id=ba.entity_id
            WHERE ba.id=:ba"""), {"ba": ba}).fetchone()
        if not meta or not meta[1]:
            continue
        name, coa, ent, ccy, func = meta
        native = ccy != func
        ledger_expr = ("COALESCE(SUM(CASE WHEN l.debit_amount > 0 THEN l.native_amount ELSE -l.native_amount END),0)"
                       if native else "COALESCE(SUM(l.debit_amount - l.credit_amount),0)")
        for me in month_ends(year):
            rb = db.execute(text("""
                SELECT running_balance FROM finance_transactions
                WHERE bank_account_id=:ba AND transaction_date <= :d AND running_balance IS NOT NULL
                ORDER BY transaction_date DESC, id DESC LIMIT 1"""), {"ba": ba, "d": me}).scalar()
            led = db.execute(text(f"""
                SELECT {ledger_expr} FROM finance_journal_lines l
                JOIN finance_journal_entries je ON je.id=l.entry_id
                WHERE l.account_code=:coa AND l.entity_id=:ent AND je.entry_date <= :d
                  AND je.status IN ('POSTED','DRAFT')
                  AND je.source NOT IN ('opening_balance','opening_correction','pre_books_park','gst_h1_opening')
            """), {"coa": coa, "ent": ent, "d": me}).scalar()
            stmt = float(d(rb)) if rb is not None else None
            ledger = float(d(led))
            rows.append({"ba": ba, "name": name, "coa": coa, "ccy": ccy, "month_end": me.isoformat(),
                         "statement": stmt, "ledger": round(ledger, 2),
                         "diff": (round(stmt - ledger, 2) if stmt is not None else None)})
    return rows


def cmd_check(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    with db_session() as db:
        rows = gather_check(db, args.year, ba_ids)
        print(f"{'account':22} {'coa':5} {'month-end':11} {'statement':>13} {'ledger':>13} {'diff':>11}")
        for r in rows:
            if r["statement"] is None:
                continue
            flag = "  ⚠" if r["diff"] and abs(r["diff"]) > 0.02 else ""
            print(f"{r['name'][:22]:22} {r['coa']:5} {r['month_end']:11} {r['statement']:>13,.2f} "
                  f"{r['ledger']:>13,.2f} {r['diff']:>11,.2f}{flag}")


def cmd_apply_feedback(args):
    """Replay Gaurav's rulings from a feedback_resolutions_<year>.json onto THIS clone.
    CONFIG (rules/aliases/accounts/defaults) is idempotent and runs BEFORE the engine;
    RESOLUTIONS apply AFTER the engine run, only to txns still in NEEDS_REVIEW
    (rows the upgraded rules already caught are skipped and reported)."""
    import json as _json
    cfg = _json.load(open(args.file))
    with db_session() as db:
        conf = cfg.get("config", {})
        # rule updates (partial: only the keys present in the JSON change)
        for ru in conf.get("rules_update", []):
            sets = {k: v for k, v in ru.items() if k != "id"}
            assign = ", ".join(f"{k} = :{k}" for k in sets)
            db.execute(text(f"UPDATE finance_categorization_rules SET {assign}, updated_at = now() "
                            f"WHERE id = :id"), {**sets, "id": ru["id"]})
            print(f"  rule {ru['id']} updated ({', '.join(sets)})")
        # rule inserts (explicit ids; skipped when present)
        for ri in conf.get("rules_insert", []):
            exists = db.execute(text("SELECT 1 FROM finance_categorization_rules WHERE id=:id"),
                                {"id": ri["id"]}).scalar()
            if exists:
                print(f"  rule {ri['id']} already present — skipped")
                continue
            cols = ", ".join(ri)
            vals = ", ".join(f":{k}" for k in ri)
            db.execute(text(f"INSERT INTO finance_categorization_rules ({cols}, created_at, updated_at) "
                            f"VALUES ({vals}, now(), now())"), ri)
            print(f"  rule {ri['id']} inserted: {ri['name']}")
        db.execute(text("SELECT setval(pg_get_serial_sequence('finance_categorization_rules','id'), "
                        "(SELECT max(id) FROM finance_categorization_rules))"))
        for acc in conf.get("ensure_accounts", []):
            exists = db.execute(text("SELECT 1 FROM finance_accounts WHERE code=:code AND entity_id IS NULL"),
                                {"code": acc["code"]}).scalar()
            if not exists:
                db.execute(text(
                    "INSERT INTO finance_accounts (code, name, account_type, normal_balance, category, "
                    "description, status, created_at, updated_at) VALUES (:code, :name, :account_type, "
                    ":normal_balance, :category, :description, 'ACTIVE', now(), now())"),
                    {**acc,
                     "normal_balance": acc.get("normal_balance", "CREDIT" if acc["account_type"] == "REVENUE" else "DEBIT"),
                     "category": acc.get("category"), "description": acc.get("description")})
                print(f"  account {acc['code']} {acc['name']} created")
            else:
                print(f"  account {acc['code']} already present")
        for al in conf.get("add_aliases", []):
            cur = db.execute(text("SELECT aliases FROM finance_counterparties WHERE id=:id"),
                             {"id": al["counterparty_id"]}).scalar() or []
            if al["alias"] not in cur:
                db.execute(text("UPDATE finance_counterparties SET aliases = (coalesce(aliases::jsonb, '[]'::jsonb) "
                                "|| to_jsonb(ARRAY[:a]))::json, updated_at = now() WHERE id=:id"),
                           {"a": al["alias"], "id": al["counterparty_id"]})
                print(f"  alias '{al['alias']}' added to counterparty {al['counterparty_id']}")
            else:
                print(f"  alias '{al['alias']}' already present on {al['counterparty_id']}")
        for dv in conf.get("set_defaults", []):
            db.execute(text("UPDATE finance_counterparties SET default_account_code=:c, updated_at=now() "
                            "WHERE id=:id"), {"c": dv["default_account_code"], "id": dv["counterparty_id"]})
            print(f"  counterparty {dv['counterparty_id']} default -> {dv['default_account_code']}")
        db.commit()

        if args.config_only:
            print("config applied (resolutions skipped: --config-only)")
            return
        from src.services.transaction_service import transaction_service
        from src.models.transaction import FinanceTransaction, TransactionStatus
        applied, skipped = 0, 0
        # Later rounds OVERRIDE earlier ones for the same txn (Gaurav's newest verdict wins):
        # dedupe by txn id, round2 entries replacing round1.
        by_txn = {}
        for res in cfg.get("resolutions", []):
            by_txn[res["txn"]] = res
        for res in cfg.get("round2", {}).get("resolutions", []):
            if res.get("account_code"):  # confirmed_no_change entries carry no action
                by_txn[res["txn"]] = res
        from src.services.journal_service import journal_service
        for txn_id, res in sorted(by_txn.items()):
            t = db.get(FinanceTransaction, txn_id)
            if t is None:
                print(f"  txn {txn_id}: NOT FOUND — investigate"); continue
            if t.status == TransactionStatus.NEEDS_REVIEW:
                transaction_service.resolve_needs_review(
                    db, txn_id, account_code=res["account_code"],
                    counterparty_id=res.get("counterparty_id"), resolved_by="apply_feedback")
                print(f"  txn {txn_id}: resolved -> {res['account_code']}")
                applied += 1
            elif t.coa_account_code == res["account_code"]:
                print(f"  txn {txn_id}: already on {res['account_code']} via {t.categorized_by_logic} — ok")
                skipped += 1
            else:
                # booked to a DIFFERENT account than the ruling — the ruling wins (a verdict that
                # fails to apply is a defect, per the 2026-08-16 process rule). Re-book.
                if t.reconciled_journal_entry_id:
                    journal_service.void_entry(db, t.reconciled_journal_entry_id,
                        reason=f"apply_feedback override: Gaurav ruled {res['account_code']} (was {t.coa_account_code})")
                _was = t.coa_account_code
                t.status = TransactionStatus.NEEDS_REVIEW
                t.reconciled_journal_entry_id = None
                db.flush()
                transaction_service.resolve_needs_review(
                    db, txn_id, account_code=res["account_code"],
                    counterparty_id=res.get("counterparty_id"), resolved_by="apply_feedback")
                print(f"  txn {txn_id}: OVERRIDE {_was or '?'} -> {res['account_code']}")
                applied += 1
        db.commit()
        print(f"resolutions: {applied} applied, {skipped} already handled by rules/defaults")


def cmd_stage_events(args):
    """Stage the economic-events lane for a whole year (POL-124 ruling 4): stage_month for each
    entity x month. STAGED rows only — projection/posting comes at the year's posting step."""
    from src.services.economic_events.service import economic_event_service
    ent_ids = [int(x) for x in args.entity_ids.split(",")]
    with db_session() as db:
        for ent in ent_ids:
            for m in range(1, 13):
                period = date(args.year, m, 1)
                r = economic_event_service.stage_month(db, ent, period)
                n = len(r["staged"])
                errs = len(r.get("query_errors") or [])
                if n or errs:
                    print(f"  entity {ent} {period:%Y-%m}: {n} staged"
                          + (f", {errs} view errors" if errs else ""))
        db.commit()


def cmd_load_own_accounts(args):
    """Seed finance_stripe_own_accounts from OUR_CONNECT_ACCOUNTS.csv (idempotent upsert).
    Mapping (ENT-7/ENT-8): RMS/Flex+/caretaker -> the market's Stripe Connect bank account;
    held-funds -> the market's Customer Held Funds account. TEST/ADMIN/UNKNOWN rows load
    with NO bank mapping and import_payouts=false (visible, never imported)."""
    import csv as _csv
    BA = {("SG", "connect"): 20, ("SG", "held"): 1657, ("AU", "connect"): 22, ("AU", "held"): 1658}
    with db_session() as db, open(args.csv) as f:
        n = 0
        for r in _csv.DictReader(f):
            cat = r["Category"].strip()
            mkt = r["Market"].strip()
            group = ("held" if "held-funds" in cat.lower() or "deposit" in cat.lower()
                     else "connect" if cat in ("RMS", "Flex+", "caretaker") else None)
            ba_id = BA.get((mkt, group)) if group else None
            db.execute(text("""
                INSERT INTO finance_stripe_own_accounts
                  (stripe_account_id, market, email, category, finance_bank_account_id, import_payouts, notes)
                VALUES (:id, :mkt, :email, :cat, :ba, :imp, :notes)
                ON CONFLICT (stripe_account_id) DO UPDATE SET
                  market=:mkt, email=:email, category=:cat, finance_bank_account_id=:ba,
                  import_payouts=:imp, updated_at=now()"""),
                {"id": r["Stripe acct id"].strip(), "mkt": mkt, "email": r["Email"].strip(),
                 "cat": cat, "ba": ba_id, "imp": ba_id is not None,
                 "notes": f"seeded from OUR_CONNECT_ACCOUNTS.csv (created {r['Created']})"})
            n += 1
        db.commit()
        importable = db.execute(text(
            "SELECT market, finance_bank_account_id, count(*) FROM finance_stripe_own_accounts "
            "WHERE import_payouts GROUP BY 1,2 ORDER BY 1,2")).fetchall()
    print(f"{n} accounts upserted; importable groups: {[tuple(r) for r in importable]}")


def cmd_import_payouts(args):
    """History backfill: import BOTH payout lanes for a whole year, month by month
    (explicit periods — the sync button's 90-day default never reaches history).
    Platform lane AND own-account lane must both land BEFORE the engine runs the
    year's bank accounts: the bank-side Stripe guessing rules are deactivated
    (2026-08-16), so arrivals only book by PAIRING against imported lines."""
    from src.services.economic_events.service import economic_event_service
    ent_ids = [int(x) for x in args.entity_ids.split(",")]
    with db_session() as db:
        for ent in ent_ids:
            for m in range(1, 13):
                p = economic_event_service.import_payout_lines(db, ent, date(args.year, m, 1))
                o = economic_event_service.import_own_account_payout_lines(
                    db, ent, date(args.year, m, 1))
                if p["lines"] or o["lines"]:
                    print(f"  entity {ent} {args.year}-{m:02d}: platform {p['lines']} lines "
                          f"({p['created']} new) · own-accounts {o['lines']} lines ({o['created']} new)")
        db.commit()


def cmd_pair_stripe_payouts(args):
    """Identity pairing for Stripe sweeps (official pipeline step, replaces the one-off script):
    every unmatched imported payout line (platform or own-account) finds its bank arrival by
    abs-amount within ±5 days on the entity's statement accounts, books ONE transfer JE
    Dr <arrival bank> / Cr <source pocket>, marks both MATCHED. Handles variable payout
    destinations (2019: platform settled to BOTH OCBC accounts) that fixed-target rules can't."""
    from datetime import datetime as _dt, timedelta, UTC as _UTC
    from src.services.journal_service import journal_service
    from src.models.transaction import FinanceTransaction, TransactionStatus
    from src.models.bank_account import FinanceBankAccount
    y0, y1 = year_window(args.year)
    ent_ids = [int(x) for x in args.entity_ids.split(",")]
    with db_session() as db:
        lines = (db.query(FinanceTransaction)
                 .join(FinanceBankAccount, FinanceBankAccount.id == FinanceTransaction.bank_account_id)
                 .filter(FinanceTransaction.source.in_(["stripe_payout_import", "stripe_own_payout_import"]),
                         FinanceTransaction.status.in_([TransactionStatus.IMPORTED, TransactionStatus.PENDING,
                                                        TransactionStatus.AWAITING_MATCH]),
                         FinanceTransaction.transaction_date.between(y0, y1),
                         FinanceBankAccount.entity_id.in_(ent_ids)).all())
        stmt_bas = {ba.id: ba for ba in db.query(FinanceBankAccount)
                    .filter(FinanceBankAccount.entity_id.in_(ent_ids),
                            FinanceBankAccount.bank_name != "Stripe",
                            FinanceBankAccount.coa_account_code.isnot(None)).all()}
        paired = unpaired = 0
        for line in lines:
            pocket_ba = db.get(FinanceBankAccount, line.bank_account_id)
            amt = abs(float(line.amount))
            cands = (db.query(FinanceTransaction)
                     .filter(FinanceTransaction.bank_account_id.in_(list(stmt_bas)),
                             FinanceTransaction.status.in_([TransactionStatus.IMPORTED, TransactionStatus.PENDING]),
                             FinanceTransaction.amount > 0,
                             FinanceTransaction.transaction_date.between(
                                 line.transaction_date - timedelta(days=5),
                                 line.transaction_date + timedelta(days=10))).all())
            match = [c for c in cands if abs(abs(float(c.amount)) - amt) <= 0.005]
            if not match:
                unpaired += 1
                print(f"  line {line.id} ({amt:.2f} {line.transaction_date}): NO arrival — left for review")
                continue
            # several same-amount arrivals in the window (e.g. four S$500 deposit sweeps in one
            # December): assign the NEAREST-dated one; candidates already claimed by earlier
            # lines are MATCHED and fell out of the query, so greedy-by-date is one-to-one.
            match.sort(key=lambda c: (abs((c.transaction_date - line.transaction_date).days), c.id))
            bk = match[0]
            bank_ba = stmt_bas[bk.bank_account_id]
            je = journal_service.create(
                db=db, entity_id=bank_ba.entity_id, entry_date=bk.transaction_date,
                description=f"Stripe sweep {pocket_ba.account_name} -> {bank_ba.account_name} "
                            f"(identity-paired: txn {line.id} <-> {bk.id})",
                lines=[{"account_code": bank_ba.coa_account_code, "debit_amount": amt, "credit_amount": 0.0},
                       {"account_code": pocket_ba.coa_account_code, "debit_amount": 0.0, "credit_amount": amt}])
            je.source = "categorization_engine"
            db.flush()
            now = _dt.now(_UTC)
            for t, logic in ((line, "transfer_rule"), (bk, "transfer_pairing")):
                t.status = TransactionStatus.MATCHED
                t.reconciled_journal_entry_id = je.id
                t.matched_at = now
                t.categorized_by_logic = logic
            paired += 1
        db.commit()
        print(f"paired {paired}, unpaired {unpaired}")


def gather_events(db, year, ent_ids):
    return [dict(zip(("entity", "period", "event_type", "amount", "ccy", "status"), r))
            for r in db.execute(text("""
        SELECT e.name, ev.period, ev.event_type, round(ev.amount::numeric,2), ev.currency, ev.status
        FROM finance_economic_events ev JOIN finance_entities e ON e.id = ev.entity_id
        WHERE ev.entity_id = ANY(:ents) AND ev.period BETWEEN :y0 AND :y1
        ORDER BY ev.entity_id, ev.period, ev.event_type"""),
        {"ents": ent_ids, "y0": date(year, 1, 1), "y1": date(year, 12, 31)}).fetchall()]


def gather_scorecard(db, year, ba_ids):
    y0, y1 = year_window(year)
    p = {"ba": ba_ids, "y0": y0, "y1": y1}
    header = [dict(zip(("ba", "acct", "coa", "ccy", "entity"), r)) for r in db.execute(text("""
        SELECT ba.id, ba.account_name, ba.coa_account_code, ba.currency, e.name
        FROM finance_bank_accounts ba JOIN finance_entities e ON e.id=ba.entity_id
        WHERE ba.id = ANY(:ba) ORDER BY ba.id"""), p).fetchall()]
    coa_names = {r[0]: r[1] for r in db.execute(text(
        "SELECT code, name FROM finance_accounts WHERE entity_id IS NULL")).fetchall()}
    txns = [dict(zip(("id", "ba", "dt", "amt", "ccy", "descr", "status", "route", "coa",
                      "cp", "ai_coa", "ai_conf", "ai_why"), r)) for r in db.execute(text("""
        SELECT t.id, t.bank_account_id, t.transaction_date, round(t.amount::numeric,2), t.currency,
               left(coalesce(t.description,''),110), t.status, coalesce(t.categorized_by_logic,''),
               coalesce(t.coa_account_code,''), coalesce(cp.name, t.counterparty_name, ''),
               coalesce(t.ai_suggested_account_code,''), t.ai_confidence, left(coalesce(t.ai_reasoning,''),160)
        FROM finance_transactions t LEFT JOIN finance_counterparties cp ON cp.id=t.counterparty_id
        WHERE t.bank_account_id = ANY(:ba) AND t.transaction_date BETWEEN :y0 AND :y1
        ORDER BY t.bank_account_id, t.transaction_date, t.id"""), p).fetchall()]
    return header, coa_names, txns


def cmd_scorecard(args):
    ba_ids = [int(x) for x in args.bank_account_ids.split(",")]
    with db_session() as db:
        header, coa_names, txns = gather_scorecard(db, args.year, ba_ids)
        inv = gather_check(db, args.year, ba_ids)
        ent_ids = [r[0] for r in db.execute(text(
            "SELECT DISTINCT entity_id FROM finance_bank_accounts WHERE id = ANY(:ba)"),
            {"ba": ba_ids}).fetchall()]
        events = gather_events(db, args.year, ent_ids)
    e = html.escape

    def coa_label(code):
        return f"{code} {coa_names.get(code, '')}".strip()

    acct_by_ba = {h["ba"]: h for h in header}
    entities = sorted({h["entity"] for h in header})
    head_lines = "".join(
        f"<li><b>{e(h['acct'])}</b> — account code {e(h['coa'])} · {e(h['ccy'])} · {e(h['entity'])} (bank id {h['ba']})</li>"
        for h in header)

    from collections import Counter as _C
    mix = _C()
    for t in txns:
        mix[(t["route"] or "(unresolved)", t["status"])] += 1

    inv_rows = "".join(
        f"<tr><td>{e(r['name'])}</td><td>{e(r['month_end'])}</td>"
        f"<td class=n>{'' if r['statement'] is None else format(r['statement'], ',.2f')}</td>"
        f"<td class=n>{format(r['ledger'], ',.2f')}</td>"
        f"<td class=n>{'' if r['diff'] is None else format(r['diff'], ',.2f')}</td>"
        f"<td>{('✓' if (r['diff'] is not None and abs(r['diff']) <= 0.02) else ('⚠' if r['diff'] is not None else '—'))}</td></tr>"
        for r in inv)

    txn_rows = []
    for t in txns:
        booked = coa_label(t["coa"]) if t["coa"] else ""
        ai = ""
        if t["ai_coa"]:
            conf = f" @ {float(t['ai_conf']):.0%}" if t["ai_conf"] is not None else ""
            ai = f"{coa_label(t['ai_coa'])}{conf}<div class=why>{e(t['ai_why'])}</div>"
        need = t["status"] in ("NEEDS_REVIEW", "PENDING", "IMPORTED")
        txn_rows.append(
            f"<tr data-txn={t['id']} data-amt={float(t['amt'])} data-status=\"{e(t['status'])}\" data-route=\"{e(t['route'])}\" "
            f"data-acct=\"{e(acct_by_ba[t['ba']]['acct'])}\" data-nocp={0 if t['cp'] else 1} "
            f"class={'review' if need else 'ok'}>"
            f"<td>{t['id']}</td><td>{e(str(t['dt']))}</td>"
            f"<td class=n>{format(float(t['amt']), ',.2f')}</td>"
            f"<td>{e(acct_by_ba[t['ba']]['acct'])}</td>"
            f"<td class=descr>{e(t['descr'])}</td>"
            f"<td>{e(t['cp'])}</td>"
            f"<td>{e(t['status'])}<div class=why>{e(t['route'])}</div></td>"
            f"<td>{booked}</td><td>{ai}</td>"
            f"<td><select class=verdict><option value=''></option><option>OK</option>"
            f"<option>Wrong COA</option><option>Wrong counterparty</option><option>Other</option></select>"
            f"<input class=fb placeholder='correct COA / name / note' size=22></td></tr>")

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>History recon — {args.year} — {e(', '.join(entities))}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;margin:24px;color:#1a202c}}
 h1{{font-size:20px;margin-bottom:2px}} h2{{font-size:15px;margin-top:28px;border-bottom:2px solid #e2e8f0;padding-bottom:4px}}
 table{{border-collapse:collapse;font-size:12px;margin-top:8px;width:100%}}
 th{{background:#f7fafc;text-align:left;padding:4px 8px;border-bottom:2px solid #cbd5e0;position:sticky;top:0}}
 td{{padding:3px 8px;border-bottom:1px solid #edf2f7;vertical-align:top}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
 td.descr{{max-width:340px}} .why{{color:#718096;font-size:11px}}
 tr.review{{background:#fffbea}} .note{{color:#718096;font-size:12.5px}}
 #export{{position:fixed;right:24px;top:18px;background:#2b6cb0;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-size:13px;cursor:pointer}}
 ul{{font-size:13px}}
</style></head><body>
<button id=export onclick=exportFb()>Export my feedback (JSON)</button>
<h1>Previous-years reconciliation — {args.year}</h1>
<p class=note><b>Entity:</b> {e(', '.join(entities))}. Shadow run on the clone: entries are DRAFT only, nothing is posted.
Yellow rows need your input. For any row you disagree with: pick a verdict, type the correct account name or note,
then click <b>Export my feedback</b> (top right) and send me the file — I apply it (rules / counterparties / corpus), re-run the year, and send a fresh scorecard.</p>
<h2>Bank accounts covered</h2><ul>{head_lines}</ul>
<h2>How much booked automatically</h2>
<table><thead><tr><th>route</th><th>status</th><th>txns</th></tr></thead><tbody>
{''.join(f'<tr><td>{e(k[0])}</td><td>{e(k[1])}</td><td class=n>{v}</td></tr>' for k, v in sorted(mix.items(), key=lambda x: -x[1]))}
</tbody></table>
<h2>Bank balance check — statement vs our books, each month-end</h2>
<p class=note>"Our books" = every entry (posted + draft) on that bank account up to the date, excluding the temporary opening/park entries.
A ⚠ means the year isn't fully booked yet at that date (usually the unresolved rows below).</p>
<table><thead><tr><th>account</th><th>month-end</th><th>bank statement</th><th>our books</th><th>difference</th><th></th></tr></thead>
<tbody>{inv_rows}</tbody></table>
<h2>Economic events staged for {args.year} (accrual lane — ClickHouse)</h2>
<p class=note>These are the platform-derived monthly aggregates (revenue, host costs, Stripe activity) staged
for the same year, per POL-124 ruling 4 — a year only closes when both lanes are booked. STAGED = awaiting
your sign-off before projection/posting. Stripe cash accounts get their history from THIS lane; no bank
statements exist for them pre-2026.</p>
{('<table><thead><tr><th>entity</th><th>month</th><th>event type</th><th>amount</th><th>ccy</th><th>status</th></tr></thead><tbody>'
  + ''.join(f"<tr><td>{e(r['entity'])}</td><td>{r['period']:%Y-%m}</td><td>{e(r['event_type'])}</td>"
            f"<td class=n>{format(float(r['amount']), ',.2f')}</td><td>{e(r['ccy'])}</td><td>{e(r['status'])}</td></tr>"
            for r in events)
  + '</tbody></table>') if events else '<p class=note><b>None staged.</b> Run the stage-events step, or ClickHouse has no data for this year/entity.</p>'}
<h2>Every transaction ({len(txns)}) — booked account, AI recommendation, and your verdict</h2>
<div id=filters style="margin:8px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px">
 <input id=fsearch placeholder="search description / counterparty / account…" size=34 oninput=applyF()>
 <select id=fstatus onchange=applyF()><option value="">all statuses</option></select>
 <select id=froute onchange=applyF()><option value="">all routes</option></select>
 <select id=facct onchange=applyF()><option value="">all bank accounts</option></select>
 <label><input type=checkbox id=fneed onchange=applyF()> only rows needing input</label>
 <label><input type=checkbox id=fnocp onchange=applyF()> no counterparty identified</label>
 <span id=fcount class=note></span>
</div>
<table id=txntable><thead><tr><th>txn</th><th>date</th><th style=cursor:pointer onclick=sortAmt() title="sort by amount">amount ⇅</th><th>bank account</th><th>description</th><th>counterparty</th>
<th>status / route</th><th>booked to</th><th>AI recommendation</th><th>your verdict + correction</th></tr></thead>
<tbody>{''.join(txn_rows)}</tbody></table>
<script>
function applyF() {{
  const q=(document.getElementById('fsearch').value||'').toLowerCase();
  const st=document.getElementById('fstatus').value, rt=document.getElementById('froute').value;
  const ac=document.getElementById('facct').value, need=document.getElementById('fneed').checked;
  let shown=0;
  document.querySelectorAll('#txntable tbody tr').forEach(tr=>{{
    const ok=(!q||tr.textContent.toLowerCase().includes(q))
      && (!st||tr.dataset.status===st) && (!rt||tr.dataset.route===rt)
      && (!ac||tr.dataset.acct===ac) && (!need||tr.classList.contains('review'))
      && (!document.getElementById('fnocp').checked||tr.dataset.nocp==='1');
    tr.style.display=ok?'':'none'; if(ok)shown++;
  }});
  document.getElementById('fcount').textContent=shown+' shown';
}}
window.addEventListener('DOMContentLoaded',()=>{{
  const sts=new Set(), rts=new Set(), acs=new Set();
  document.querySelectorAll('#txntable tbody tr').forEach(tr=>{{
    if(tr.dataset.status)sts.add(tr.dataset.status);
    if(tr.dataset.route)rts.add(tr.dataset.route);
    if(tr.dataset.acct)acs.add(tr.dataset.acct);
  }});
  const fill=(id,vals)=>{{const el=document.getElementById(id);
    [...vals].sort().forEach(v=>{{const o=document.createElement('option');o.textContent=v;el.appendChild(o)}})}};
  fill('fstatus',sts); fill('froute',rts); fill('facct',acs); applyF();
}});
let sortDir=0;
function sortAmt() {{
  sortDir = sortDir===1 ? -1 : 1;
  const tb=document.querySelector('#txntable tbody');
  [...tb.querySelectorAll('tr')].sort((a,b)=>sortDir*(Math.abs(+b.dataset.amt)-Math.abs(+a.dataset.amt)))
    .forEach(tr=>tb.appendChild(tr));
}}
function exportFb() {{
  const rows = [];
  document.querySelectorAll('tr[data-txn]').forEach(tr => {{
    const v = tr.querySelector('.verdict').value, f = tr.querySelector('.fb').value;
    if (v || f) rows.push({{txn: +tr.dataset.txn, verdict: v, input: f}});
  }});
  const blob = new Blob([JSON.stringify({{year: {args.year}, bank_account_ids: [{e(args.bank_account_ids)}], feedback: rows}}, null, 1)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'scorecard_feedback_{args.year}.json'; a.click();
}}
</script></body></html>"""
    with open(args.out, "w") as f:
        f.write(doc)
    print(f"scorecard -> {args.out} ({len(doc)//1024} KB, {len(txns)} txns)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-prod", metavar="PASSPHRASE",
                    help="Arm ONE write command against production. Requires the literal "
                         "passphrase RUN-ON-PROD-2019 and an interactive PROCEED confirmation.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("check", cmd_check), ("scorecard", cmd_scorecard)):
        s = sub.add_parser(name)
        s.add_argument("--year", type=int, required=True)
        s.add_argument("--bank-account-ids", required=True)
        if name == "run":
            s.add_argument("--limit", type=int, default=200)
        if name == "scorecard":
            s.add_argument("--out", required=True)
        s.set_defaults(fn=fn)
    s = sub.add_parser("apply-feedback")
    s.add_argument("--file", required=True)
    s.add_argument("--config-only", action="store_true")
    s.set_defaults(fn=cmd_apply_feedback)
    s = sub.add_parser("stage-events")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--entity-ids", required=True)
    s.set_defaults(fn=cmd_stage_events)
    s = sub.add_parser("load-own-accounts")
    s.add_argument("--csv", default="documentation/wip/OUR_CONNECT_ACCOUNTS.csv")
    s.set_defaults(fn=cmd_load_own_accounts)
    s = sub.add_parser("import-payouts")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--entity-ids", required=True)
    s.set_defaults(fn=cmd_import_payouts)
    s = sub.add_parser("pair-stripe-payouts")
    s.add_argument("--year", type=int, required=True)
    s.add_argument("--entity-ids", required=True)
    s.set_defaults(fn=cmd_pair_stripe_payouts)
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    tgt = "LOCAL-CLONE" if ("localhost" in url or "127.0.0.1" in url) else "PROD"
    print(f"[history_runner] target={tgt}")
    if args.cmd in ("run", "apply-feedback", "stage-events", "load-own-accounts", "import-payouts", "pair-stripe-payouts") and tgt == "PROD":
        # Deliberate prod arming (PROD_RUNBOOK_2019): the default is REFUSE. A single invocation
        # can be armed with --allow-prod plus the literal passphrase, so arming is a conscious act
        # that cannot happen by a stray `source .env` or a copy-pasted command.
        if not getattr(args, "allow_prod", None):
            print("REFUSING: shadow work happens on the CLONE (POL-124/VR-1c). Point DATABASE_URL "
                  "at the dated clone, or arm this single run with --allow-prod RUN-ON-PROD-2019.")
            return
        if args.allow_prod != "RUN-ON-PROD-2019":
            print(f"REFUSING: --allow-prod passphrase mismatch (got {args.allow_prod!r}).")
            return
        print(f"\n*** ARMED FOR PRODUCTION *** command={args.cmd}  db={url.split('@')[-1][:60]}")
        if input("Type PROCEED to run this ONE command against production: ").strip() != "PROCEED":
            print("Aborted — nothing ran.")
            return
    args.fn(args)


if __name__ == "__main__":
    main()
