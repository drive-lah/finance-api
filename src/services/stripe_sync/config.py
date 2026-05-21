"""Stripe sync configuration: code maps, account mappings, reference patterns."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Optional

# ============================================================================
# PAYOUT TYPE CODE MAPPING (section 3.4 from architecture)
# Maps Stripe transfer code to Finance API expense account
# ============================================================================

# ============================================================================
# LEGACY: TRANSFER CODE MAPPING (Phase 1-2, kept for backwards compatibility)
# Maps Stripe transfer code to Finance API expense account
# Phase 3+ uses PAYOUTTYPE_TO_ACCOUNT instead
# ============================================================================

CODE_TO_ACCOUNT = {
    "1": "5021",  # Damage
    "2": "5024",  # Excess mileage (FIXED BUG: was uncaptured in ClickHouse views)
    "3": "5040",  # Superhost
    "4": "5041",  # Sticker
    "5": "5002",  # Flex+
    "6": "5023",  # Fuel
    "7": "5024",  # Excess mileage (alternate code)
    "8": "5042",  # Misc
    "9": "5042",  # Misc
    "10": "5042",  # Misc
    "11": "5042",  # Misc
    "12": "5042",  # Misc
}

# ============================================================================
# PAYOUTTYPE MAPPING (Phase 3+)
# Maps payout_entries.payoutType to Finance API debit account
# Used for JEs #8-15 (host payouts accrual)
# ============================================================================

PAYOUTTYPE_TO_ACCOUNT = {
    "damage": "5021",  # Incidentals Payout - Damage
    "excess_mileage": "5024",  # Incidentals Payout - Excess Mileage
    "flexplus": "5002",  # Host Payouts - Flex+
    "fuel_refund": "5023",  # Incidentals Payout - Fuel (positive amounts)
    "fuel_charge": "5023",  # Incidentals Payout - Fuel (charges, negative amounts)
    "misc_payout": "5042",  # Host Payouts - Misc
    "cleanliness": "5022",  # Incidentals Payout - Cleanliness
    "tolls": "5020",  # Incidentals Payout - Tolls
    "late_return": "5044",  # Incidentals Payout - Late Return
    "misc_charge": "4025",  # Incidentals Revenue - Other (not payout)
    "referral": "5025",  # Host Payouts - Referral (exact mapping TBD Phase 4)
    "subscription": "4010",  # Subscription Revenue - Device (not payout)
}

PAYOUTTYPE_TO_NAME = {
    "damage": "Damage",
    "excess_mileage": "Excess Mileage",
    "flexplus": "Flex+",
    "fuel_refund": "Fuel Refund",
    "fuel_charge": "Fuel Charge",
    "misc_payout": "Misc Payout",
    "cleanliness": "Cleanliness",
    "tolls": "Tolls",
    "late_return": "Late Return",
    "misc_charge": "Misc Charge",
    "referral": "Referral",
    "subscription": "Subscription",
}

CODE_TO_NAME = {
    "1": "Damage",
    "2": "Excess Mileage",
    "3": "Superhost",
    "4": "Sticker",
    "5": "Flex+",
    "6": "Fuel",
    "7": "Excess Mileage",
    "8": "Misc",
    "9": "Misc",
    "10": "Misc",
    "11": "Misc",
    "12": "Misc",
}

# ============================================================================
# COA MAPPING (section 7 from architecture)
# ============================================================================

COA_MAP = {
    # Assets
    "1000": "Bank - Primary Operating",
    "1001": "Bank - Wise SGD",
    "1016": "Bank - OCBC Bank (OCBC 3001)",
    "1017": "Bank - Stripe (Stripe Platform)",  # Clearing account
    "1200": "Trade Receivables (Stripe AR)",
    # Liabilities
    "2100": "Deferred Trip Revenue",
    "2110": "Customer Deposits Held",
    "2120": "Host Payables",
    # Revenue
    "4000": "GBV - P2P",
    "4010": "Subscription Revenue - Device",
    "4025": "Incidentals Revenue - Other",
    # Expenses (Host payouts)
    "5000": "Host Payouts - P2P",
    "5002": "Host Payouts - Flex+",
    "5010": "Payment Processing Fees",
    "5021": "Incidentals Payout - Damage",
    "5023": "Incidentals Payout - Fuel",
    "5024": "Incidentals Payout - Excess Mileage",
    "5040": "Host Payouts - Superhost",
    "5041": "Host Payouts - Sticker",
    "5042": "Host Payouts - Misc",
    "5051": "Chargebacks",
    "5052": "Trip Refunds",
    "5053": "Invoice Refunds",
    "5054": "Subscription Refunds",
}

# ============================================================================
# REGION CONFIG
# ============================================================================

REGIONS = {
    "SG": {
        "name": "Singapore",
        "currency": "SGD",
        "entity_id": 2,
        "stripe_platform_account": "1017",  # Stripe Platform SGD
        "stripe_connect_account": "1018",  # Stripe Connect SGD
        "bank_account": "1016",  # OCBC 3001 SGD
        "company_bank": "OCBC",  # Company bank identifier for account detection
    },
    "AU": {
        "name": "Australia",
        "currency": "AUD",
        "entity_id": 3,
        "stripe_platform_account": "1019",  # Stripe Platform AUD
        "stripe_connect_account": "1020",  # Stripe Connect AUD
        "bank_account": "1018",  # TBD - AU bank account (CMB expected)
        "company_bank": "CMB",  # Commonwealth Bank identifier for account detection
    },
}

# ============================================================================
# RMS PARTNER ACCOUNT IDENTIFICATION (Phase 4: P2P vs RMS Differentiation)
# Maps Sharetribe user IDs to identify RMS partner company accounts
# Used to distinguish P2P revenue (GBV 4000) from RMS revenue (GBV 4001)
# ============================================================================

# RMS PARTNER ACCOUNTS (Remote Management Service - All regions)
RMS_OWNER_USER_IDS = {
    "AU": {
        # Australia RMS Partner Accounts (13 total - rms1-rms13)
        # Email Pattern: rms*.drivemate@gmail.com
        # Stripe Accounts: acct_1OCbXj*, acct_1OERHq*, etc.
        
        "65544aed-ba14-4c9c-b1e8-2406a8f4eb59",  # rms1.drivemate@gmail.com | acct_1OCbXjQnAg7Zqg5y
        "6555bd73-6e9e-474e-9ee3-83cd44180ecd",  # rms2.drivemate@gmail.com | acct_1OEPYGQmWBV9Zh4G
        "6555c187-85a7-4769-85ff-c9f48a46629e",  # rms3.drivemate@gmail.com | acct_1OERHqQWCy0txr58
        "6555c464-f928-489e-9809-a0032a9163fc",  # rms4.drivemate@gmail.com | acct_1OERNsQgHcV47Res
        "6555c862-cec8-457b-b25a-1b7acfa5ad68",  # rms5.drivemate@gmail.com | acct_1OERTuH9dt93eFNq
        "6555cbd9-9c3a-48cb-86b0-cf9beebe4ad5",  # rms6.drivemate@gmail.com | acct_1OERc3QZAyPXXaDr
        "6559ac55-bf9d-460c-9fbb-c57310c552c5",  # rms7.drivemate@gmail.com | acct_1OJV* (Stripe ID TBD)
        "656d3ef6-619c-4a1a-8151-9cc52ffa8965",  # rms8.drivemate@gmail.com | acct_1OJVkCQaCeiornSf
        "656d45dc-9ff6-4c75-a06d-193e8fd10bbe",  # rms9.drivemate@gmail.com | acct_1OJVxnH63nk22MW0
        # rms10: MISSING - Sharetribe user ID not found in au_users table
        "656d53c6-c290-4b17-8f56-b43eb8c9d15c",  # rms11.drivemate@gmail.com | acct_1OJWArH5zTDxfmIq
        "656d57f1-6945-46d1-9732-75622ad48d60",  # rms12.drivemate@gmail.com | acct_1OJWF1HKEXkbY9QD
        "656d5a71-68e6-4927-ae06-2c3b40c71d75",  # rms13.drivemate@gmail.com | acct_1OJWL7QeSWLFVtk2
        "67cf8550-0509-4df9-914c-f1d07a94b2f9",  # rms.drivemate@gmail.com | acct_1OKuPBH* (Generic RMS account)
    },
    "SG": {
        # Singapore RMS Partner Accounts (14+ total)
        # Email Pattern: rms*.drivelah@gmail.com or rms*.drivelah@gmail.sg
        # Note: SG has more RMS accounts than AU (rms1-7, rms9, rms11-16, rms20)
        
        "65c5fc11-fbc6-4d45-9466-257621807687",  # rms1.drivelah@gmail.com
        "624455b0-6117-4efe-afd8-f7ed735cda0b",  # rms2.drivelah@gmail.com
        "624468d4-e937-43d8-ba38-104e8b9f0123",  # rms3.drivelah@gmail.com
        "6332f2a9-2d83-4c5e-8baa-f0353443d260",  # rms4.drivelah@gmail.com
        "67861010-a430-4b10-b6da-45bb038a132b",  # rms5.drivelah@gmail.com
        "625f8df9-6069-4c8c-bffb-be1ca41c6740",  # rms6.drivelah@gmail.com
        "626b9e56-7c97-4480-8278-2370c9d51132",  # rms7.drivelah@gmail.com
        # rms8: MISSING in SG
        "626a4924-0479-4781-99c0-1bc944b67235",  # rms9.drivelah@gmail.com
        # rms10: MISSING in SG
        "626bbb6e-70f0-4efc-8004-eecda4abdd3e",  # rms11.drivelah@gmail.com
        "62733450-f23e-482a-bc17-b8e219e561be",  # rms12.drivelah@gmail.com
        "62da0ff0-8f6b-44b1-a073-cc343620b722",  # rms13.drivelah@gmail.com
        "624472e7-57a0-4f27-b21a-346c30005404",  # rms14.drivelah@gmail.com
        "635b5dc9-5748-4123-9427-85818507b922",  # rms15.drivelah@gmail.com
        "626baa36-ac82-4804-8870-6f9306e4b0bf",  # rms16.drivelah@gmail.com
        "65c49a50-0686-4e52-ac47-b8a9687c93b2",  # rms20.drivelah@gmail.com
    },
}

# Alias for backwards compatibility and clarity
COMPANY_OWNER_USER_IDS = RMS_OWNER_USER_IDS


# ============================================================================
# RMS CONNECTED ACCOUNT ID MAPPING (Phase 4: Revenue Matching)
# Maps Sharetribe user IDs to Stripe connected account IDs
# CRITICAL: Used to match transactions in balance_transactions table
# to determine if revenue should be classified as GBV 4001 (RMS) or 4000 (P2P)
# ============================================================================

RMS_OWNER_CONNECTED_ACCOUNT_IDS = {
    "AU": {
        "65544aed-ba14-4c9c-b1e8-2406a8f4eb59": "acct_1OCbXjQnAg7Zqg5y",  # rms1.drivemate@gmail.com
        "6555bd73-6e9e-474e-9ee3-83cd44180ecd": "acct_1OEPYGQmWBV9Zh4G",  # rms2.drivemate@gmail.com
        "6555c187-85a7-4769-85ff-c9f48a46629e": "acct_1OERHqQWCy0txr58",  # rms3.drivemate@gmail.com
        "6555c464-f928-489e-9809-a0032a9163fc": "acct_1OERNsQgHcV47Res",  # rms4.drivemate@gmail.com
        "6555c862-cec8-457b-b25a-1b7acfa5ad68": "acct_1OERTuH9dt93eFNq",  # rms5.drivemate@gmail.com
        "6555cbd9-9c3a-48cb-86b0-cf9beebe4ad5": "acct_1OERc3QZAyPXXaDr",  # rms6.drivemate@gmail.com
        "6559ac55-bf9d-460c-9fbb-c57310c552c5": "acct_1OE4tYHKtN9dUaUe",  # rms7.drivemate@gmail.com
        "656d3ef6-619c-4a1a-8151-9cc52ffa8965": "acct_1OJVkCQaCeiornSf",  # rms8.drivemate@gmail.com
        "656d45dc-9ff6-4c75-a06d-193e8fd10bbe": "acct_1OJVxnH63nk22MW0",  # rms9.drivemate@gmail.com
        "656d53c6-c290-4b17-8f56-b43eb8c9d15c": "acct_1OJWArH5zTDxfmIq",  # rms11.drivemate@gmail.com
        "656d57f1-6945-46d1-9732-75622ad48d60": "acct_1OJWF1HKEXkbY9QD",  # rms12.drivemate@gmail.com
        "656d5a71-68e6-4927-ae06-2c3b40c71d75": "acct_1OJWL7QeSWLFVtk2",  # rms13.drivemate@gmail.com
        "67cf8550-0509-4df9-914c-f1d07a94b2f9": "acct_1R1fFLQeMResL1VC",  # rms.drivemate@gmail.com (generic)
    },
    "SG": {
        "65c5fc11-fbc6-4d45-9466-257621807687": "acct_1OhrEU2cS1DP8vWa",  # rms1 (vigneshsuran.caretaker@gmail.com)
        "624455b0-6117-4efe-afd8-f7ed735cda0b": "acct_1Kj1Z8GbfaCuCMSn",  # rms2.drivelah@gmail.com
        "624468d4-e937-43d8-ba38-104e8b9f0123": "acct_1Kj2e7Ggs4dW237O",  # rms3.drivelah@gmail.com
        "624472e7-57a0-4f27-b21a-346c30005404": "acct_1Kj3GXGgv77ZK4yI",  # rms4.drivelah@gmail.com
        "6252ccca-5e29-4c36-83f8-77f2c9dcf085": "acct_1KmzyLGdwK7XUuJ0",  # rms5.drivelah@gmail.sg
        "625f8df9-6069-4c8c-bffb-be1ca41c6740": "acct_1KqVNfGhtSeycIhW",  # rms6.drivelah@gmail.com
        "626a4924-0479-4781-99c0-1bc944b67235": "acct_1KtSJy2ckZZCKHL7",  # rms7.drivelah@gmail.com
        "626a847c-48e4-4ab2-a5f0-ea39259f64bb": "acct_1KtWHNGgDMZOMcSY",  # rms8.drivelah@gmail.com
        "626b9e56-7c97-4480-8278-2370c9d51132": "acct_1KtpH6Gf9Id3eAYb",  # rms9.drivelah@gmail.com
        "626baa36-ac82-4804-8870-6f9306e4b0bf": "acct_1KtqnD2f6lRB1l1E",  # rms10.drivelah@gmail.com
        "626bbb6e-70f0-4efc-8004-eecda4abdd3e": "acct_1KtqvxGamNXz2rUY",  # rms11.drivelah@gmail.com
        "62733450-f23e-482a-bc17-b8e219e561be": "acct_1KvuQbGhTCfo0DGN",  # rms12.drivelah@gmail.com
        "62da0ff0-8f6b-44b1-a073-cc343620b722": "acct_1LOC032cwv5R5Lec",  # rms13.drivelah@gmail.com
        "6332f2a9-2d83-4c5e-8baa-f0353443d260": "acct_1LmdW7GhVIOn4Ccq",  # rms14.drivelah@gmail.com
        "635b5dc9-5748-4123-9427-85818507b922": "acct_1LxkZm2cBxBrr1SU",  # rms15.drivelah@gmail.com
    },
}


# ============================================================================
# TRANSACTIONAL CONNECT ACCOUNT IDS (Phase 5: Platform ↔ Connect Transfers)
# Company-owned Stripe Connect accounts for verification charges and deposits
# Used to identify cash flows between Platform and these Connect accounts
# ============================================================================

TRANSACTIONAL_OWNER_CONNECTED_ACCOUNT_IDS = {
    "SG": "acct_1EhuMGAcVqeggTlg",  # SG transactional account (deposits/verification)
    "AU": "acct_1JkKmQQWb9mOwfae",  # AU transactional account (deposits/verification)
}


# ============================================================================
# HELPER: All Company-Owned Connect Accounts (RMS + Transactional)
# ============================================================================

def get_all_company_owned_connect_accounts(region: str) -> set:
    """
    Get all company-owned Connect account IDs for a region (RMS + Transactional).
    
    Args:
        region: 'SG' or 'AU'
    
    Returns:
        Set of all company-owned Stripe connected_account_ids
    """
    region = region.upper()
    rms_accounts = set(RMS_OWNER_CONNECTED_ACCOUNT_IDS.get(region, {}).values())
    transactional = {TRANSACTIONAL_OWNER_CONNECTED_ACCOUNT_IDS.get(region)}
    return rms_accounts | transactional


def is_company_account(connected_account: Optional[dict] = None, region: Optional[str] = None,
                      connected_account_id: Optional[str] = None, sharetribe_user_id: Optional[str] = None) -> bool:
    """
    Determine if a connected account is company-owned (RMS).
    
    Supports two matching methods:
    1. Via connected_account dict (extracts sharetribe_user_id from metadata)
    2. Via explicit connected_account_id string (for balance_transactions matching)
    
    Args:
        connected_account: Dict with 'metadata' field containing 'sharetribe-user-id'.
                          If provided, sharetribe_user_id is extracted from metadata.
        region: Optional region ('SG' or 'AU'). If provided, checks that region's accounts only.
               If not provided, checks both regions.
        connected_account_id: Optional Stripe account ID (acct_*) to match directly.
                             Used for balance_transactions revenue classification.
        sharetribe_user_id: Optional Sharetribe user ID to match directly.
                           Used if already extracted.
    
    Returns:
        True if account is in RMS_OWNER_USER_IDS or RMS_OWNER_CONNECTED_ACCOUNT_IDS.
        False otherwise.
    
    Usage Examples:
        >>> # Check via connected_account dict (extracts from metadata)
        >>> acct = {'metadata': {'sharetribe-user-id': '65544aed-ba14-4c9c-b1e8-2406a8f4eb59'}}
        >>> is_company_account(acct, 'AU')
        True
        
        >>> # Check via Stripe account ID (for balance_transactions)
        >>> is_company_account(connected_account_id='acct_1OCbXjQnAg7Zqg5y', region='AU')
        True
        
        >>> # Check via Sharetribe user ID
        >>> is_company_account(sharetribe_user_id='65544aed-ba14-4c9c-b1e8-2406a8f4eb59', region='AU')
        True
    """
    try:
        # Method 1: Direct connected_account_id matching
        if connected_account_id:
            if region:
                region_mapping = RMS_OWNER_CONNECTED_ACCOUNT_IDS.get(region.upper(), {})
                return connected_account_id in region_mapping.values()
            else:
                # Check all regions
                for region_mapping in RMS_OWNER_CONNECTED_ACCOUNT_IDS.values():
                    if connected_account_id in region_mapping.values():
                        return True
                return False
        
        # Method 2: Direct sharetribe_user_id matching
        if sharetribe_user_id:
            if region:
                region_ids = COMPANY_OWNER_USER_IDS.get(region.upper(), set())
                return sharetribe_user_id in region_ids
            else:
                for region_ids in COMPANY_OWNER_USER_IDS.values():
                    if sharetribe_user_id in region_ids:
                        return True
                return False
        
        # Method 3: Extract from connected_account dict
        if connected_account:
            metadata = connected_account.get("metadata", {})
            if isinstance(metadata, str):
                # Handle case where metadata is JSON string
                import json
                metadata = json.loads(metadata)
            
            user_id = metadata.get("sharetribe_user_id") or metadata.get("sharetribe-user-id")
            if not user_id:
                return False
            
            if region:
                region_user_ids = COMPANY_OWNER_USER_IDS.get(region.upper(), set())
                return user_id in region_user_ids
            else:
                for region_ids in COMPANY_OWNER_USER_IDS.values():
                    if user_id in region_ids:
                        return True
                return False
        
        # No matching method provided
        return False
        
    except (KeyError, TypeError, AttributeError):
        return False


# ============================================================================
# REFERENCE NUMBER PATTERNS
# Generates standardized reference numbers for journal entries
# ============================================================================

class ReferencePattern:
    """Build standardized reference numbers for Stripe sync journal entries."""
    
    @staticmethod
    def build(region: str, suffix: str, month_str: str) -> str:
        """
        Build reference number: STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}
        
        Args:
            region: 'SG' or 'AU'
            suffix: JE-specific code (e.g., 'JE01-SG', 'C-TRIP-CASH')
            month_str: 'YYYY-MM' format
        
        Returns:
            Reference number (e.g., 'STRIPE-SG-JE01-SG-2025-12')
        """
        return f"STRIPE-{region}-{suffix}-{month_str}"


# ============================================================================
# JOURNAL ENTRY SPECIFICATION (JESpec Dataclass)
# Intermediate data structure passed from query_builder → sync_service
# Specifies the complete parameters needed to create a PostgreSQL JournalEntry
# ============================================================================

@dataclass
class JESpec:
    """
    Journal Entry Specification - parameters for creating a PostgreSQL JournalEntry.
    
    Passed from _generate_all_je_specs() to _create_journal_entries() or _create_transfer_transactions().
    Contains all information needed to construct a complete JE record or transfer transaction.
    
    Attributes:
        reference_suffix: Code suffix for reference_number (e.g. 'JE01-SG')
        entry_date: Date the JE should be recorded (month end date)
        description: Full description of the JE (e.g. 'JE #1: Trip charges (cash) - Dec 2025 ($1,234.56)')
        debit_code: COA code for debit account (e.g. '1017')
        credit_code: COA code for credit account (e.g. '2100')
        amount: Amount in SGD/AUD (Decimal, rounded to 2 places)
        je_number: Journal entry number 1-25 (used for transfer categorization)
        is_transfer: True if this is an internal transfer JE (JE #23-24), False otherwise
    """
    reference_suffix: str
    entry_date: date
    description: str
    debit_code: str
    credit_code: str
    amount: Decimal
    je_number: int
    is_transfer: bool
