# Claude Code Instructions for Finance API

Project-specific guidelines for working with this repository.

---

## Documentation Organization

### Rule 1: WIP Documentation Goes in `documentation/wip`

All work-in-progress documentation MUST be created in `documentation/wip/`, not in the documentation root.

**Examples of WIP files:**
- `documentation/wip/PHASE_4_RULE_CONSTRAINTS.md`
- `documentation/wip/STRIPE_SYNC_ARCHITECTURE.md`
- `documentation/wip/STRIPE_SYNC_BLOCKERS.md`
- `documentation/wip/VIEW_TABLE_TO_JE_MAPPING.md`

❌ **WRONG:**
```
documentation/NEW_FEATURE.md  # Don't create here
```

✅ **CORRECT:**
```
documentation/wip/NEW_FEATURE.md  # Create here instead
```

---

### Rule 2: Only Update Main Documentation After Significant Development

The `documentation/` root holds exactly **two** docs:

- **`IDEAL_STATE.md`** — the target/vision **only** (mental model + ideal pipeline + subsystem ideal specs). No current-state or gap. Slow-changing; update only when the vision shifts, OR when explicitly requested.
- **`STATUS.md`** — the living progress tracker: current state, the gap, done / left / next steps. Updated continuously as work progresses (see Rule 4).

Do not add other docs to the root. Deep architecture reference is archived in `wip/SYSTEM_OVERVIEW.md`; the code is the source of truth for how things actually work. All other docs (analysis, design, roadmaps, API reference) go in `documentation/wip/`.

**WIP documentation files:**
- `documentation/wip/STRIPE_SYNC_BLOCKERS.md`
- `documentation/wip/PHASE_2_5_CROSS_ENTITY_IMPLEMENTATION.md`
- `documentation/wip/CATEGORIZATION_ROADMAP.md`

---

### Rule 3: Random Documentation Gets Created in `documentation/wip`

If creating exploration docs, analysis docs, or one-off reference docs, default location is `documentation/wip/`.

**Examples:**
- Architecture investigations → `documentation/wip/`
- Comparative analysis → `documentation/wip/`
- Design options/alternatives → `documentation/wip/`
- Session notes/continuations → `documentation/wip/`

---

## How to Apply These Rules

### When Creating New Documentation

**Step 1:** Determine if it's WIP or Main
- WIP: Experimental, exploratory, incomplete → `documentation/wip/`
- Main: Finalized, ready for reference → `documentation/`

**Step 2:** If unsure, default to `documentation/wip/`

**Step 3:** When work completes, optionally consolidate WIP into Main (with user approval)

### When Updating Existing Documentation

- **Main docs:** Only update if significant development complete OR user explicitly asks
- **WIP docs:** Update freely as understanding evolves

---

## Examples

### ✅ CORRECT: WIP Architecture Analysis

```
User: "Map all ClickHouse views to JEs, show differences between SG and AU"
→ Create: documentation/wip/VIEW_TABLE_TO_JE_MAPPING.md
→ Reason: Analysis/exploration doc, goes in wip
```

### ❌ INCORRECT: Random File in Root

```
User: "Document the views..."
→ Create: documentation/VIEWS.md  # Wrong location
→ Should be: documentation/wip/VIEWS.md
```

### ✅ CORRECT: Update Main After Development

```
User: "We've completed Phase 2 Stripe sync, update main docs"
→ Update: documentation/STRIPE_SYNC_ARCHITECTURE.md
→ Reason: Significant development + explicit request
```

### ❌ INCORRECT: Update Main During Exploration

```
During exploration: Update documentation/SYSTEM_OVERVIEW.md
→ Wrong: This is mid-development analysis
→ Should be: Create documentation/wip/EXPLORATION.md instead
```

---

## Summary

| Scenario | Action | Location |
|----------|--------|----------|
| Exploring architecture | Create doc | `documentation/wip/` |
| Analyzing differences | Create doc | `documentation/wip/` |
| Session notes/continuation | Create doc | `documentation/wip/` |
| Design investigation | Create doc | `documentation/wip/` |
| Feature complete + tested | Update main | `documentation/` |
| User explicitly requests main update | Update main | `documentation/` |
| Unsure where to put something | Default | `documentation/wip/` |

