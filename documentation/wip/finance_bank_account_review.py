"""Finance bank-account REVIEW sheet (Pickle, 2026-08-14) — one-time exercise, read-only.

A counterparty-centric CSV for the finance team to validate/annotate each payee's bank account:
best-match Wise recipient pre-filled where we found one, blank where not, plus remarks columns for
finance to confirm or correct. Feeds the confirmed backfill (PM-5) once returned.

Run: PYTHONPATH=. ../finance-api/venv/bin/python documentation/wip/finance_bank_account_review.py
"""
import csv
import re
from difflib import SequenceMatcher

from dotenv import load_dotenv
load_dotenv("/Users/gauravsinghal/Documents/Work/G-master/drivelah/finance-api-payout/.env")

from sqlalchemy import text
from src.database import db_session
from src.services.wise_service import WiseService

CHANNELS = {13811029: "Wise SG", 41524706: "Wise AU", 74921502: "Wise Ventures"}
_SUFFIX = re.compile(r"\b(pte|pty|ltd|limited|inc|llc|llp|co|company|holdings?|group|services?)\b", re.I)


def norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", s)).strip()


def score(a, b):
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
    recips = []
    for pid, label in CHANNELS.items():
        try:
            accts = w._get("/v1/accounts", {"profile": pid})
            accts = accts if isinstance(accts, list) else accts.get("content", [])
        except Exception:
            continue
        for a in accts:
            det = a.get("details") or {}
            acct = det.get("accountNumber") or det.get("iban") or ""
            recips.append({"channel": label, "recipient_id": a.get("id"), "holder": a.get("accountHolderName"),
                           "currency": a.get("currency"), "acct_tail": ("…" + acct[-4:]) if len(acct) >= 4 else acct})

    with db_session() as db:
        cps = db.execute(text("SELECT id, name FROM finance_counterparties WHERE name IS NOT NULL ORDER BY name")).all()

    rows = []
    for cid, nm in cps:
        best = max(((score(nm, r["holder"]), r) for r in recips), key=lambda t: t[0], default=(0, None))
        s, r = best
        matched = s >= 0.70
        rows.append({
            "counterparty_id": cid, "counterparty_name": nm,
            "proposed_holder": r["holder"] if matched else "",
            "currency": r["currency"] if matched else "",
            "account_tail": r["acct_tail"] if matched else "",
            "channel": r["channel"] if matched else "",
            "wise_recipient_id": r["recipient_id"] if matched else "",
            "match_confidence": f"{s:.2f}" if matched else "",
            "FINANCE_confirm_Y_N": "", "FINANCE_correct_account_if_wrong": "", "FINANCE_remarks": "",
        })
    rows.sort(key=lambda x: (x["proposed_holder"] == "", x["counterparty_name"].lower()))

    out = "documentation/wip/finance_bank_account_review.csv"
    cols = ["counterparty_id", "counterparty_name", "proposed_holder", "currency", "account_tail",
            "channel", "wise_recipient_id", "match_confidence",
            "FINANCE_confirm_Y_N", "FINANCE_correct_account_if_wrong", "FINANCE_remarks"]
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader(); wr.writerows(rows)
    have = sum(1 for x in rows if x["proposed_holder"])
    print(f"counterparties: {len(rows)}   with a proposed account: {have}   blank (finance to fill): {len(rows)-have}")
    print(f"review sheet -> {out}")


if __name__ == "__main__":
    main()
