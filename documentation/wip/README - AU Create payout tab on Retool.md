# Finance Tool: AU Host Payout Tab (Technical Documentation)

## Table of contents

1. [Overview](#1-overview)
2. [Location in Retool app](#2-location-in-retool-app)
3. [Type selection (Select type container)](#3-type-selection-select-type-container)
4. [Form routing logic](#4-form-routing-logic)
5. [Duplicate check modals](#5-duplicate-check-modals)
6. [Backend queries](#6-backend-queries-au-host-payout-tab)
7. [REST APIs (AU)](#7-rest-apis-au)
8. [Logging (Retool Database)](#8-logging-retool-database)
9. [Known implementation notes / potential follow-ups](#9-known-implementation-notes--potential-follow-ups)

---

## 1. Overview

This Retool app contains an AU Host payout workflow for Finance users to create payout entries or charge entries for AU hosts. The flow includes:

1. A type/category selection section (Payout vs Charge)
2. One of two forms:
   - **Trip-based entry** (requires Trip ID): validates transaction details and performs a duplicate check against existing entries for that trip before calling the payout API.
   - **Host/listing-based entry** (no Trip ID): used for special cases (e.g. Flex+) or when an entry is not linked to a trip. Performs a duplicate check for the host/listing/type before calling the host payout API.
3. A duplicate-check modal shown before creating the payout/charge.

---

## 2. Location in Retool app

### 2.1 UI view (tab)

- `src/tabbedContainer1.rsx` → `<View viewKey="AU Host payout">`

### 2.2 Type selection container

- `src/container8.rsx`

### 2.3 Duplicate check modals

- Trip-based modal: `src/auDuplicateCheckWithTId.rsx` (`auDuplicateCheckWithTId`)
- Host-based modal: `src/auDuplicateCheckWithHostId.rsx` (`auDuplicateCheckWithHostId`)

### 2.4 Backend queries

- Located in `functions.rsx` under `<Folder id="auHostPayoutTab">`

---

## 3. Type selection (Select type container)

### 3.1 Component: `select1` — Payout vs Charge

**Label:** Charge or Pay the Host?

**Options**

- Payout
- Charge

**Behavior**

On change, clears both category selections:

- clears `payout2`
- clears `charge2`

### 3.2 Component: `payout2` — Payout category

**Visible only when:** `select1.value === 'Payout'`

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
| Distance | `distance` |
| Duration | `duration` |

### 3.3 Component: `charge2` — Charge category

**Visible only when:** `select1.value === 'Charge'`

**Options**

| Label | Value |
| --- | --- |
| Misc Charge | `misc_charge` |
| Fuel Charge | `fuel_charge` |

### 3.4 Component: `switch1` — "Is not linked to a trip?"

Shown only for exception cases:

- `payout2.value === 'misc_payout'` **OR** `charge2.value === 'misc_charge'` (and the other category is not selected)

**Label:** Is not linked to a trip?

**Caption:** Use this as exception only. Finance team responsibility.

---

## 4. Form routing logic

There are 2 forms. Exactly one should be visible depending on the selected type/category and whether the flow is trip-based or exception-based.

### 4.1 Form 3 — Trip-based entry (TID required)

**Component:** `form3`
**Title:** Add transaction & amount

This is the default flow when:

- Type is selected
- Category is selected
- NOT Flex+
- NOT exception toggle flow

**Inputs**

| Component | Field | Notes |
| --- | --- | --- |
| `textInput3` | Trip ID | Required |
| `numberInput1` | Amount | Must be > 0; custom validation disallows 0 |
| `textArea5` | Description | Optional |

**On submit triggers**

1. SQL: `retrievetxndetails`
2. SQL: `retrievepayouts`
3. Show modal: `auDuplicateCheckWithTId`

### 4.2 Form 4 — Host/listing-based entry (no TID)

**Component:** `form4`
**Title:** Add host and amount details

This is the exception flow when:

- Category is `flexplus`, **OR**
- `switch1.value === true` **AND** category is `misc_payout` or `misc_charge`

**Inputs**

| Component | Field | Notes |
| --- | --- | --- |
| `textInput4` | Host ID | Required |
| `textInput5` | Listing ID | Required |
| `numberInput2` | Amount | Must be > 0; custom validation disallows 0 |
| `textArea6` | Description | Optional |

**On submit triggers**

1. SQL: `retrieveListingDetails`
2. SQL: `retrievePayoutsForHost`
3. Show modal: `auDuplicateCheckWithHostId`

---

## 5. Duplicate check modals

### 5.1 Modal: `auDuplicateCheckWithTId` (trip-based)

**Displays**

- Trip details (from `retrievetxndetails`)
- Existing entries for that trip (table `table4` fed by `retrievepayouts.data`)

**Proceed button**

- Triggers REST: `createPayoutWithTId` (endpoint with Trip ID)

**Cancel button**

Hides modal and clears/resets:

- clears `form3`
- resets `retrievepayouts`, `retrievetxndetails`
- clears `select1`, `payout2`, `charge2`

### 5.2 Modal: `auDuplicateCheckWithHostId` (host-based)

**Displays**

- Host/listing details (from `retrieveListingDetails`)
- Existing entries for listing/type (table `table5` fed by `retrievePayoutsForHost.data`)

**Proceed button**

- Triggers REST: `createPayoutWithHostId` (endpoint without Trip ID)

**Cancel button**

Hides modal and clears/resets:

- clears `form4`
- resets `retrievePayoutsForHost`, `retrieveListingDetails`
- clears `select1`, `payout2`, `charge2`

---

## 6. Backend queries (AU Host payout tab)

### 6.1 SQL: `retrievetxndetails`

**Purpose:** Look up host/guest/listing information for a Trip ID.

- **File:** `lib/retrievetxndetails.sql`
- **Resource:** Drive mate - Production (MySQL)

**Input dependencies**

- `textInput3.value` (Trip ID)

**Output fields used**

- `guest_id`, `host_id`, `listing_id`, `id`
- `host_firstname`, `host_lastname`, `guest_firstname`, `guest_lastname`

### 6.2 SQL: `retrievepayouts`

**Purpose:** Fetch existing payout entries for the Trip ID to prevent duplicates.

- **File:** `lib/retrievepayouts.sql`
- **Resource:** Drive mate - Production (MySQL)

**Input dependencies**

- `textInput3.value` (Trip ID)

### 6.3 SQL: `retrieveListingDetails`

**Purpose:** Resolve host details and registration number from Listing ID.

- **File:** `lib/retrieveListingDetails.sql`
- **Resource:** Drive mate - Production (MySQL)

**Input dependencies**

- `textInput5.value` (Listing ID)

### 6.4 SQL: `retrievePayoutsForHost`

**Purpose:** Fetch existing payout entries for listing + payoutType for duplicate checking in the exception flow.

- **File:** `lib/retrievePayoutsForHost.sql`
- **Resource:** Drive mate - Production (MySQL)

**Input dependencies**

- `payout2.value` (payoutType)
- `textInput5.value` (listingId)

---

## 7. REST APIs (AU)

Both APIs:

- **Header:** `Content-Type: application/json`
- **Authentication:** none

> **Note:** Amounts are passed in cents (AUD × 100). Charges are passed as negative amounts.

### 7.1 API #1 — With Trip ID (trip-based flow)

- **Query ID (Retool):** `createPayoutWithTId`
- **Endpoint:** `POST https://payout-prod.drivemate.au/api/add-custom-payout-entry`
- **Headers:** `Content-Type: application/json`

**Triggered when**

- User confirms "Ok to proceed. Its not a duplicate" in the `auDuplicateCheckWithTId` modal

**Request payload (as implemented in Retool)**

```json
{
  "payload": {
    "guestId": "<from retrievetxndetails.data.guest_id[0]>",
    "hostId": "<from retrievetxndetails.data.host_id[0]>",
    "listingId": "<from retrievetxndetails.data.listing_id[0]>",
    "tripId": "<from retrievetxndetails.data.id[0]>",
    "payoutAmount": "<numberInput1.value * 100; negative if Charge>",
    "payoutCurrency": "AUD",
    "payoutType": "<payout2.value if Payout; charge2.value if Charge>",
    "description": "<textArea5.value>",
    "payoutSource": "admin_api"
  }
}
```

**Exact payoutAmount rule**

- If `select1.value === 'Charge'`: `payoutAmount = -1 * numberInput1.value * 100`
- Else: `payoutAmount = numberInput1.value * 100`

**Post-success behavior**

1. Shows success notification
2. Triggers SQL logging insert `logForPayoutEntry`
3. Clears form and resets supporting queries, hides modal

### 7.2 API #2 — Without Trip ID (host/listing-based flow)

- **Query ID (Retool):** `createPayoutWithHostId`
- **Endpoint:** `POST https://payout-prod.drivemate.au/api/add-host-payout-entry`
- **Headers:** `Content-Type: application/json`

**Triggered when**

- User confirms "Ok to proceed. Its not a duplicate" in the `auDuplicateCheckWithHostId` modal

**Request payload (as implemented in Retool)**

```json
{
  "payload": {
    "hostId": "<from retrieveListingDetails.data.hostId[0]>",
    "listingId": "<from retrieveListingDetails.data.ListingId[0]>",
    "payoutAmount": "<numberInput2.value * 100; negative if Charge>",
    "payoutCurrency": "AUD",
    "payoutType": "<payout2.value if Payout; charge2.value if Charge>",
    "description": "<textArea6.value>",
    "payoutSource": "admin_api"
  }
}
```

**Exact payoutAmount rule**

- If `select1.value === 'Charge'`: `payoutAmount = -1 * numberInput2.value * 100`
- Else: `payoutAmount = numberInput2.value * 100`

**Post-success behavior**

1. Shows success notification
2. Triggers SQL logging insert `logForPayoutEntryForHostApi`
3. Clears form and resets supporting queries, hides modal

---

## 8. Logging (Retool Database)

Two insert queries are used to log entry creation into `logs_table`:

1. `logForPayoutEntry` (trip-based flow)
2. `logForPayoutEntryForHostApi` (host/listing-based flow)

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

1. `createPayoutWithTId` failure notification currently references `createPayoutWithHostId.data...` in its error message (likely copy/paste). Consider updating to reference `createPayoutWithTId.error` / `createPayoutWithTId.data`.
2. `retrievePayoutsForHost.sql` filters by `payout2.value` even when creating a Charge in Form 4. Confirm desired behavior for charge duplicate checking in the exception flow.
