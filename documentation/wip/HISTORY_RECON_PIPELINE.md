# Previous-Years Reconciliation Pipeline (2019–2025) — Spec (Gaurav rulings, 2026-08-15)

The mammoth: book all pre-2026 history properly, year by year, with the categorization engine +
economic-events lane, agent-reviewed, invariant-gated, scorecard-first. Prereq PASSED: every non-Stripe
bank's imported txn chain ties to its running balance and Jan-1 opening to the cent (2026-08-15 gate);
Stripe replays via its two lanes (DQ-54) when its turn comes.

## Locked rulings (Gaurav, 2026-08-15)

1. **GST — AU posts GST fully, historically.** The lane machine (POL-123) runs as-is on AU history:
   cash-basis GST lines on every qualifying pre-2026 AU cash movement. LATER, alignment JEs reconcile
   the machine's GST vs what was ACTUALLY lodged/paid per historical BAS (QuickBooks era) — the same
   "machine-true first, align to filed reality second" pattern as H1. SG posts NO GST (not registered).
2. **The ~500–600 unpairable invoices:** give up pair-by-pair; book their payments as direct vendor
   expenses (counterparty + COA via the engine; the vendor gate/registrations now correct). Invoices
   marked settled-historically without pairing.
3. **Year-end closes: yes, per jurisdiction.** AU FY = 1 Jul – 30 Jun; SG FY = calendar (confirm
   entity FYE). Each completed year closes P&L → Retained Earnings per its own jurisdictional year.
   Mechanics to be designed with Kaveesh where tax-sensitive.
4. **Cadence: ONE YEAR at a time** (not months), account-by-account within the year is fine, BUT the
   economic-events lane runs in the SAME year pass — a year only closes when EVERYTHING in it is
   booked. Any paired-but-unposted items inside the year must post as part of that year's pass.
5. **HTML scorecard per year, BEFORE posting.** After the shadow run: a self-contained HTML report —
   what was categorized how, pairing outcomes (correct / suspect / failed), verdict mix, counterparty
   concentrations, invariant results, open questions. Gaurav reviews, gives feedback (mis-pairs,
   mis-recognitions), feedback goes back into rules/corpus, re-run, THEN post.

## Pipeline (per year; chronological from the earliest year forward, since each close feeds the next opening)

1. **Shadow run** — lift POL-28 for the batch; engine → DRAFTS only. Rules → AI → NEEDS_REVIEW.
   Economic-events lane staged + projected in shadow for the same year.
2. **Agent lanes (parallel):** rule-hit spot-checks · adversarial review of every AI classification ·
   needs-review resolution proposals · transfer-integrity (both legs of every internal transfer pair
   across accounts) · invoice-settlement lane (ruling 2).
3. **Hard gates:** batch balances · post-batch ledger == running balance at each month-end (the
   per-date tripwire) · entry dates inside the window · AU GST lines present & SG absent (ruling 1).
4. **HTML scorecard** → Gaurav feedback → corrections re-run (repeat until accepted).
5. **Post** (supervised, VR-1c) → **re-park**: recompute `pre_books_park` + shrink the opening JEs'
   coverage as real history lands beneath them; when a year/account completes, its park slice retires.
6. **Year close** (ruling 3) → next year.

**Order of attack:** dormant/small Wise accounts (shakedown) → OCBC Main → DBS → Wise SGD/AUD → CBA →
Stripe (two-lane replay) — repeated per year.

**Monitoring:** every batch writes a scorecard row (account, period, counts, verdict mix, invariants,
agent sign-offs, human sign-off) — the recon dashboard; nothing finalizes without green gates.

## Process rule — mid-year engine fixes force a full-year re-sweep (Gaurav defect find, 2026-08-16)

An engine/rule fix landed MID-YEAR-PASS applies only to txns still open at fix time; everything already
booked under the old behavior stays wrong silently (the four Dirk-Jan loans-in sat in 4025 Incidentals
Revenue via the catch-all rule because the blank-counterparty-name defect bypassed rule 385, and only the
two still-open stragglers got the fixed matcher). RULE: after ANY engine or rule-infrastructure fix during
a year pass, re-sweep the WHOLE year for the defect class (query for victims, void + re-run through the
fixed engine) BEFORE regenerating the scorecard. A verdict Gaurav already gave (rules 385/386) that fails
to apply is a DEFECT, not feedback to re-collect.
