# Pre-launch → Cutover Checklist (2026-09-15)

> The technical runway that runs IN PARALLEL with the Zilla/Rahul briefing stage.
> Launch = both tracks done. Owner G = Gaurav decision/phrase · P = Pickle · Z = Zilla · R = Rahul.

## A. Ship the code (blocks everything)
- [ ] **G**: say the push phrase → P pushes 3 branches (finance-api / admin-bff / admincontrols, all `20260904-gs-finance-launch`)
- [ ] **P**: PRs + review + merge + deploy (Render auto-deploy on main)
- [ ] **G+P supervised**: apply migrations **077, 078, 079, 080** to prod (incident context · sub-type · COA coverage · attachments)

## B. Prod environment/config (at deploy)
- [ ] `IMS_CONFIG_DATABASE_URL` — PROD IMS database URL (psycopg2 form, `options=-csearch_path=incidentals_service`; local used the dev RDS)
- [ ] `AWS_S3_BUCKET=drivelah-finance-invoices` (attachments; key already present)
- [ ] `INTERCOM_APP_ID=q8nq4c01` (card deep links; defaulted in code, set for clarity)
- [ ] Entry-sheet base URLs are baked (payout-prod.drivemate.au / payout-service.drivelah.sg), env-overridable — nothing to do unless overriding
- [ ] `DEFAULT_APPROVER_CHAIN` — defaulted (zilla→dirkjan→gauravs); set only to change order

## C. Safety gates (deploy SAFE, arm LATER deliberately)
- [ ] Deploy with `INCIDENT_RAIL_DRY_RUN` unset (=ON) — prod becomes the team's SANDBOX: full flows, zero money
- [ ] **G supervised**: arm `INCIDENT_RAIL_DRY_RUN=0` + one $1-class real host entry verified in the live sheet mirror + one small real guest refund verified in Stripe
- [ ] **G supervised**: arm `PAYOUT_DRY_RUN=0` (Wise invoice rail) + one supervised real payment
- [ ] **G**: yes/no on cancelling the 15 Penny intake tasks + 31 stale invoice-approval backfill tasks on prod (awaiting since 15/09)

## D. Access + gates hygiene
- [ ] **P**: retarget vendor-approval tasks off `finance.invoices` (whole team holds it) to the finance circle — FOUND 15/09, one-line fix
- [ ] **P**: M5 route-gate flip off legacy flat `finance` + drop shim (L-2b/L-8)
- [ ] **P**: grant `finance.settings` to Gaurav / DJ / Zilla

## E. Data/ops work-sessions (people, not code)
- [ ] **Z (+P prepares list)**: PM-5b — confirm the ~119 unvalidated Wise recipients → counterparty bank accounts (2/121 done; blocks paying vendors at scale)
- [ ] **P**: inventory + port pending Retool payment requests/invoices with correct statuses (L-5); announce Retool cutoff date
- [ ] **G+P**: auto-ingestion scoping chat (what auto-ingests vs parks), then ship the gated version (L-6)
- [ ] **R**: HR fill-list — remaining ~24 staff comp data → first supervised payroll pilot (own track, not launch-gating)
- [ ] **G**: config workbook (`INCIDENT_COA_APPROVER_CONFIG.xlsx`) filled → P loads matrix + coverage (this retires Zilla-as-default-approver) — V2, not launch-gating

## F. Briefing stage (this week)
- [ ] **G**: hand Zilla + Rahul the one-pager (`documentation/wip/ONE_PAGER_ZILLA_RAHUL.md`)
- [ ] **Z+R**: play with the deployed dry-run system — raise invoices, host payouts/charges, guest refunds, claims; approve each other's; void own; check Track and My Tasks
- [ ] **G/P**: Q&A pass; every question answered before team rollout
- [ ] Team rollout announcement: "from <date>, everything through the system; Retool retires <date>"

## Not launch-gating (parallel/after)
- Settlement pairing job (auto-`paid` off sheet mirror + Stripe) — V1.1, P builds during training week
- Claims parity (attachments + controller card) — small ports
- Trip deep-link on the card — waiting on the console URL pattern from G
- Recon 2020–2025 — separate exercise by standing ruling
