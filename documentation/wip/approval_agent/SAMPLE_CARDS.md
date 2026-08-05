# Approval Agent — sample cards (v0.1)


## Invoice 2480 — Focussed Assessing Pty Ltd
```
```
INVOICE (reality): Focussed Assessing Pty Ltd · AUD 330.00 · COA: not assigned · Drive lah Australia · Invoice #4031578

WHY ARE WE PAYING THIS: During a trip on 1RT7DP (2016 Nissan Pulsar, host Vishal Kamra), guest
Peter Tournier caused a total-loss multi-vehicle accident on 7 March 2026, including a non-return
/ stolen-vehicle element. Drive lah engaged Focussed Assessing Pty Ltd (ABN 92 143 234 066) to
conduct a professional assessment of the vehicle — standard procedure in total-loss claims to
establish market value before settling with the host. The $330 assessment fee is the vendor's
charge for that work. Drive lah bears this cost because the guest's card was declined on every
recovery attempt (including a $338.25 attempt that included a $8.25 processing fee); the debt
remains unrecovered and Drive lah must absorb the cost.

SUPPORTING FACTS:
  - Rego 1RT7DP → 2016 Nissan Pulsar (VIC), host: Vishal Kamra (vishalkamra2006@gmail.com)
  - Guest: Peter Tournier; guest code ITW2JWNX-0002
  - Trip reference in ticket: TA30382150 (trip_id not resolved in schema)
  - Relevant booking window: 2026-03-05 20:00 → 2026-03-07 08:00 (lastTransition:
    expire-review-period); a follow-on booking 2026-03-07 18:30 → 2026-03-09 10:30 was
    withdrawn by admin — consistent with a mid-trip incident / non-return scenario (source: TRIP)
  - Incident: multi-vehicle accident, front-end destruction, airbag deployment, rear bumper
    detached, rear windscreen shattered, metal pole in rear; stolen plate WIX229 found on vehicle
    (source: ticket 94099654)
  - Assessed market value: $8,250 incl. GST ($7,425 ex-GST); total-loss payout to host: $5,250
    + $75 inconvenience fee (source: ticket 94099654)
  - This $330 invoice corresponds to the "payment request ID 30,177 for $330 component"
    referenced in the ticket (source: ticket 94099654)
  - Ticket 94099654 state: resolved (56 parts; pre-summarised)
  - Requester approval note: "Within financial limit"
  - Team remarks: "$8.25 processing fee applied on top; total attempted guest charge $338.25"
  - COA: not assigned — will need coding before posting

RISK FLAGS:
  - guest-recovery: DECLINED — charge of $338.25 attempted against Peter Tournier; outcome:
    declined. All card attempts across the total-loss case failed (source: ticket 94099654).
    ⚠ Drive lah absorbs this $330. Approver must confirm this is a legitimate write-off.
  - duplicate/already-paid: CLEAR — no prior payment found in ledger or bank transactions
    (source: ALREADY_PAID)
  - amount vs ticket: MATCH — ticket references a $330 payment request (ID 30,177); invoice is
    AUD 330.00. Consistent.
  - approval trail: PARTIAL — approval note "Within financial limit" is present, but COA is null
    (not assigned). Cannot be posted to ledger until COA is coded. ⚠

RECOMMENDATION: HOLD-FOR-INFO
  Reason: Amount is verified, not a duplicate, and assessment work is legitimate — but COA is
  unassigned (cannot post) and the $330 is a confirmed write-off (guest card declined, debt
  unrecovered). Approver should (1) assign COA before releasing payment, and (2) explicitly
  sign off that this is an approved bad-debt / operational write-off per Drive lah's total-loss
  cost-absorption policy.
```
```
