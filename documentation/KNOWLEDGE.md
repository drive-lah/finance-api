<!-- CANONICAL — single source for BUSINESS FACTS. One numbered fact per line, dated, sourced. -->
<!-- IDs are stable and append-only: never renumber, never delete — strike and supersede. -->
<!-- Task/workstream state does NOT belong here → STATUS.md. Maintenance process → CLAUDE.md Rule 6. -->

# KNOWLEDGE — Drive lah Business Facts

Feeds: (1) the RAG "company facts" input (IDEAL_STATE §3 — the AI never runs blind), (2) future agents, (3) humans.

---

## 1. Entities & structure (ENT)

- **ENT-1** Only 3 real legal entities: Drive lah Pte Ltd (SG), Drive lah Ventures Holding (SG), Drive lah Australia Pty Ltd (AU). *(Gaurav, 2026-06-01)*
- **ENT-2** "Drive lah Fleet" (SG) and "Drive mate fleet" (AU) were MOCK QuickBooks entities created only to segregate RMS money; their GL folds into the parents. *(Gaurav, 2026-06-01)*
- **ENT-3** "Drive mate" / "DRIVE MATE MELBOURNE" is the AU trading name — our own name in bank text, never a third party. *(Gaurav, 2026-07-23)*
- **ENT-4** Ventures Holding is a fundraising/holding vehicle, not an operating company; its parties are the cap table, lenders, and corp-service firms. *(Gaurav, 2026-06-01)*
- **ENT-5** Related entities are NEVER counterparties — intercompany rides on entity ids; entity names in bank text belong to the transfer/IC machinery. *(Gaurav, 2026-07-23)*

## 2. Products & money flows (FLOW)

- **FLOW-1** Two products, two margins: P2P (owner self-manages; platform fee) and RMS (we manage the car; extra management fee → higher margin). Long-term variants: Flex+ and Flex+ RMS. *(P2P_RMS doc, 2026-03)*
- **FLOW-2** On RMS trips WE are the registered host: the guest's payment is revenue recognized ONCE at Stripe; the "host share" flows to OUR OWN Stripe connected account, then settles into our bank. *(Gaurav, 2026-07-10)*
- **FLOW-3** QB booked that settlement inflow as "Due to Fleet" and payouts to real car owners as "Due from Fleet"; the mock Fleet entity existed to do the consolidation elimination ($100 rev − $80 self-payout ⊕ $80 rev − $60 owner cost ⇒ $100 rev − $60 cost). *(Gaurav, 2026-07-10)*
- **FLOW-4** With Fleet folded away, that elimination happens at mapping: settlement inflows = internal transfers (never P&L); payouts to real RMS owners = 5001. Mapping settlement inflows to revenue would double-count. *(Gaurav, 2026-07-10)*
- **FLOW-5** "Due to SG" exists only in the mock Fleet entity's books (its mirror of SG's "Due from Fleet"); Fleet has no bank account, so those lines never appear in a bank replay. *(verified, 2026-07-10)*
- **FLOW-6** Historical RMS revenue cannot be split from P2P at bank level — booking-level RMS data is required for the 4000 vs 4001 split. *(agreed, 2026-07-10)*
- **FLOW-7** "Ventures-Euro" was the business partner's PERSONAL/bureau EUR account: investors paid into it on behalf of Drive lah; he later transferred to the company. Every conduit flow has TWO legs — investor→partner (the real capital/loan event, recognize once) and partner→company (a settlement transfer, NOT a second contribution). *(Gaurav, 2026-06-01)*
- **FLOW-8** The claims/incidentals world (guest damage) runs its own vendor ecosystem — towing, smash repair, assessors, locksmiths — which entered the live DB via invoice/AP workflows, not bank statements. *(verified, 2026-07-23)*
- **FLOW-9** Remote staff are paid via Wise in currency batches (INR/MYR/AUD) whose memo text describes the batch ("Wise INR Human Resource Salary Payment") — those strings are payment descriptors, not parties; prime rule material (~469 lines). *(Gaurav, 2026-07-23)*

## 3. Counterparty facts (CP)

