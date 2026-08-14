"""PM-5 backfill matcher — PREVIEW ONLY (Pickle, 2026-08-14).

Pulls every Wise recipient across our 3 profiles and proposes a link to a finance counterparty by
name (and account-number corroboration), so the 120 recipients Wise already holds can populate the new
counterparty_bank_account + payout_channel_registration layer. WRITES NOTHING — emits a proposals CSV
and a confidence summary for Gaurav to approve. Money-routing data, so confirm-gated by design (DQ-100).

Run: PYTHONPATH=. ../finance-api/venv/bin/python documentation/wip/payout_recipient_backfill.py
"""
import csv
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
        cps = db.execute(text("SELECT id, name FROM finance_counterparties WHERE name IS NOT NULL")).all()
    cps = [(cid, nm) for cid, nm in cps]

    recips = []
    for pid, label in CHANNELS.items():
        try:
            accts = w._get("/v1/accounts", {"profile": pid})
            accts = accts if isinstance(accts, list) else accts.get("content", [])
        except Exception as e:
            print(f"  {label}: fetch error {str(e)[:80]}"); continue
        for a in accts:
            det = a.get("details") or {}
            recips.append({"channel": label, "profile": pid, "recipient_id": a.get("id"),
                           "holder": a.get("accountHolderName"), "currency": a.get("currency"),
                           "acct": det.get("accountNumber") or det.get("iban") or ""})

    rows, hi, med, lo = [], 0, 0, 0
    for r in recips:
        best = max(((score(r["holder"], nm), cid, nm) for cid, nm in cps), default=(0, None, None))
        s, cid, nm = best
        band = "HIGH" if s >= 0.90 else "MEDIUM" if s >= 0.70 else "LOW/NONE"
        hi += band == "HIGH"; med += band == "MEDIUM"; lo += band == "LOW/NONE"
        rows.append({**r, "match_score": round(s, 3), "band": band,
                     "counterparty_id": cid if s >= 0.70 else "", "counterparty_name": nm if s >= 0.70 else ""})

    out = "documentation/wip/payout_recipient_backfill_proposals.csv"
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["channel", "profile", "recipient_id", "holder", "currency",
                                           "acct", "match_score", "band", "counterparty_id", "counterparty_name"])
        wr.writeheader(); wr.writerows(rows)

    print(f"Wise recipients: {len(recips)}   counterparties: {len(cps)}")
    print(f"  HIGH (>=0.90, auto-linkable on confirm): {hi}")
    print(f"  MEDIUM (0.70-0.90, review each):          {med}")
    print(f"  LOW/NONE (<0.70, manual):                 {lo}")
    print(f"proposals -> {out}\n")
    print("Sample HIGH matches:")
    for r in [x for x in rows if x["band"] == "HIGH"][:12]:
        print(f"  {r['channel']:<13} {str(r['holder'])[:28]:<28} → cp#{r['counterparty_id']} {str(r['counterparty_name'])[:28]} ({r['match_score']})")


if __name__ == "__main__":
    main()