---

## Rule 4: Task Status Goes ONLY in STATUS.md

**Single Source of Truth:** All task status (completion %, blockers, priority, effort) lives **ONLY** in `documentation/STATUS.md`. (Supersedes the old `documentation/wip/FINANCE_API_COMPLETION_ROADMAP.md`, now a redirect stub.)

❌ **NEVER** put task status in:
- SYSTEM_OVERVIEW.md
- API.md
- Other documentation files
- Code comments

✅ **ALWAYS** put task status in:
- `documentation/STATUS.md` (only place)

**Why:** Prevents status drift where the same task is documented in multiple places with different completion percentages.

### Example

❌ **WRONG:**
```markdown
# SYSTEM_OVERVIEW.md
| Phase 5 | 🔄 IN_PROGRESS | 30% complete |
```

✅ **RIGHT:**
```markdown
# documentation/STATUS.md
| A5 | E2E test: monthly sync | 🔄 IN_PROGRESS | 30% |
```

Then in SYSTEM_OVERVIEW.md, reference it:
```markdown
**Status & Timeline:** See `documentation/STATUS.md` for current progress.
```

---

## Rule 5: Keep STATUS.md Current (Living Doc)

`documentation/STATUS.md` is a **living tracker** — it must reflect reality at the end of every working session, not just when convenient. Stale status is worse than no status.

**The rule:** Any session that changes the system — code committed, a decision made, a blocker/bug found, a module finished, files cleaned up — MUST update `STATUS.md` in the **same session, before wrapping up.**

**What to keep current:**
- The **Overall** summary + **Verified ground truth** line (test/mypy counts, branch state) whenever they change — and keep the numbers **verified** (re-run pytest/mypy), not remembered.
- **§2 What's Pending** — move finished items out; add newly discovered work, blockers, and bugs.
- **§3 Decisions** — record any decision the moment it's made.

**If you did work and didn't touch `STATUS.md`, the task isn't done.**

---

## Rule 6: `documentation/KNOWLEDGE.md` — the CANONICAL business-facts file. Update it the moment you learn anything.

Gaurav's explanations of the business are **gold**. The moment he (or verified data) reveals a business
fact — who a vendor is, what a bank-text pattern means, how a money flow works, an accounting ruling, a
data trap — **append it to `documentation/KNOWLEDGE.md` in the same session.** The smallest of lines counts.

Canonical format (non-negotiable):
- ONE fact per bullet, numbered with a stable section-prefixed ID: `ENT-n` (entities), `FLOW-n` (money
  flows), `CP-n` (counterparties), `POL-n` (policies/principles), `DQ-n` (data quirks).
- IDs are append-only: never renumber, never delete — strike (`~~…~~`) and add the superseding fact.
- Every fact carries `(source, date)` — e.g. `(Gaurav, 2026-07-23)`.
- Facts only. No task state, no progress, no "next steps" — that is STATUS.md's job.

KNOWLEDGE.md is the **"company facts" input for the RAG/AI pipeline** (IDEAL_STATE §3) and the context base
for future agents. **The test: if Gaurav says something a future agent would need and it isn't in
KNOWLEDGE.md by end of session, the session isn't done.**

*(Documentation root is therefore exactly three files: `STATUS.md`, `IDEAL_STATE.md`, `KNOWLEDGE.md` —
amends Rule 2.)*

---

## Rule 7: STATUS.md must stay CANONICAL — numbered, lean, no paragraph dumps

STATUS.md is the living tracker and it must remain **scannable**:

- Every state item is **numbered** (tables with stable row IDs like `S-n`, or numbered lists) — never
  free-flowing paragraph bullets that grow by appending sentences.
- Each item is **state, not story**: what it is + where it stands + what unblocks it. One to three lines.
- Business substance discovered along the way goes to `KNOWLEDGE.md` (Rule 6); STATUS references IDs
  (e.g. "per POL-13", "see DQ-6") instead of restating facts.
- When an item completes, compress it: outcome + date + artifact path. Prune superseded detail — git
  history keeps the archaeology.
- Same-session update discipline as Rule 5; verified numbers only.

**If a STATUS section can't be skimmed in ten seconds, it's in violation — restructure it.**

---

**These rules apply to all future work in this repository.**
