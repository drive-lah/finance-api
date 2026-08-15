"""Zilla recipient-mapping tool builder (Pickle, 2026-08-15).

Every Wise recipient across our 3 profiles should map to ONE counterparty on our side. The account-holder
name on Wise often isn't the counterparty name (e.g. a personal Wise recipient paying into an "RR Ventures"
business account) so the mapping needs a HUMAN (Zilla). This emits a SELF-CONTAINED interactive HTML:
every Wise recipient + a searchable counterparty picker + a comment box, autosaving to the browser, with
Export-JSON / Copy so she can send the result back. We ingest it confirm-gated into the registration model.

WRITES NOTHING to the DB. Run:
  PYTHONPATH=. ../finance-api/venv/bin/python documentation/wip/build_zilla_mapping_tool.py
"""
import json
import os
import re
from difflib import SequenceMatcher

from dotenv import load_dotenv
load_dotenv("/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api-payout/.env")

from sqlalchemy import text
from src.database import db_session
from src.services.wise_service import WiseService

CHANNELS = {13811029: "Wise SG", 41524706: "Wise AU", 74921502: "Wise Ventures"}
_SUFFIX = re.compile(r"\b(pte|pty|ltd|limited|inc|llc|llp|co|company|holdings?|group|services?)\b", re.I)
OUT = "documentation/wip/zilla_recipient_mapping.html"


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def score(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0
    return max(SequenceMatcher(None, na, nb).ratio(), jacc)


def main():
    w = WiseService()
    with db_session() as db:
        cps = db.execute(text(
            "SELECT id, name FROM finance_counterparties WHERE name IS NOT NULL ORDER BY name")).all()
    cps = [{"id": cid, "name": nm} for cid, nm in cps]
    cp_pairs = [(c["id"], c["name"]) for c in cps]

    recips = []
    for pid, label in CHANNELS.items():
        try:
            accts = w._get("/v1/accounts", {"profile": pid})
            accts = accts if isinstance(accts, list) else accts.get("content", [])
        except Exception as e:
            print(f"  {label}: fetch error {str(e)[:80]}"); continue
        for a in accts:
            det = a.get("details") or {}
            holder = a.get("accountHolderName")
            best = max(((score(holder, nm), cid, nm) for cid, nm in cp_pairs), default=(0, None, None))
            s, cid, nm = best
            recips.append({
                "channel": label, "profile": pid, "recipient_id": a.get("id"),
                "holder": holder, "currency": a.get("currency"),
                "acct": det.get("accountNumber") or det.get("iban") or "",
                "proposed_id": cid if s >= 0.90 else None,
                "proposed_name": nm if s >= 0.90 else None,
                "proposed_score": round(s, 3),
            })
    recips.sort(key=lambda r: (r["channel"], (r["holder"] or "").lower()))

    payload = {"recipients": recips, "counterparties": cps}
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload))
    with open(OUT, "w") as f:
        f.write(html)
    print(f"Wise recipients: {len(recips)}   counterparties: {len(cps)}")
    print(f"  pre-proposed (>=0.90): {sum(1 for r in recips if r['proposed_id'])}")
    print(f"tool -> {OUT}")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Wise Recipient → Counterparty Mapping</title>
