# PRD: Invoice / Accounts Payable System

**Version:** 1.1
**Date:** 2026-03-11
**Status:** Draft — Open questions resolved
**Author:** Gaurav / Kai

---

## 1. Introduction / Overview

Drive Lah currently has no invoice management system. When vendor invoices arrive (via email, forwarded to a finance admin), there is no structured intake, no approval process, and no automated accounting. Payments are recorded as simple cash-basis expenses when the bank transaction is imported — which is wrong for accrual accounting and makes it impossible to track what is owed to vendors (AP) at any point in time.

This feature builds an AI-led invoice management system. A human uploads the PDF invoice in one place. From that point, the system:
- Extracts all invoice data via AI
- Identifies or creates the vendor counterparty
- Determines the correct COA account
- Identifies the service period (for accruals/amortization)
- Validates the invoice against known contracts or expected recurring amounts
- Routes it for approval via Slack (or auto-approves based on configured rules)
- Creates the correct journal entry on approval
- Automatically knocks off the AP liability when the payment hits the bank

**Goal:** Finance team uploads a PDF. Everything else is automatic.

---

## 2. Goals

1. Eliminate manual journal entry creation for vendor invoices
2. Maintain a real-time accounts payable ledger (what we owe and to whom)
3. Correctly handle accrual accounting — expense is recognised in the service period, not the payment date
4. Validate invoices against contracts/expected amounts before approval
5. Route approvals via Slack with configurable rules by category and amount
6. Auto-approve invoices that match known patterns within configured thresholds
7. Automatically knock off AP when a matching bank payment is detected

---

## 3. User Stories

**As a finance admin,** I want to upload a vendor invoice PDF and have the system extract all the details automatically, so I don't need to manually key in any data.

**As a finance admin,** I want the system to flag if an invoice amount looks wrong relative to our contract or last payment to that vendor, so I catch billing errors before approval.

**As an approver,** I want to receive a Slack notification with the invoice summary and click Approve or Reject directly in Slack, so I don't need to log into a separate system.

**As a finance manager,** I want recurring invoices from known vendors within their contracted amount to be auto-approved with no intervention, so routine payments flow without manual steps.

**As a CFO,** I want to see exactly what the company owes at any point (AP aging: current, 30, 60, 90+ days), so I can manage cash flow.

**As a developer of the categorization engine,** I want bank transactions for vendor payments to automatically knock off the AP liability rather than double-booking the expense, so the books are correct.

---

## 4. Functional Requirements

### 4.1 Invoice Upload

1. The system must provide a single upload interface in the Finance admin UI (Invoices tab) where any PDF invoice can be uploaded.
2. Upload must accept PDF files and support multi-page invoices.
3. On upload, the system must immediately pass the PDF to an AI extraction pipeline — the admin does not fill in any fields manually.
4. The system must show a processing state while extraction runs, then present the extracted result for quick review before submission.

### 4.2 AI Extraction

5. The AI must extract the following fields from the invoice PDF:
   - Vendor name
   - Invoice number
   - Invoice date
   - Due date (if present; default: invoice date + 30 days)
   - Total amount
   - **Invoice currency** (stored as-is — not converted. Payment may be made in a different currency.)
   - Line item descriptions (summary — not individual line coding)
   - Service period (start date / end date — if stated anywhere in the invoice, e.g. "for the period Jan 1 – Dec 31, 2026")
   - Any PO/reference number
   - **Entity** (extracted from invoice header, or matched from vendor history — see requirement 9A)

6. The AI must match the extracted vendor name against the existing `finance_counterparties` directory.
   - If a match is found (exact or fuzzy) → link the invoice to that counterparty.
   - If no match is found → automatically create a new counterparty record with `type = vendor`, using the name extracted from the invoice. Flag the new vendor for review in the UI.

7. The AI must suggest a COA contra account code based on:
   - The vendor's `default_account_code` (if set)
   - The vendor's type and past categorization history
   - The invoice description / line item summary
   - The AI should provide a confidence level with the suggestion. Low-confidence suggestions are flagged for human selection.

8. If a service period is identified and it spans more than one calendar month, the system must flag the invoice for amortization (prepayment schedule) rather than single-period expense recognition.

9. The admin must be able to review and correct any AI-extracted field before submitting the invoice for approval. The UI must present all extracted fields as editable inputs pre-filled with the AI's output. Corrections are tracked (original AI value vs human-corrected value) for model improvement.