### People
- **CP-1** Gaurav Singhal is founder/director; "GS" in SG bank text is him and is NOT a counterparty (lines handled per line). He holds ONE global party record — the founder exception to entity-scoped people. *(Gaurav, 2026-07-23)*
- **CP-2** Dirk-Jan **ter Horst** is the co-founder/director; the name "Dirk-Jan ten Brink" was wrong — never use it. *(Gaurav, 2026-07-23)*
- **CP-3** Vigneshsuran So Dhevan and Sarala D O Letchumenan are SG employees; bank texts "Sala Vigneshsuran", "Sala Sarala", "LET SARAH", "Sarahh Letchumenan" are their salary transfers. "Sala <name>" prefix = salary transfer to that person. *(Gaurav, 2026-07-23)*
- **CP-4** Philippines-based individuals paid regularly (Maricon REviza/"Maricon Viza", Ralph E. Seronga/"Ralph Efrenson Eronga", Edelyn Reyes, …) are the remote customer-support team → 5063. *(Gaurav, 2026-07-23)*
- **CP-5** Jay Balan and Muhamad Haziq Bin Mustaffa are on-ground team paid per job → 5062. *(Gaurav, 2026-07-23)*
- **CP-6** Correct names: Agha Ali (QB "Agha Salary") · Aaron (QB "ARON J") · Idris Ong (QB "BEXP Idris Ong") · Filbert (QB "Filbert Salary") · Paramjeet Paramjeet is an employee → 6000. *(Gaurav, 2026-07-23)*
- **CP-34** "RR Ventures Pty Ltd" in bank text is Gaurav's salary vehicle — an alias on his party record; historically booked as director salary (6003). *(Gaurav, 2026-07-23)*
- **CP-7** Vernika Singhal made a one-line family loan — deliberately NOT a counterparty; handled at replay. *(Gaurav, 2026-07-23)*

### Technology vendors
- **CP-8** Twilio powers product SMS/OTP → 6700. Vercel and Render host/serve the product → 6700. Apple defaults 6700 with a future rule to split App Store purchases. *(Gaurav, 2026-07-23)*
- **CP-9** ~~Anthropic, ChatGPT, Onfido, Rightworks, Amplitude, Lovable, CircleCI, SGNIC → 6701 subscriptions. OpenAI API spend is distinct from ChatGPT subscriptions.~~ Superseded by CP-40 on the OpenAI clause; the 6701 list stands. *(Gaurav, 2026-07-23)*
- **CP-40** OpenAI and ChatGPT are ONE party ("OpenAI") — ALL OpenAI/ChatGPT spend is ALWAYS 6701 Technology - Software Subscriptions, API or subscription alike. *(Gaurav, 2026-07-23)*
- **CP-10** "Technology" as a QB payee is an artifact — bulk-tagged over 1,248 card purchases; the real vendors (DocuSign, Dropbox Sign, Anthropic, OpenAI…) are in the bank memo text. *(verified, 2026-07-23)*
- **CP-11** Google is TWO parties by product economics: **Google Ads** (→ 6100) and **Google** (Cloud/Storage/Workspace tech → 6700). *(Gaurav, 2026-07-23)*
- **CP-12** KORE Wireless provides SIM/data connectivity inside the in-car devices → 5030 (a device running cost, not office tech). *(Gaurav, 2026-07-23)*
- **CP-13** Roobykon is the Sharetribe development agency; its platform work is CAPITALIZED → 1710. Eventila Technologies likewise. *(Gaurav, 2026-07-23)*

### Vehicle & ops vendors
- **CP-14** Digital Matter, Humax, SentriLock make the in-car GPS/telematics/lockbox devices; their RECURRING billing → 5030 (see POL-4 for the purchase-vs-subscription split). The Fleet Dr Pty Ltd does device installations → 5064. *(Gaurav, 2026-07-23; refined 2026-07-24)*
- **CP-15** Elite Car Ventures (= "Elite car") is a workshop → 5032. Cheng Chuan is the car-repair vendor Cheng Chuan Motor Services → 5032. *(Gaurav, 2026-07-23)*
- **CP-16** ~~Detailing vendors (Car mobile detailing, Arena Detailing) are workshop-class → 5032~~ Car mobile detailing → 5032 Workshop; **Arena Detailing → 6103 Marketing - Asset Creation** (corrected — they did marketing/photography work); pure washes (Wash and Go) → 5022. *(Gaurav, 2026-07-23)*
- **CP-17** Towlah ("Tow lah", "TOWLAH SG RECOVERY") is the SG towing vendor → 5033. RACQ is roadside assistance → 5033. AU has ~20 distinct towing companies from the claims workflow — all separate parties. *(2026-07-23)*
- **CP-18** RMS hosts (car owners under our management) we pay: Abwin Leasing, CDC Australia, Norman Chan, Bombora → 5001. CDC Australia ≠ CDG/ComfortDelGro. RMS leasing-style hosts are P2P RMS (5001), not Flex+ (5003). *(Gaurav, 2026-07-23)*
- **CP-19** U R Drive and URA charges are parking → 5060; URA is a government body. Transport for NSW lines are staff public-transport travel → 6400 (NOT vehicle tolls). *(Gaurav, 2026-07-23)*

