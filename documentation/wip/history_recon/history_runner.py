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
            f"<tr data-txn={t['id']} data-status=\"{e(t['status'])}\" data-route=\"{e(t['route'])}\" "
            f"data-acct=\"{e(acct_by_ba[t['ba']]['acct'])}\" class={'review' if need else 'ok'}>"
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
<h2>Every transaction ({len(txns)}) — booked account, AI recommendation, and your verdict</h2>
<div id=filters style="margin:8px 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px">
 <input id=fsearch placeholder="search description / counterparty / account…" size=34 oninput=applyF()>
 <select id=fstatus onchange=applyF()><option value="">all statuses</option></select>
 <select id=froute onchange=applyF()><option value="">all routes</option></select>
 <select id=facct onchange=applyF()><option value="">all bank accounts</option></select>
 <label><input type=checkbox id=fneed onchange=applyF()> only rows needing input</label>
 <span id=fcount class=note></span>
</div>
<table id=txntable><thead><tr><th>txn</th><th>date</th><th>amount</th><th>bank account</th><th>description</th><th>counterparty</th>
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
      && (!ac||tr.dataset.acct===ac) && (!need||tr.classList.contains('review'));
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
    args = ap.parse_args()
    url = os.getenv("DATABASE_URL", "")
    tgt = "LOCAL-CLONE" if ("localhost" in url or "127.0.0.1" in url) else "PROD"
    print(f"[history_runner] target={tgt}")
    if args.cmd == "run" and tgt == "PROD":
        print("REFUSING: shadow runs happen on the CLONE (POL-124/VR-1c). Set DATABASE_URL to finance_local.")
        return
    args.fn(args)


if __name__ == "__main__":
    main()