9A. The AI must identify the entity the invoice belongs to based on:
   - The entity name / address on the invoice (if present)
   - The vendor's historical entity association
   - If ambiguous, the uploader must select the entity before the invoice can be submitted. Entity selection is mandatory — an invoice cannot proceed without one.

9B. The invoice's `total_amount` and `currency` are stored exactly as extracted from the invoice. The AP journal entry is posted in the invoice currency. When payment is made in a different currency (e.g., invoice in USD, paid from SGD account), the FX difference is recorded as FX gain/loss at payment time — not at invoice time.

### 4.2A Approval Rules Configuration

The system must provide a UI for finance to configure approval rules and approver mappings. This is not hardcoded — it is managed as data.

9C. Finance admins must be able to create and edit approval rules specifying:
   - Conditions: COA account prefix, amount range (min/max), entity, vendor type
   - Action: `auto_approve` or `require_approval`
   - Approver: Slack user ID and/or channel (for `require_approval`)
   - Timeout: days before escalation
   - Priority: order in which rules are evaluated

9D. A global auto-approve amount threshold (invoices below $X always auto-approve regardless of vendor/category) is configurable but not required at launch. Can be set to $0 (disabled) initially.

---

### 4.3 Contract / Commitment Tracking

10. The system must support a `Contracts` module where finance can register:
    - Vendor (counterparty)
    - Entity
    - Contract type: `subscription` (auto-renewing) or `fixed_term`
    - Expected amount (or range: min/max)
    - Frequency: monthly / quarterly / annual / one-off
    - Contract start date and end date (nullable for open-ended subscriptions)
    - COA account code
    - Auto-approve flag: yes/no
    - Auto-approve tolerance: percentage variance allowed (e.g., ±10%)
    - Notes / contract reference

11. For vendors with no formal contract, the system must support a `RecurringExpectation` record (same structure, just without a contract document) — representing informal knowledge of expected recurring payments (e.g., "we expect ~$3,000/month from AWS").

12. When an invoice is processed, the AI must check whether it matches a known contract or recurring expectation:
    - Same vendor
    - Amount within the configured tolerance
    - Invoice date within the expected frequency window
    If matched → flag as `contract_matched` and eligible for auto-approval.
    If amount exceeds tolerance or vendor is new → flag as `requires_review`.

### 4.4 Approval Workflow

13. The system must support configurable approval rules. Each rule defines:
    - Trigger conditions: category (COA account range), amount threshold (e.g., > $5,000), vendor type, entity
    - Action: `auto_approve` or `send_to_approver`
    - Approver: Slack user ID or channel

14. Rules are evaluated in priority order. First matching rule determines the outcome.

15. For invoices routed to an approver, the system must send a Slack message to the configured approver containing:
    - Vendor name
    - Invoice number and date
    - Amount and currency
    - Entity
    - COA account (suggested)
    - Service period (if applicable)
    - Contract match status (matched / new / amount variance flagged)
    - Two inline buttons: **Approve** and **Reject**

16. Clicking Approve in Slack must:
    - Mark the invoice as `Approved`
    - Trigger journal entry creation (see 4.5)
    - Log who approved and at what time

17. Clicking Reject in Slack must:
    - Mark the invoice as `Rejected`
    - Notify the finance admin who uploaded it
    - Allow the admin to void or re-submit with corrections

18. Auto-approved invoices (from rule 13 or contract match) must skip Slack and immediately proceed to journal entry creation. An automated Slack notification must be sent confirming the auto-approval (to a configured finance channel), not requiring action.

19. Approvals must have a timeout: if no response within N days (configurable), escalate to a secondary approver or notify the finance admin.

### 4.5 Journal Entry Creation on Approval

20. On approval, the system must create a journal entry:
    - **Standard invoice (single period):**
      ```
      Dr [contra_account_code]  $X    (expense)
      Cr 2000 Accounts Payable  $X
      ```
    - **Invoice with service period spanning multiple months:**
      ```
      Dr 1200 Prepaid Expenses  $X    (not the expense account yet)
      Cr 2000 Accounts Payable  $X
      ```
      Plus an amortization schedule: N monthly entries of Dr [expense account] / Cr Prepaid. Each monthly entry can be configured in one of two modes:
      - **Auto-post:** Entry posts automatically on the 1st of each month, no approval needed. A Slack notification is sent to the finance channel confirming the posting.
      - **Stage for approval:** Entry is created as a draft and a Slack message is sent for one-click approval before posting.
      The mode is configurable per contract / per invoice at amortization schedule creation time.