### Professional, marketing, insurance
- **CP-20** Fiverr gigs are marketing creative/production → 6103. Upwork payments are PH support contractors → 5063 (via rule). Anubhav Designs → 6103. *(Gaurav, 2026-07-23)*
- **CP-21** Venture Haven is corp-services → 6500. Sleek and Apex are corp-service firms but deliberately NOT counterparties. *(Gaurav, 2026-07-23)*
- **CP-22** Tokio Marine premiums are per-trip insurance → 5035. NTUC (Income) is the SG co-op insurer, entity-scoped. *(Gaurav, 2026-07-23)*

### Government & statutory
- **CP-23** ATO, IRAS, Service NSW, WorkCover QLD: NO default account — payments mix tax types; every line ruled individually. *(Gaurav, 2026-07-23)*
- **CP-24** ~~CPF payments settle the payroll-accrued liability → 2300 CPF Payable.~~ Superseded: **CPF party defaults to 6001 Employer CPF (SG) — expense, not liability** (Gaurav: "counterparty default to expense not liability"); the payroll knock-off still claims CPF lines against runs when they exist. *(Gaurav, 2026-07-24)*
- **CP-25** ACRA and ASIC charges are statutory filing fees → 6500. Fines Victoria → 6502. *(Gaurav, 2026-07-23)*

### Investors
- **CP-26** Investors and lenders are ONE type (investor): the same party may hold equity AND convertible loans — the instrument is a property of the transaction (e.g. Stevu Beheer BV holds both). *(Gaurav, 2026-07-23)*
- **CP-27** Ventures' counterparties are 100% the cap table + lenders: share-capital investors → 3000; EUR-conduit contributors (Derkjan Lutgert, Kalle Blom, Marten Hatzmann, Peter Stam, Nick Leijten) default 3000 with two-leg conduit flows (FLOW-7). ComfortDelGro's Ventures line is a loan → 2400. Sebastiaan W Koeling and "Anusha Krishnakumar & Sivaramakrishnan" (joint account) are investors → 3000. *(Gaurav, 2026-06-01 + 2026-07-23)*

### Never counterparties
- **CP-28** Banks and payment rails (Wise, Commonwealth Bank, "Card transaction", "Transfer", Stripe settlement text) are NEVER counterparties. *(Gaurav, 2026-07-23)*
- **CP-29** One-off merchants (restaurants, hotels, petrol, retail) never become counterparties, even when identifiable. *(Gaurav, 2026-07-23)*
- **CP-30** Debt collectors (ABC Debt, Ecollect.com) — removed by choice; lines handled individually. *(Gaurav, 2026-07-23)*
- **CP-32** QB-only payees pre-created (rules existed, no transaction evidence yet): SEON ("SEON1", "EONS TECHNOLOGIES" = fraud-detection SaaS → 6701); Eurokars, Komoco, City Auto, Cheng Auto Body works (SG car dealers/workshops → 5032, some known only by account numbers); LoK ("LOK NGEE", no default). *(Gaurav, 2026-07-23)*
- **CP-33** CIRCLE BUILDS = CircleCI (one global party, 6701; "CIRCLECI.COM" alias). "Biz Giro" is GIRO descriptor text, not a party; "BIZ RR GIRO" is a CPF alias. QB's Dropbox→docsend and docsend→Hotjar rule wirings were errors — discarded. *(Gaurav, 2026-07-23)*
- **CP-35** "Caretakers" are individuals paid to look after RMS cars (historically 5001): Quang Vu Nguyen, Shannon Yeo, Tan Lay Suan Judy, Sabrina Lin Yan Xiu — deliberately NOT pre-created as parties (replay learns them). *(Gaurav, 2026-07-23)*
- **CP-36** Car-listing photographers → 6103 Marketing - Asset Creation: Christian Alvarez, Benjamin Lai, Chuan Quan Koh, Sapna Prakash Dabade, plus an unidentified one known only by bank acct 565251121001. *(Gaurav, 2026-07-23)*
- **CP-37** Ho Sze Yie is called "Jared" — PH support team → 5063. "Ana Ash" may be bank truncation of Anas Ashfaq — verify at replay. *(Gaurav, 2026-07-23)*
- **CP-38** Care Corporation provides the PH team's HEALTH INSURANCE → new account **6004 Staff Health Insurance** (added to COA v2 2026-07-23; distinct from product insurance 5031/5035 and claims 6010-14). *(Gaurav, 2026-07-23)*
- **CP-39** Paddle (merchant-of-record billing platform) deliberately NOT a party — replay learns its lines. Cycle & Carriage is an RMS leasing host → 5001 (live 5003 corrected). *(Gaurav, 2026-07-23)*
- **CP-41** Google sub-products billed as subscriptions keep EXCEPTION rules → 6701 (GSuite/Workspace, Google Play, Google Storage); bare Google/Cloud stays 6700; apple.com store text → 6701 against Apple's 6700 default. *(Gaurav, 2026-07-24)*
- **CP-42** Accounting SOFTWARE (Xero, QuickBooks/Intuit) → 6500 Accounting & Bookkeeping Fees, not 6701. *(Gaurav, 2026-07-24)*
- **CP-43** Founders' party defaults = 6003 Directors Salary (Gaurav Singhal, Dirk-Jan ter Horst). Jay Balan = salaried on-ground staff → 5061 (acct 6218104856). Vighnesh R Vighnesh + Mythri S = employees → 6000; Noraisa Domado + Emmanuel Jacobo = PH support → 5063; "Nipa Riten Mody" = alias of Ankish Mody; Benjamin Gotto Smith = AU employee → 6000 (not the lawyer the mined rule guessed). *(Gaurav, 2026-07-24)*
- **CP-44** Device-connectivity vendors → 5030 (Liberty Wireless joins KORE). Domain registrars → 6701 (GoDaddy joins SGNIC). Dev-tool SaaS → 6701 (Fingerprint, Papertrail, Uptime, Glocksoft/2Checkout, Superblog, Stonly). ComfortDelGro (CDG + Rent-A-Car merged) + Eurokars = RMS leasing hosts → 5001; Sabrina Lin Yan Xiu → 5001; YS Auto works → 5032 (acct 601344831001). *(Gaurav, 2026-07-24)*
- **CP-31** Terminology: they are "counterparties" (parties we do business with — suppliers, investors, employees, related entities), not "vendors". *(Gaurav, 2026-06-01)*