<style>
  :root{--bg:#f7f8fa;--card:#fff;--ink:#1a1f2b;--mut:#6b7280;--line:#e5e7eb;--blue:#2563eb;--amber:#b45309;--green:#059669}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
  header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:14px 20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
  h1{font-size:17px;margin:0 0 4px}
  .sub{color:var(--mut);font-size:12.5px;margin:0}
  .bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:10px}
  .bar input[type=text]{padding:7px 10px;border:1px solid var(--line);border-radius:8px;min-width:220px}
  .bar select{padding:7px 8px;border:1px solid var(--line);border-radius:8px}
  .prog{margin-left:auto;font-weight:600}
  .prog b{color:var(--green)}
  button{padding:8px 14px;border:1px solid var(--line);border-radius:8px;background:var(--card);cursor:pointer;font-weight:600}
  button.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
  main{padding:16px 20px 80px;max-width:1200px;margin:0 auto}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
  th{background:#f0f2f5;font-size:12px;color:var(--mut);position:sticky;top:0}
  tr:last-child td{border-bottom:none}
  .holder{font-weight:600}
  .meta{color:var(--mut);font-size:12px}
  .ccy{display:inline-block;padding:1px 6px;border-radius:5px;background:#eef2ff;color:#3730a3;font-weight:600;font-size:11.5px}
  .pick{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:7px}
  .cmt{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:7px;margin-top:5px}
  .hint{font-size:11.5px;color:var(--amber);margin-top:3px}
  tr.done{background:#f0fdf4}
  tr.done .holder::after{content:" ✓";color:var(--green)}
  .foot{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--line);padding:10px 20px;display:flex;gap:10px;align-items:center}
  .foot .note{color:var(--mut);font-size:12px}
  .save{color:var(--green);font-size:12px;font-weight:600;opacity:0;transition:opacity .3s}
</style></head>
<body>
<header>
  <h1>Wise Recipient → Counterparty Mapping</h1>
  <p class="sub">Every Wise recipient below should point to ONE counterparty in our system. The Wise
  account-holder name is often NOT the counterparty (e.g. a personal recipient paying into a business
  account). Pick the right counterparty, or leave a comment if none fits / it needs a new one. Your work
  autosaves in this browser — when done, click <b>Export mapping</b> and send the file back.</p>
  <div class="bar">
    <input id="q" type="text" placeholder="Search holder / account / currency…"/>
    <select id="fCh"><option value="">All channels</option></select>
    <select id="fCcy"><option value="">All currencies</option></select>
    <select id="fSt"><option value="">All rows</option><option value="todo">Unmapped only</option><option value="done">Mapped only</option></select>
    <span class="prog">Mapped <b id="pDone">0</b> / <span id="pTot">0</span></span>
  </div>
</header>
<main>
  <table><thead><tr>
    <th style="width:90px">Channel</th><th style="width:60px">Ccy</th>
    <th>Wise recipient</th><th style="width:44%">Counterparty (our system)</th>
  </tr></thead><tbody id="rows"></tbody></table>
</main>
<div class="foot">
  <button class="primary" id="exp">⬇ Export mapping (JSON)</button>
  <button id="cp">Copy to clipboard</button>
  <span class="save" id="saved">Saved ✓</span>
  <span class="note" id="note"></span>
</div>
<script>
const DATA = __DATA__;
const KEY = "zilla_recip_map_v1";
const state = JSON.parse(localStorage.getItem(KEY) || "{}"); // recipient_id -> {counterparty_id, comment}
const cps = DATA.counterparties;
const cpById = Object.fromEntries(cps.map(c => [String(c.id), c.name]));

function saveFlash(){ const s=document.getElementById("saved"); s.style.opacity=1; setTimeout(()=>s.style.opacity=0,700); }
function persist(){ localStorage.setItem(KEY, JSON.stringify(state)); saveFlash(); refreshProgress(); }

function optionsHtml(sel){
  let h = '<option value="">— select counterparty —</option><option value="__none">✗ No counterparty / needs new</option>';
  for(const c of cps){ h += `<option value="${c.id}" ${String(c.id)===String(sel)?"selected":""}>${esc(c.name)} — #${c.id}</option>`; }
  return h;
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }

function rowDone(r){ const st=state[r.recipient_id]; return st && (st.counterparty_id || st.counterparty_id==="__none"); }

function render(){
  const q=(document.getElementById("q").value||"").toLowerCase();
  const fCh=document.getElementById("fCh").value, fCcy=document.getElementById("fCcy").value, fSt=document.getElementById("fSt").value;
  const tb=document.getElementById("rows"); tb.innerHTML="";
  for(const r of DATA.recipients){
    if(fCh && r.channel!==fCh) continue;
    if(fCcy && r.currency!==fCcy) continue;
    const done=rowDone(r);
    if(fSt==="todo" && done) continue;
    if(fSt==="done" && !done) continue;
    const hay=`${r.holder} ${r.acct} ${r.currency} ${r.channel}`.toLowerCase();
    if(q && !hay.includes(q)) continue;
    const st=state[r.recipient_id]||{};
    const sel = st.counterparty_id!=null ? st.counterparty_id : (r.proposed_id!=null?r.proposed_id:"");
    const tr=document.createElement("tr"); if(done) tr.className="done";
    tr.innerHTML=`
      <td><span class="meta">${esc(r.channel)}</span></td>
      <td><span class="ccy">${esc(r.currency||"?")}</span></td>
      <td><div class="holder">${esc(r.holder||"(no name)")}</div>
          <div class="meta">acct ${esc(r.acct||"—")} · Wise id ${esc(r.recipient_id)}</div></td>
      <td>
        <select class="pick" data-id="${r.recipient_id}">${optionsHtml(sel)}</select>
        ${r.proposed_id!=null && st.counterparty_id==null ? `<div class="hint">suggested: ${esc(r.proposed_name)} (#${r.proposed_id}, ${r.proposed_score}) — confirm or change</div>`:""}
        <input class="cmt" data-id="${r.recipient_id}" placeholder="comment (optional — e.g. account is in a different legal name)" value="${esc(st.comment||"")}"/>
      </td>`;
    tb.appendChild(tr);
  }
}
function refreshProgress(){
  const done=DATA.recipients.filter(rowDone).length;
  document.getElementById("pDone").textContent=done;
  document.getElementById("pTot").textContent=DATA.recipients.length;
}
document.addEventListener("change",e=>{
  if(e.target.classList.contains("pick")){
    const id=e.target.dataset.id; state[id]=state[id]||{}; state[id].counterparty_id=e.target.value||null;
    persist(); const tr=e.target.closest("tr"); tr.classList.toggle("done", rowDone({recipient_id:id}));
  }
});
document.addEventListener("input",e=>{
  if(e.target.classList.contains("cmt")){ const id=e.target.dataset.id; state[id]=state[id]||{}; state[id].comment=e.target.value; localStorage.setItem(KEY,JSON.stringify(state)); }
});
["q","fCh","fCcy","fSt"].forEach(id=>document.getElementById(id).addEventListener("input",render));

function buildExport(){
  return DATA.recipients.map(r=>{
    const st=state[r.recipient_id]||{};
    const cid = st.counterparty_id!=null?st.counterparty_id:null;
    return {recipient_id:r.recipient_id, channel:r.channel, profile:r.profile, currency:r.currency,
            holder:r.holder, acct:r.acct,
            counterparty_id: (cid==="__none"||cid==null)?null:Number(cid),
            counterparty_name: (cid && cid!=="__none")?cpById[String(cid)]:null,
            no_match: cid==="__none", comment: st.comment||""};
  });
}
document.getElementById("exp").onclick=()=>{
  const blob=new Blob([JSON.stringify({generated:new Date().toISOString(),mappings:buildExport()},null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="zilla_recipient_mapping.json"; a.click();
};
document.getElementById("cp").onclick=async()=>{
  await navigator.clipboard.writeText(JSON.stringify({generated:new Date().toISOString(),mappings:buildExport()},null,2));
  document.getElementById("note").textContent="Copied to clipboard."; setTimeout(()=>document.getElementById("note").textContent="",2000);
};
// populate filters
[...new Set(DATA.recipients.map(r=>r.channel))].forEach(v=>{const o=document.createElement("option");o.value=o.textContent=v;fCh.appendChild(o);});
[...new Set(DATA.recipients.map(r=>r.currency).filter(Boolean))].sort().forEach(v=>{const o=document.createElement("option");o.value=o.textContent=v;fCcy.appendChild(o);});
render(); refreshProgress();
</script>
</body></html>"""


if __name__ == "__main__":
    main()