21. The JE entry date must be the invoice date (not the payment date).

22. For invoices covering a service period with GST, GST must be applied at approval time following the existing GST logic (entity rate + account gst_applicable + invoice-level override).

### 4.6 AP Knock-off (Bank Transaction Matching)

23. When a bank transaction is imported and enters the categorization engine, the engine must check whether it matches an open AP invoice:
    - Same counterparty (by counterparty_id)
    - Amount matches (exact or within a small tolerance for FX/rounding)
    - Transaction date >= invoice date
    If matched → create the payment JE automatically:
    ```
    Dr 2000 Accounts Payable  $X
    Cr [bank_coa_code]        $X
    ```
    Mark invoice as `Paid`. Mark bank transaction as `Reconciled`.

24. If multiple open AP invoices exist for the same vendor, the system must match the oldest outstanding invoice first (FIFO).

25. Partial payments must be supported: if bank transaction amount < invoice amount, mark invoice as `Partially Paid`, create partial AP knock-off JE, leave remaining AP balance open.

### 4.7 Invoice Status Lifecycle

26. The invoice must progress through the following statuses:
    ```
    Draft           → AI extracted, under review / awaiting submission
    Pending Approval → Sent to Slack approver (or auto-approve check running)
    Approved        → JE created, AP liability on books
    Partially Paid  → Some payment received, AP balance remains
    Paid            → AP fully cleared by bank transaction matching
    Rejected        → Declined by approver
    Void            → Cancelled after creation
    ```

### 4.8 AP Aging & Reporting

27. The system must provide an AP aging view showing all outstanding AP invoices grouped by:
    - Current (not yet due)
    - 1–30 days overdue
    - 31–60 days overdue
    - 61–90 days overdue
    - 90+ days overdue
    Filterable by entity and vendor.

28. Each invoice must display: vendor, invoice number, invoice date, due date, amount, amount paid, amount outstanding, days overdue.

---

## 5. Non-Goals (Out of Scope)

- **Purchase Orders (POs):** Not required at current scale. Invoice-first workflow is sufficient.
- **Payment initiation:** The system records that an invoice was paid (via bank transaction match), but does not initiate bank transfers. Humans initiate payments via banking portal.
- **Accounts Receivable (AR):** This PRD covers AP (what we owe). AR (what customers owe us) is a separate future module.
- **Multi-line COA coding per invoice:** One contra account per invoice. Line-item level COA splitting is out of scope.
- **OCR on non-PDF formats:** Only PDF invoices. Image-only scans (JPEG, PNG) are out of scope for now.
- **Vendor payment terms negotiation or tracking:** We store `payment_terms_days` on the counterparty but this PRD doesn't build a payment terms workflow.
- **Multi-currency invoices** (invoice with mixed currencies): out of scope. Invoice is in one currency.

---

## 6. Technical Considerations

### Data Model (new tables)

```
finance_invoices
├── id
├── entity_id                   FK → finance_entities
├── counterparty_id             FK → finance_counterparties
├── contract_id                 FK → finance_contracts (nullable)
├── invoice_number
├── invoice_date
├── due_date
├── total_amount
├── amount_paid
├── currency
├── contra_account_code         COA code for the expense/prepaid side
├── status                      draft | pending_approval | approved | partially_paid | paid | rejected | void
├── service_period_start        (nullable — for accrual/amortization)
├── service_period_end          (nullable)
├── has_amortization_schedule   bool
├── journal_entry_id            FK → created on approval
├── ai_extraction_raw           JSONB (raw AI output for audit)
├── ai_confidence_score         float (0-1)
├── contract_matched            bool
├── approved_by                 Slack user ID
├── approved_at
├── rejection_reason
├── uploaded_by
├── pdf_storage_path
├── created_at
└── updated_at

finance_contracts
├── id
├── entity_id                   FK → finance_entities
├── counterparty_id             FK → finance_counterparties
├── contract_type               subscription | fixed_term | recurring_expectation
├── expected_amount_min         (nullable)
├── expected_amount_max         (nullable)
├── frequency                   monthly | quarterly | annual | one_off
├── start_date
├── end_date                    (nullable — open-ended)
├── coa_account_code
├── auto_approve                bool
├── auto_approve_tolerance_pct  float (e.g., 0.10 = ±10%)
├── notes
├── status                      active | inactive
├── created_at
└── updated_at

finance_approval_rules
├── id
├── priority                    int (lower = higher priority)
├── entity_id                   (nullable — null = all entities)
├── coa_account_prefix          (nullable — e.g., "67" matches all 67xx accounts)
├── amount_threshold            (nullable — rule applies when amount > this)
├── vendor_type                 (nullable — vendor | employee | etc.)
├── action                      auto_approve | send_to_approver
├── approver_slack_id           (nullable)
├── approver_slack_channel      (nullable)
├── timeout_days                int (default 3)
├── escalation_slack_id         (nullable)
└── created_at

finance_amortization_schedules
├── id
├── invoice_id                  FK → finance_invoices
├── total_amount
├── months                      int
├── expense_account_code
├── prepaid_account_code        (default 1200)
├── start_month                 date (first month of amortization)
├── entries_posted              int (how many monthly JEs done)
└── created_at
```