## 4. Accounting policies & principles (POL)

- **POL-1** Revenue is recognized ONCE at the economic source (Stripe/guest payment); settlement legs between our own accounts are transfers, never P&L. *(Gaurav, 2026-07-10)*
- **POL-2** Anything that moves money (invoice/payroll knock-off matching) is deterministic, never AI. Knock-offs are order-independent (retroactive passes prevent double-count). *(design, 2026-05)*
- **POL-3** The 6700/6701 line: services that HOST or RUN the product → 6700 Infrastructure; tools/APIs bought as subscriptions → 6701. *(Gaurav, 2026-07-23)*
- **POL-4** In-car devices are EXPENSED on purchase → 5030 (not capitalized to 1510); connectivity → 5030; installation → 5064. *(Gaurav, 2026-07-23)*
- **POL-5** Development-agency platform work is CAPITALIZED → 1710. In-house R&D-software policy (capitalize vs expense) still open — decide at replay. *(Gaurav, 2026-07-23)*
- **POL-6** Refund-type income is NOT grants: refunds/rebates → 7001; grants → 7000. *(2026-07-23)*
- **POL-7** Security deposits we HOLD from guests/hosts are a liability → 2110 (not the 1310 asset). *(Gaurav, 2026-07-23)*
- **POL-8** Counterparties = entities/people we have a RELATIONSHIP with; one-off transactions never create parties. *(Gaurav, 2026-07-23)*
- **POL-9** Exactly 4 counterparty types: vendor · employee · government · investor. *(Gaurav, 2026-07-23)*
- **POL-10** Scope follows the ERP master-data principle: identity is global (multinationals → one record, `entity_id NULL`); people, local businesses, statutory bodies are entity-scoped; founders/directors get one global record. *(Gaurav, 2026-07-23)*
- **POL-11** One string points at ONE party — canonical names and aliases must never collide across parties (mechanical gate). *(2026-07-23)*
- **POL-12** IDENTITY belongs to enrichment (names + aliases on the party); rules decide ACCOUNTING only — a rule may condition on a counterparty but never assign one. *(Gaurav, 2026-07-23)*
- **POL-13** Rules fire BEFORE counterparty defaults; a rule that merely names a known party is harmful redundancy — such patterns are dropped, the party default governs. *(2026-07-23)*
- **POL-14** A party's default account is a fallback, not a law — multi-role parties exist; rules and knock-offs override per line. *(Gaurav, 2026-06-01)*
- **POL-16** Rules are NEVER deleted — only deactivated (`status=INACTIVE`); the engine evaluates ACTIVE rules only. History stays auditable. *(Gaurav, 2026-07-23)*
- **POL-17** The rules model/engine must not support counterparty as an ACTION (assignment) — counterparty is a CONDITION only; code change queued: engine ignores + validation rejects assignment fields, columns dropped in a later migration. *(Gaurav, 2026-07-23)*
- **POL-18** Prefer a LEAN rule book: for small-count text patterns (travel cards, one-off refunds, A/R collection texts, ex-people), do NOT load convenience rules — replay review + the feedback loop handle them. Rules are for high-value deterministic streams only. *(Gaurav, 2026-07-24)*
- **POL-19** Historical payroll is NEVER reconstructed as runs — historical salary/statutory lines book as direct expense via party defaults; real payroll runs begin H2-2026. *(Gaurav, 2026-07-24)*
- **POL-20** The AP/accrual leg exists only where invoice data exists (≈Jul-2025 onward); earlier periods book expense-on-payment — the accrual+settlement pair nets to the same P&L. *(Gaurav, 2026-07-24)*
- **POL-21** QuickBooks' trial balance is a CROSS-CHECK REFERENCE, not the source of truth — QB carries known errors (mis-payees, generic buckets, wrong accounts) and our arbitrated treatments deliberately diverge (Fleet elimination, RMS, Arena, device expensing…). Diffs are adjudicated case-by-case; the finalised finance-api statements become the new truth once Gaurav signs off. *(Gaurav, 2026-07-24)*
- **POL-15** A counterparty seed loads by UPSERT (normalized name + aliases); never blind-insert against a live directory. *(2026-07-23)*

