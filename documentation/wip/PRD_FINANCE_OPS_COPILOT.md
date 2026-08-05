# PRD — Finance Ops Copilot

> **Version** 0.1 (draft) · **Author** Gaurav (via Pickle) · **Date** 2026-08-04 · **Status** For review
> **Scope** The manager-in-the-loop AI layer over finance-api. NOT a PRD for the ledger itself — the ledger's target lives in `IDEAL_STATE.md`; this doc specifies the *copilot* that drives it.
> **Companion docs** `IDEAL_STATE.md` (technical target) · `STATUS.md` (progress) · `KNOWLEDGE.md` (business facts / RAG corpus). This PRD does not restate them; it references their IDs.

---

## 1. Problem & the one metric

Finance closes SG + AU by hand. Every month, someone categorizes thousands of bank/Stripe transactions, chases invoice knock-offs, reconciles cash, and assembles a consolidated USD P&L — mostly by eyeballing bank text and remembering what a counterparty means. It is slow, it doesn't scale with volume, and the knowledge lives in one or two heads.

The engine to fix this largely exists (categorization cascade, ledger, RAG). What's missing is the **product around it**: the surface where a finance manager reviews what the AI proposes, approves in one click, and trusts that nothing wrong reached the ledger.

**The one metric:** **time-to-close at audited accuracy.** Concretely — *the % of a period's transactions posted correctly without a human typing a journal entry, holding categorization accuracy at or above the human baseline.* Everything else (latency, manual-queue length) is a sub-lever. The Lantern lesson applies directly: value is time-to-resolution at high accuracy — launch, then grind down the time of each step.

**Falsifier for the whole product:** if a finance manager still opens a spreadsheet to close the month, the copilot has failed regardless of how good the classifier is.

---

## 2. Users & jobs-to-be-done

| User | Job today | What the copilot changes |
|------|-----------|--------------------------|
| **Finance manager / controller** | Categorize, reconcile, approve JEs, close the period | Reviews AI proposals with evidence; approves or corrects; owns the ledger gate |
| **Ops / bookkeeper** | Feed source data, chase invoices, flag oddities | Asks the copilot open questions across sources; handles escalations |
| **Gaurav / director** | Spot-check trust, sign off on the close | Reads the audit log + risk view; approves high-risk items only |

The copilot's users are **finance decision-makers, not engineers**. Every surface is designed for the person who clicks *approve*, echoing Lantern's manager-in-the-loop centre of gravity.

---

## 3. The autonomy model  ← the heart of this PRD

Every action the copilot can take carries an autonomy level and a guardrail. This table *is* the trust contract — it's what lets the manager leave routine work on autopilot without fear.

| Action | Default autonomy | Guardrail / cap |
|--------|------------------|-----------------|
| **Deterministic categorization** (internal-transfer & exact-match rules, cascade tier 1) | `auto` — posts a DRAFT JE | Counterparty-independent, exact, auditable (IDEAL_STATE §3 cascade). Never touches the ledger gate — still DRAFT until reconciled. |
| **Counterparty enrichment** (L1→L2→L3) | `auto` | Enrichment only writes the counterparty, never a posted JE. |
| **AP / payroll knock-off match** | `propose-&-approve` | **Deterministic match, AI never decides it** (IDEAL_STATE §3). Human confirms the settle. |
| **AI long-tail categorization** (RAG-grounded fallback) | `propose-&-approve` | Below confidence threshold → queued for human confirmation; every confirmation becomes a new RAG example. |
| **Reconcile / post** (DRAFT → POSTED, the ledger gate) | `always-ask` | **No AI posts to the ledger.** A human flips DRAFT→POSTED. Hard rule, see §7. |
| **Refund / dispute resolution** | `propose-&-approve` | Hard cap per case (e.g. ≤ AUD/SGD X); above cap → `always-ask`. |
| **Period close / lock** | `always-ask` | Director or controller sign-off only. |
| **Open-ended query across sources** | `auto` (read-only) | Read-only; can propose actions but executes none without its action's own autonomy level. |

**The placement rule (from IDEAL_STATE §3):** a classifier runs *before* enrichment iff its match is counterparty-independent, else *after*. Autonomy follows the same logic — the more deterministic and reversible an action, the more autonomy it earns; anything that moves money to POSTED stays human.

---

## 4. Playbooks in scope

Each is a readable playbook the manager can reason about — not a black box.

1. **Transaction categorization** — the cascade (IDEAL_STATE §3): deterministic → enrichment → counterparty-dependent → RAG-grounded AI → human confirm. Proposes DRAFT JEs; reconciliation is the gate.
2. **Bank / cash reconciliation** — tie bank/Stripe cash to the ledger; internal transfers (Stripe→OCBC) matched automatically.
3. **AP invoice knock-off** — deterministic 3-case match (invoice-ref+amount / FIFO / park-to-1300), cross-entity IC pairs, order-independent retroactive knock-off. **AI upstream only** (enrichment, fuzzy ref extraction); never posts.
4. **Insurance classification** — per POL-98: AU broker→underwriter split (premium 5035 via Penguin/DEFT; excess 5036 direct to underwriter), SG direct insurers classified by size. A worked example of "playbook + RAG facts, human confirms."
5. **Refunds & disputes** — read case → decide → draft reply → cap. The most Lantern-shaped playbook.
6. **Month-end close** — accruals, depreciation, GST, revenue recognition, consolidation to USD; produces the 5th-of-month dashboard (IDEAL_STATE one-line ideal).

