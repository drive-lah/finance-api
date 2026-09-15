# The New Finance System — Starter Guide for Zilla & Rahul

Welcome! This is **Version 1** of our new finance system. From launch day, the team does all
day-to-day finance work here — raising invoices, claims, host payouts and charges, guest
refunds, and tracking all of it. One system, one queue, no more Retool for payments.

*One honest note up front: some pieces (like how incidents are categorised) will get smarter
soon, and parts of the incident flow will later move into TMS. Everything you learn here stays —
the screens and the way of working don't change.*

---

## What the system does

**Raise a request** (Finance → Requests → Raise a request). Four cards:

- **Vendor invoice** — upload the document; the system reads it automatically (vendor, amount,
  date), you confirm, and it goes for approval. If the vendor is new, the invoice waits in draft
  until finance approves the vendor — then it submits itself.
- **Host payout** — pick what you're paying for (Tolls, Damage, Cleanliness, Fuel shortage,
  Excess mileage, Late return, Flex+, Referral, Misc…), enter the Host ID and amount. For
  anything trip-related you'll need the **Trip ID (TA…/TS…) and the Intercom ticket number** —
  Flex+/Referral don't need either. The system checks everything live as you type: it shows you
  the host's name, the trip's car and dates, the ticket's title. Green ticks = you've got the
  right ones.
- **Host charge** — billing a host (fuel charge / misc). Enter the amount as a normal positive
  number; the system handles the rest.
- **Guest refund** — pick the incident, enter the Guest ID, trip and ticket. On approval the
  money goes back automatically to the exact card the guest paid with. You never handle bank
  details.

You can also **attach photos, quotes and receipts** to host/guest requests — please do, the
approver sees them.

**Track everything you raised** (Requests → Track): every request with its live status and WHO
it's sitting with. Made a mistake? Hit **Void** on your own request while it's still open, then
raise a fresh one. (Requests can't be edited after raising — void and re-raise, so the record
stays clean.)

**My Claims**: personal expense claims — file, your manager approves, finance pays.

**My Tasks**: everything waiting on YOU. Approvals show a card with a plain-English summary of
what the payment is about, a confidence score, and red flags if something doesn't add up (wrong
ticket for the trip, possible duplicate, unexplained amount). The card does the homework; you
make the call.

**Golden rule built into the system**: whoever raises a request can never approve it — the
system physically won't allow it, for anyone, including admins.

---

## Zilla — Finance Lead

- **You are the approver for now.** Every payout, charge, refund and invoice approval lands in
  YOUR My Tasks by name. Work the queue daily; the card gives you the story, links to the
  Intercom ticket, the attachments, and flags. Approve, Reject (with reason), or Reassign (with
  a note) — those are your three buttons.
- If YOU raise something, it routes to Dirk-Jan automatically — the system never lets you
  approve your own.
- **This is temporary by design**: once we set up the approval matrix (who approves what, by
  category and amount), approvals route to the right people and you step out of being the
  default.
- **New vendors**: you approve them before their invoices can move. Vendor bank accounts are a
  separate, stricter step — and there's a working session coming to confirm the existing Wise
  recipient list (about 120 entries to eyeball).
- When something's paid outside the system in the transition, record it against the request
  (mark-executed) so tracking stays truthful.

## Rahul — HR Lead

- **You own people-data truth**: employees, their details, compensation. The payroll run is only
  ever as correct as this data — keeping it accurate IS the job.
- ~24 staff still need their details completed before the first payroll run through the system;
  that fill-list is the priority.
- Claims from your team route to their manager (you, mostly) for approval before finance pays.
- Payroll goes live as its own step shortly after launch: first a supervised pilot run, then
  monthly as normal.

---

## Playing around (please do!)

Until we flip the switch, the system runs in **rehearsal mode**: everything works — raising,
approving, voiding, tracking — but **no real money moves anywhere**. Break things. Raise a fake
host payout to a real host, approve each other's requests, void something, attach a photo, watch
the card judge your fake request. Write down every question and everything that feels confusing:
your questions shape the team training.

*Version 1 · September 2026 · questions → Gaurav*