### Integrations

- **AI extraction:** Claude API (via PAI Inference tool) — prompt includes the extracted PDF text + COA + counterparty list as context
- **Slack:** Slack Bot API — interactive messages with Approve/Reject buttons. Webhook endpoint to receive button clicks. `SLACK_BOT_TOKEN` env var (token to be provided). Approval rules and approver mappings are configured in the system UI (not hardcoded).
- **Categorization engine:** AP knock-off logic added as Phase 0 before existing Phase 1 (counterparty enrichment) — check open AP invoices first
- **PDF storage:** Files stored in AWS S3. Path (S3 key) saved in invoice record. `AWS_S3_BUCKET` and `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars required. Pre-signed URLs used for serving PDFs to the UI.

### Dependencies

- Existing `finance_counterparties` table — vendor auto-creation uses this
- Existing `finance_accounts` (COA) — for COA suggestion and validation
- Existing `finance_journal_entries` — JE created on approval
- Existing categorization engine — AP knock-off is an extension of Phase 2
- `WISE_API_KEY` env var pattern — similarly, `SLACK_BOT_TOKEN` needed

---

## 7. Success Metrics

- **% of invoices auto-approved** (target: >60% within 3 months of go-live)
- **% of invoices with correct COA on first extraction** (target: >85%)
- **Time from invoice upload to AP entry creation** (target: <5 minutes for auto-approve path)
- **AP aging: zero invoices >60 days unpaid** without active flag/escalation
- **Zero double-booked vendor expenses** (bank transaction correctly knocks off AP rather than re-booking expense)

---

## 8. Open Questions

| # | Question | Status | Answer |
|---|----------|--------|--------|
| 1 | PDF storage location | ✅ Resolved | AWS S3. Pre-signed URLs for serving. Env vars: `AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. |
| 2 | Slack bot | ⏳ Deferred | Slack app creation deferred. Workflow built first, token plugged in when app is ready. |
| 3 | Approver mapping | ✅ Resolved | Built as configurable approval rules in the UI (not hardcoded). Finance team populates rules at setup. |
| 4 | Global auto-approve threshold | ✅ Resolved | Configurable, defaults to disabled ($0). Define specific thresholds at setup time. |
| 5 | FX invoices | ✅ Resolved | Invoice stored in invoice currency. JE posted in invoice currency. FX diff recorded at payment time. Entity identified first before any FX consideration. |
| 6 | Amortization posting mode | ✅ Resolved | Both modes supported. Configurable per contract/invoice: auto-post (with Slack notification) or staged for one-click Slack approval. |
| 7 | Invoice corrections | ✅ Resolved | Yes — all AI-extracted fields are editable in Draft state before submission. Original AI values retained for audit. |

### Remaining unknowns (pre-build decisions needed)

- **AWS S3 setup:** S3 bucket name and IAM credentials needed before upload endpoint is built. (`AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` env vars.)
- **Slack app creation:** Deferred. Slack approval workflow will be built but not wired until the Slack app is created. The approval rules engine and Slack message formatting will be built first; bot token plugged in when ready.
- **FX gain/loss account:** ✅ Confirmed as `7100 FX Gains/Losses` from existing COA.