- **FLOW-10** Gaurav reports a **~2.7M device PURCHASE in September 2024** that must be capitalized (1510) and depreciated. NOT yet located in the mined bank lines — the only ≈2.7M line found is Ventures' Oct-2023 SGD 2,750,210 inflow (USD 2,000,000 from ComfortDelGro — the CDG loan, unrelated). Locate at replay (may be AP/journal-side, a different month, or inside the missing-statement window) and capitalize. *(Gaurav, 2026-07-24)*

## 5. Data quirks & traps (DQ)

- **DQ-1** "(deleted)" on QB payee names is soft-delete noise — strip it; the name remains valid for matching. *(2026-07-23)*
- **DQ-2** QB's payee field is unreliable (see CP-10); the memo field ≈ raw bank text but QB sometimes rewrote memos — GL-mined patterns must be cross-checked against raw bank statements before Stage-2 relies on them. *(2026-06-01)*
- **DQ-3** SG GL was exported in 4 overlapping windows — dedup on load. *(2026-06-01)*
- **DQ-4** Old-COA generic buckets ("Technology System Cost", "General Expense", "Uncategorised Income") map to no single code — SPLIT: resolve per line. *(Gaurav, 2026-07-10)*
- **DQ-5** The 244 live engine rules were bulk-migrated from QuickBooks' own bank rules on 2026-03-14 (240 in one day; qb_rules export dated 2026-03-13). *(verified, 2026-07-23)*
- **DQ-6** 90 of the 244 live rules SET a counterparty (identity leak — violates POL-12), incl. junk like `contains "Sydney"` → Uber.com and `"Tax Payments Netbank Bpay"` → ATO+9000 (violates CP-23). Migration to aliases planned. *(verified, 2026-07-23)*
- **DQ-7** Only 4 of 244 live rules are scoped to bank accounts — entity lives in the rule NAME only; scoping pass needed at apply. *(verified, 2026-07-23)*
- **DQ-8** Live counterparties carry artifact vendors ("GLOBAL TRANSPORT", "Refund Purchase", "Sydney Transport Provider") and a duplicate pair (AUTOROLA/Autorola); 22 records marked for deactivation at apply. *(2026-07-23)*
- **DQ-9** Live vendor records are all GLOBAL regardless of nature — over-broad for local vendors vs POL-10. *(verified, 2026-07-23)*
- **DQ-10** HR-synced employees (external_system=user_registry) use legal names that differ from bank text ("Maricon Viza" vs "Maricon REviza", "Jabez Jun Jie Lim" vs "Lim Jun Jie Jabez") — merge by curated identity, never insert duplicates. *(2026-07-23)*
- **DQ-11** Live COA has 155 accounts vs the CSV's 135 — minor unreconciled drift. *(2026-05-21)*
- **DQ-12** Bank-statement coverage holes: recent ~12–18 months missing (all SG accounts); OCBC-3001 missing 2022; CBA missing pre-Sep-2022. QB GL has no holes. DBS files carry SGD+USD together. *(2026-06-01)*
- **DQ-13** Fuzzy name-matching trap: token-subset scoring gives false 100s against short/generic names ("1ST CLASS TOWING" ~ "Towing") — curate fuzzy merges by hand. *(2026-07-23)*
