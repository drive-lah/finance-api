# Finance Tool: SG Host Payout Tab (Technical Documentation)

## Table of contents

1. [Overview](#1-overview)
2. [Location in Retool app](#2-location-in-retool-app)
3. [Type selection (Select type container)](#3-type-selection-select-type-container)
4. [Form routing logic](#4-form-routing-logic)
5. [Duplicate check modals](#5-duplicate-check-modals)
6. [Backend queries](#6-backend-queries-sg-host-payout-tab)
7. [REST APIs (SG)](#7-rest-apis-sg)
8. [Logging (Retool Database)](#8-logging-retool-database)
9. [Known implementation notes / potential follow-ups](#9-known-implementation-notes--potential-follow-ups)

---

## 1. Overview

The SG Host Payout workflow is used by Finance users to create payout entries or charge entries for SG hosts. The flow includes:

1. A type/category selection section (Payout vs Charge)
2. One of three forms:
   - **Trip-based entry** (requires Trip ID): validates transaction details and performs a duplicate check against existing entries for that trip before calling the SG payout API.
   - **Host/listing-based entry** (no Trip ID): used for Flex+ and "not linked to trip" exceptions. Performs a duplicate check for the host/listing/type before calling the SG host payout API.
   - **RMS entry flow** (host-only, no listing/trip): special payout type `rms`. Performs duplicate check using Host ID + payoutType before calling the same SG host payout API.
3. A duplicate-check modal shown before creating the payout/charge.

---

## 2. Location in Retool app

### 2.1 UI view (tab)

- `src/tabbedContainer1.rsx` → `<View viewKey="SG Host Payout">`

### 2.2 Type selection container

- `src/container10.rsx`

### 2.3 Duplicate check modals

- Trip-based modal: `src/sgDuplicateCheckWithTId.rsx` (`sgDuplicateCheckWithTId`)
- Host/listing-based modal: `src/sgDuplicateCheckWithHostId.rsx` (`sgDuplicateCheckWithHostId`)
- RMS (host-based) modal: `src/sgDuplicateCheckWithHostIdForRMS.rsx` (`sgDuplicateCheckWithHostIdForRMS`)

### 2.4 Backend queries

- Located in `functions.rsx` under `<Folder id="sgHostPayoutsTab">`

---

## 3. Type selection (Select type container)

### 3.1 Component: `select2` — Payout vs Charge

**Label:** Charge or Pay the Host?

**Options**

- Payout
- Charge

**Behavior**

On change, clears category selections (as configured):

- clears `payout2` (this looks like a mis-reference; SG uses `payout3`)
- clears `charge3`

> **Implementation note:** In `src/container10.rsx`, `select2` clears `pluginId="payout2"` instead of `payout3`. That appears to be a copy/paste bug. Functionally, SG payout selection may not clear correctly unless `payout2` exists and is used (AU uses `payout2`, SG uses `payout3`).

### 3.2 Component: `payout3` — Payout category

**Visible only when:** `select2.value === 'Payout'`

**Options**

| Label | Value |
| --- | --- |
| Tolls | `tolls` |
| Fuel Shortage | `fuel_refund` |
| Late Return | `late_return` |
| Excess Mileage | `excess_mileage` |
| Damage | `damage` |
| Cleanliness | `cleanliness` |
| Flex+ | `flexplus` |
| Misc Payout | `misc_payout` |
| Duration | `duration` |

> Compared to AU: SG payout categories omit `distance` and have `duration`.

### 3.3 Component: `charge3` — Charge category

**Visible only when:** `select2.value === 'Charge'`

**Options**

| Label | Value |
| --- | --- |
| Misc Charge | `misc_charge` |
| Fuel Charge | `fuel_charge` |

### 3.4 Component: `switch2` — "Is not linked to a trip?"

Shown only for exception cases:

- `payout3.value === 'misc_payout'` **OR** `charge3.value === 'misc_charge'` (and the other category is not selected)

**Label:** Is not linked to a trip?

**Caption:** Use this as exception only. Finance team responsibility.

---

## 4. Form routing logic

There are 3 SG forms; visibility is driven by `select2`, category selections, and special payout types.

### 4.1 Form 5 — Trip-based entry (TID required)

**Component:** `form5`
**Title:** Add transaction & amount

This is the default flow when:

- Type selected
- Category selected
- NOT Flex+
- NOT RMS
- NOT exception toggle flow (`switch2` for `misc_*`)

**Inputs**

| Component | Field | Notes |
| --- | --- | --- |
| `textInput6` | Trip ID | Required |
| `numberInput3` | Amount | Must be > 0; custom validation disallows 0 |
| `textArea7` | Description | Optional |

**On submit triggers**

1. SQL: `sgretrievetxndetails`
2. SQL: `sgretrievepayouts`
3. Show modal: `sgDuplicateCheckWithTId`

### 4.2 Form 6 — Host/listing-based entry (no Trip ID)

**Component:** `form6`
**Title:** Add host and amount details

This is the exception flow when:

- Category is `flexplus`, **OR**
- `switch2.value === true` **AND** category is `misc_payout` or `misc_charge`

**Inputs**

| Component | Field | Notes |
| --- | --- | --- |
| `textInput7` | Host ID | Required |
| `textInput8` | Listing ID | Required |
| `numberInput4` | Amount | Must be > 0; custom validation disallows 0 |
| `textArea8` | Description | Optional |

**On submit triggers**

1. SQL: `sgretrieveListingDetails`
2. SQL: `sgretrievePayoutsForHost`
3. Show modal: `sgDuplicateCheckWithHostId`

### 4.3 Form 7 — RMS flow (host-only)

**Component:** `form7`
**Title:** Add host and amount details (RMS path)

This flow is active when:

- Type selected and category selected
- Payout category is exactly `rms`

**Inputs**

| Component | Field | Notes |
| --- | --- | --- |
| `textInput9` | Host ID | Required |
| `numberInput5` | Amount | Must be > 0; custom validation disallows 0 |
| `textArea9` | Description | Optional |

**On submit triggers**

1. SQL: `sgretrieveHostDetailsForRms`
2. SQL: `sgretrievePayoutsForHostForRms`
3. Show modal: `sgDuplicateCheckWithHostIdForRMS`

---

## 5. Duplicate check modals

### 5.1 Modal: `sgDuplicateCheckWithTId` (trip-based)

**File:** `src/sgDuplicateCheckWithTId.rsx`

**Displays**

- Trip details (from `sgretrievetxndetails`)
- Existing entries for that trip (table `table7` fed by `sgretrievepayouts.data`)

**Proceed button**

- Triggers REST: `sgcreatePayoutWithTId` (endpoint with Trip ID)

**Cancel button**

Hides modal and clears/resets:

- clears `form5`
- resets `sgretrievepayouts`, `sgretrievetxndetails`
- clears `select2`, `payout3`, `charge3`

### 5.2 Modal: `sgDuplicateCheckWithHostId` (host/listing-based)

**File:** `src/sgDuplicateCheckWithHostId.rsx`

**Displays**

- Host/listing details (from `sgretrieveListingDetails`)
- Existing entries for listing + payoutType (table `table6` fed by `sgretrievePayoutsForHost.data`)

**Proceed button**

- Triggers REST: `sgcreatePayoutWithHostId` (endpoint without Trip ID)

**Cancel button**

Hides modal and clears/resets:

- clears `form6`
- resets `sgretrievePayoutsForHost`, `sgretrieveListingDetails`
- clears `select2`, `payout3`, `charge3`

### 5.3 Modal: `sgDuplicateCheckWithHostIdForRMS` (RMS host-only)

**File:** `src/sgDuplicateCheckWithHostIdForRMS.rsx`

**Displays**

- Host details (from `sgretrieveHostDetailsForRms`)
- Existing entries for host + payoutType (table `table8` fed by `sgretrievePayoutsForHostForRms.data`)

**Proceed button**

- Triggers REST: `sgcreatePayoutWithHostIdForRms` (same "without Trip ID" style API)

**Cancel button**

Hides modal and clears/resets:

- clears `form7`
- resets `sgretrievePayoutsForHostForRms`, `sgretrieveHostDetailsForRms`
- clears `select2`, `payout3`, `charge3`

---

## 6. Backend queries (SG Host Payout tab)

### 6.1 SQL: `sgretrievetxndetails`

**Purpose:** Look up host/guest/listing information for a Trip ID.

- **File:** `lib/sgretrievetxndetails.sql`
- **Resource:** Drive lah - Production (MySQL)

**Input dependencies**

- `textInput6.value` (Trip ID)

**SQL**

```sql
SELECT 
    h.id AS host_id, 
    h.firstname AS host_firstname, 
    h.lastname AS host_lastname, 
    g.id AS guest_id, 
    g.firstname AS guest_firstname, 
    g.lastname AS guest_lastname,
    t.listingid AS listing_id,
    t.id
FROM 
    sync_db_prod.transactions t 
JOIN 
    sync_db_prod.users h 
    ON h.id = t.providerid 
JOIN 
    sync_db_prod.users g 
    ON g.id = t.customerid 
WHERE 
    t.id = {{ textInput6.value }};
```

**Output fields used**

- `guest_id`, `host_id`, `listing_id`, `id`
- plus host/guest names in modal

### 6.2 SQL: `sgretrievepayouts`

**Purpose:** Fetch existing payout entries for the Trip ID (duplicate check).

- **File:** `lib/sgretrievepayouts.sql`

**Input dependencies**

- `textInput6.value` (Trip ID)

**SQL**

```sql
SELECT 
  id, 
  createdat, 
  hostid, 
  tripid, 
  transactionId,
  payoutAmount/100, 
  payoutType, 
  description
FROM sync_db_prod.payout_entries
WHERE tripid = {{ textInput6.value }}
```

Used by `sgDuplicateCheckWithTId.table7` as `sgretrievepayouts.data`.

### 6.3 SQL: `sgretrieveListingDetails`

**Purpose:** Resolve host details and registration number from Listing ID.

- **File:** `lib/sgretrieveListingDetails.sql`

**Input dependencies**

- `textInput8.value` (Listing ID)

**SQL**

```sql
SELECT
  h.id as hostId,
  l.id as ListingId,
  CONCAT(h.firstName, " ", h.lastName) as hostName,
  JSON_UNQUOTE(JSON_EXTRACT(l.publicData, '$.license_plate_number')) as license_plate_number
FROM sync_db_prod.listings l
LEFT JOIN sync_db_prod.users h ON l.userId = h.id
WHERE l.id = {{ textInput8.value }};
```

Used by `sgDuplicateCheckWithHostId` and by `sgcreatePayoutWithHostId` payload construction.

### 6.4 SQL: `sgretrievePayoutsForHost`

**Purpose:** Fetch existing payout entries for listing + payoutType for duplicate checking.

- **File:** `lib/sgretrievePayoutsForHost.sql`

**Input dependencies**

- `payout3.value` (payoutType)
- `textInput8.value` (listingId)

**SQL**

```sql
SELECT 
  id, 
  createdAt, 
  hostId, 
  tripId, 
  transactionId,
  payoutAmount/100, 
  payoutType, 
  description
FROM sync_db_prod.payout_entries
WHERE payoutType = {{ payout3.value }}
  AND listingId = {{ textInput8.value }}
ORDER BY createdAt desc;
```

Used by `sgDuplicateCheckWithHostId.table6`.

> **Note:** This filters by `payout3.value` (payout type) and does not reference `charge3.value`. If Form 6 is used for Charge scenarios, confirm whether duplicate checking should also run against `charge3.value` depending on `select2.value`.

### 6.5 SQL: `sgretrieveHostDetailsForRms`

**Purpose:** Resolve host details by Host ID for RMS workflow.

- **File:** `lib/sgretrieveHostDetailsForRms.sql`

**Input dependencies**

- `textInput9.value` (Host ID)

**SQL**

```sql
SELECT
  id as hostId,
  email,
  CONCAT(firstName, " ", lastName) as hostName
FROM sync_db_prod.users
WHERE id = {{ textInput9.value }};
```

Used by RMS duplicate check modal and RMS API payload `hostId`.

### 6.6 SQL: `sgretrievePayoutsForHostForRms`

**Purpose:** Fetch existing payout entries for Host ID + payoutType (RMS duplicate check).

- **File:** `lib/sgretrievePayoutsForHostForRms.sql`

**Input dependencies**

- `payout3.value` (payoutType)
- `textInput9.value` (hostId)

**SQL**

```sql
SELECT 
  id, 
  createdAt, 
  hostId, 
  tripId, 
  transactionId,
  payoutAmount/100, 
  payoutType, 
  description
FROM sync_db_prod.payout_entries
WHERE payoutType = {{ payout3.value }}
  AND hostId = {{ textInput9.value }}
ORDER BY createdAt desc;
```

Used by `sgDuplicateCheckWithHostIdForRMS.table8`.

---

## 7. REST APIs (SG)

Both APIs:

- **Header:** `Content-Type: application/json`
- **Authentication:** none

> Amounts are passed in cents (SGD × 100). Charges are passed as negative amounts.

### 7.1 API #1 — With Trip ID (trip-based flow)

- **Query ID (Retool):** `sgcreatePayoutWithTId`
- **Endpoint:** `POST https://payout-service.drivelah.sg/api/add-custom-payout-entry`
- **Headers:** `Content-Type: application/json`

**Triggered when**

- User confirms "Ok to proceed. Its not a duplicate" in the `sgDuplicateCheckWithTId` modal

**Request payload (as implemented in Retool)**

```json
{
  "payload": {
    "guestId": "<from sgretrievetxndetails.data.guest_id[0]>",
    "hostId": "<from sgretrievetxndetails.data.host_id[0]>",
    "listingId": "<from sgretrievetxndetails.data.listing_id[0]>",
    "tripId": "<from sgretrievetxndetails.data.id[0]>",
    "payoutAmount": "<numberInput3.value * 100; negative if Charge>",
    "payoutCurrency": "SGD",
    "payoutType": "<payout3.value if Payout; charge3.value if Charge>",
    "description": "<textArea7.value>",
    "payoutSource": "admin_api"
  }
}
```

**Exact payoutAmount rule**

- If `select2.value === 'Charge'`: `payoutAmount = -1 * numberInput3.value * 100`
- Else: `payoutAmount = numberInput3.value * 100`

**Post-success behavior**

1. Shows success notification
2. Triggers SQL logging insert `sglogForPayoutEntry`
3. Clears form and resets supporting queries, hides modal

### 7.2 API #2 — Without Trip ID (host/listing-based flow)

- **Query ID (Retool):** `sgcreatePayoutWithHostId`
- **Endpoint:** `POST https://payout-service.drivelah.sg/api/add-host-payout-entry`
- **Headers:** `Content-Type: application/json`

**Triggered when**

- User confirms "Ok to proceed. Its not a duplicate" in the `sgDuplicateCheckWithHostId` modal

**Request payload (as implemented in Retool)**

```json
{
  "payload": {
    "hostId": "<from sgretrieveListingDetails.data.hostId[0]>",
    "listingId": "<from sgretrieveListingDetails.data.ListingId[0]>",
    "payoutAmount": "<numberInput4.value * 100; negative if Charge>",
    "payoutCurrency": "SGD",
    "payoutType": "<payout3.value if Payout; charge3.value if Charge>",
    "description": "<textArea8.value>",
    "payoutSource": "admin_api"
  }
}
```

**Exact payoutAmount rule**

- If `select2.value === 'Charge'`: `payoutAmount = -1 * numberInput4.value * 100`
- Else: `payoutAmount = numberInput4.value * 100`

**Post-success behavior**

1. Shows success notification
2. Triggers SQL logging insert `sglogForPayoutEntryForHostApi`
3. Clears form and resets supporting queries, hides modal

### 7.3 API #3 — RMS flow (host-only, no Listing ID)

- **Query ID (Retool):** `sgcreatePayoutWithHostIdForRms`
- **Endpoint:** `POST https://payout-service.drivelah.sg/api/add-host-payout-entry`
- **Headers:** `Content-Type: application/json`

**Triggered when**

- User confirms in the `sgDuplicateCheckWithHostIdForRMS` modal

**Request payload (as implemented in Retool)**

```json
{
  "payload": {
    "hostId": "<from sgretrieveHostDetailsForRms.data.hostId[0]>",
    "payoutAmount": "<numberInput5.value * 100; negative if Charge>",
    "payoutCurrency": "SGD",
    "payoutType": "<payout3.value if Payout; charge3.value if Charge>",
    "description": "<textArea9.value>",
    "payoutSource": "admin_api"
  }
}
```

---

## 8. Logging (Retool Database)

SG uses 3 insert queries to log entry creation into `logs_table`:

1. `sglogForPayoutEntry` (trip-based flow)
2. `sglogForPayoutEntryForHostApi` (host/listing-based flow)
3. `sglogForPayoutEntryForHostApiForRms` (RMS host-only flow)

Each inserts:

| Field | Value |
| --- | --- |
| tool identifier | `[Finance] Finance team tool` |
| action | `Created an Entry` |
| `action_user` | `current_user.email` |
| `actioned_at` | `new Date()` |
| `tool_row_id` | Concatenated string including payout/charge type, amount, description |

---

## 9. Known implementation notes / potential follow-ups

1. **Potential copy/paste bug in `select2` change handler**
   In `src/container10.rsx`, `select2` clears `payout2` but SG's payout select is `payout3`. This may prevent correct clearing of payout selection on type change.

2. **Duplicate checking SQL for host/listing flow filters by `payout3.value`**
   If "Charge" is being created in Form 6, consider whether `sgretrievePayoutsForHost.sql` should use:

   ```js
   select2.value === 'Payout' ? payout3.value : charge3.value
   ```

   rather than always `payout3.value`.

3. **Currency formatting in some tables/modals**
   Some SG tables use `currency: "AUD"` in column format options (e.g. in `sgDuplicateCheckWithTId` / `table7`). This may be a display-only mismatch (payload uses SGD correctly).
