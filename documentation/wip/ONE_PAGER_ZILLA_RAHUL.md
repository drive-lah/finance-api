# The New Finance System — Guide for Zilla & Rahul

This is our new finance module inside Admin Controls. From launch, ALL finance work happens
here: invoices, employee claims, host payouts, host charges, guest refunds, and employee
management. **We plan to retire Retool on Monday, 21 September** — after that, everything goes
through this system only.

*One thing to know: the host payout, host charge and guest refund screens are **temporary
helpers**. Once our Trip Management System launches, those become automated and these screens
retire. Everything else — invoices, claims, payments, tracking — is permanent and only grows.*

---

## The blocks, very simply

**Invoices** — every vendor invoice gets added HERE, by everyone. Upload the document, the
system reads it (vendor, amount, date), you confirm, it goes for approval. New vendors must be
registered and approved by finance before their first invoice can move — the invoice waits in
draft and submits itself the moment the vendor is approved.

**Invoice payments** — paid via Wise, but **from this console only**. Zilla makes the payment
from inside the system; never directly in Wise. That's how every payment stays tracked,
approved, and reconciled automatically.

**Employee claims** — personal expenses. File the claim, your manager approves, finance pays.
You watch its status the whole way.

**Host payouts & host charges** *(temporary until TMS)* — pick the payout type (tolls, damage,
cleanliness, fuel, Flex+…), enter the Host ID and amount. Trip-related types need the Trip ID
(TA…/TS…) and the Intercom ticket number; contractual ones (Flex+, referral) need neither.
**On approval, the entry goes straight into the monthly payout sheet and gets paid in the
monthly cycle.** Charges work the same, as negative entries.

**Guest refunds** *(temporary until TMS)* — pick the incident, enter Guest ID, trip and ticket.
On approval the money is refunded automatically to the exact card the guest paid with.

**Employee management** — Rahul's block: employee records and compensation data, feeding
payroll. Payroll itself goes live as its own step shortly after launch.

---

## Approvals, permissions, tracking

- **Approval cards**: every request creates a card for the approver — a plain-English summary
  of what the payment is about, the trip and ticket details, attachments, a confidence score,
  and red flags if something doesn't add up. The card does the homework; the approver decides.
- **For now, Zilla is the default approver for everything.** That changes before full team
  launch, when we configure who approves what — then approvals route to the right person
  automatically and Zilla steps out of the default seat.
- **The one unbreakable rule**: whoever raises a request can never approve it. The system
  refuses — for everyone, including admins. (If Zilla raises something, it routes to Dirk-Jan.)
- **Permissions are scoped** — nobody sees everything. Everyone can raise requests and track
  their own; approving, paying, and settings need the right role.
- **Tracking**: Requests → Track shows everything YOU raised, its live status, and who it's
  sitting with — including payment status after approval (host payouts show as queued in the
  monthly sheet, then paid). Made a mistake? **Void** your own request while it's open and
  raise a fresh one.

---

## Your mission this week (rehearsal mode — no real money moves)

The system is live but in rehearsal: everything works end to end, **zero dollars move**. We
want you to genuinely play — and feel free to pull in one more team member. Do at least:

1. **Raise an employee claim** → watch it route for approval
2. **Upload a vendor invoice** (any real PDF) → confirm the auto-extraction → send for approval
3. **Try paying an approved invoice** from the console (it will simulate, not pay)
4. **Raise a host payout** — use a real Host ID, a real TA trip and its ticket; watch the green
   ticks appear as you type
5. **Raise a host charge** and a **guest refund** the same way
6. **Attach a photo or PDF** to one of them
7. **Approve each other's requests** — look at the approval card: does the summary tell you
   enough to decide? What's missing?
8. **Try to approve your own** — see the system refuse
9. **Void** one of your own requests
10. Check **Track** — is it obvious where everything stands?

Then tell Gaurav: what confused you, what's missing, what you'd change. Your questions become
the team's training guide.

*Version 1 · September 2026 · questions → Gaurav*