Phase-1 playbooks: **1, 2, 3** (the daily grind). Phase-2: **4, 5**. Phase-3: **6** (close).

---

## 5. Evidence & explainability contract

In finance this is not a feature — it is the product. Every proposal the copilot makes ships with its receipts, because auditability is what earns the approve click and what survives an audit.

- **Every proposal carries its reasoning:** which cascade tier fired, which past confirmed categorizations were retrieved (RAG: "matched because of these txns"), which KNOWLEDGE facts grounded it. The AI is never blind and never unexplained (the "Dom Drive lah" lesson).
- **Full logbook:** every action, who/what took it, when, and the before/after ledger state. Immutable.
- **Interrogable:** the manager can ask "why did you recommend this?" and get the steps taken — mirroring Lantern's "here are the steps I took."
- **Grounded in KNOWLEDGE.md, not model recall.** Facts come from the canonical corpus; confidence requires a cited source.

---

## 6. Integrations & data sources

- **Cash rails** (permanent, provider-agnostic): Stripe SG/AU, Grab, OCBC, Wise, CBA — ingested like bank feeds (IDEAL_STATE §1).
- **Economic-event source** (swappable): ClickHouse today → PGW/TMS ledger tomorrow, behind one adapter seam.
- **The ledger:** finance-api itself (the durable target).
- **Reuse, don't rebuild** (IDEAL_STATE §1 build boundary): the copilot drives existing bank/categorization/ledger machinery; the only new surface is the review/approve/audit UX and the autonomy controller.

---

## 7. Guardrails, controls & reversibility  ← non-negotiable

Finance is the one domain where a wrong autonomous write is expensive and hard to unwind. The autonomy model (§3) is where these are enforced structurally, not by good intentions.

- **No unsupervised agent writes derived/ledger effects.** The ledger-posting path (DRAFT→POSTED) is human-supervised, foreground, always. *(Standing rule from the finance-api VR-1c load incident, 2026-08-02 — a detached loader OOM'd, mis-mapped accounts, auto-respawned, and polluted prod `finance_transactions` twice; the ledger survived only because raw ingest was JE-free and backed up.)*
- **Money-moving matches are deterministic, not AI** — AP/payroll knock-off must be exact, reproducible, auditable.
- **Hard caps** on refunds/disputes; above cap escalates to `always-ask`.
- **Segregation of duties** — the actor who proposes cannot be the actor who posts.
- **Reversible by construction** — raw ingest stays JE-free; pre-op backups + an invariant tripwire (e.g. debits=credits, control-total checks) run after any bulk operation; verify end-state directly (query the ledger), never trust an agent's "done."
- **Period locks** — a closed period is immutable.

---

## 8. Success metrics & falsifiers

| Metric | Target | Falsifier |
|--------|--------|-----------|
| **Auto-posted %** (no human JE typed) | rises month-over-month | If the manual queue isn't shrinking, the cascade isn't learning. |
| **Categorization accuracy** | ≥ human baseline | A sampled audit finds AI-posted entries wrong more often than human ones. |
| **Time-to-close** | falls toward the 5th-of-month ideal | Close still slips or needs a spreadsheet. |
| **Correction→reuse latency** | a correction is usable immediately (RAG) | The same mistake recurs after a human already fixed it once. |
| **Zero unsupervised ledger writes** | always 0 | Any POSTED JE with no human approver in the log. |

---

## 9. Non-goals

- **AI-vision damage assessment** — a *separate frontline category* (per the Lantern demo); it would feed events in, but it is not this system.
- **Replacing the cash rails** — providers are bank accounts; PGW is a future economic-event source, not a rails replacement (IDEAL_STATE §1).
- **Fine-tuning a model** — rejected for now; RAG over our own history instead (IDEAL_STATE §3).
- **A new tracker doc** — status stays in STATUS.md, facts in KNOWLEDGE.md.

---

## 10. Rollout phases

1. **Shadow mode** — copilot proposes on live data, posts nothing; measure proposed-vs-human agreement. Establishes the accuracy baseline before any autonomy is granted.
2. **Propose-only** — Phase-1 playbooks (categorize, reconcile, knock-off) surface proposals with evidence; humans approve every one. Ships the review/audit UX.
3. **Graduated autonomy** — deterministic tiers move to `auto` (DRAFT only); confidence thresholds tuned from shadow data; refund caps enabled.
4. **Close assist** — Phase-3 close playbook; the 5th-of-month dashboard.

Autonomy is *earned from measured accuracy*, never granted up front — the shadow-mode data is the gate.

---

## 11. Risks & open questions

- **Confidence threshold calibration** — where's the line between `auto`, `propose`, and `always-ask`? Set from shadow-mode data, revisited per playbook.
- **Refund cap values** — need real per-case limits for AU and SG (§3, §4.5).
- **PGW seam timing** — how much to build against ClickHouse vs waiting for the TMS ledger.
- **Segregation of duties in a small team** — if one person is both proposer and approver, what's the compensating control?
- **RAG corpus cold-start** — how much confirmed history is enough before AI categorization is trustworthy per counterparty class.

---

*Next: review the autonomy table (§3) and the caps (§4.5, §7), then move approved sections toward an implementation plan. Task status → STATUS.md; business facts discovered here → KNOWLEDGE.md.*
