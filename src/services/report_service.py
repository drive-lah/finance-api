"""
Report Service

Business logic for generating financial reports.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from src.models import (
    FinanceJournalLine,
    FinanceJournalEntry,
    FinanceAccount,
    JournalEntryStatus,
    AccountType,
)


class ReportService:
    """Service for generating financial reports."""
    
    def get_trial_balance(
        self,
        db: Session,
        entity_id: int,
        as_of_date: Optional[date] = None
    ) -> dict:
        """
        Generate a trial balance report showing account balances.
        
        The trial balance lists all accounts with their debit and credit balances,
        ensuring that total debits equal total credits.
        
        Args:
            db: Database session
            entity_id: ID of the entity to generate report for
            as_of_date: Report as of this date (defaults to today if None)
        
        Returns:
            dict with structure:
            {
                "entity_id": int,
                "as_of_date": str,
                "accounts": [
                    {
                        "account_code": str,
                        "account_name": str,
                        "account_type": str,
                        "debit_balance": float,
                        "credit_balance": float,
                        "net_balance": float
                    },
                    ...
                ],
                "totals": {
                    "total_debits": float,
                    "total_credits": float
                }
            }
        """
        if as_of_date is None:
            as_of_date = date.today()
        
        # Query all Posted journal lines up to as_of_date
        # Join with journal_entry to filter by status and entry_date
        # Join with finance_accounts to get account details
        query = (
            db.query(
                FinanceJournalLine.account_code,
                FinanceAccount.name.label('account_name'),
                FinanceAccount.account_type,
                func.sum(FinanceJournalLine.debit_amount).label('total_debit'),
                func.sum(FinanceJournalLine.credit_amount).label('total_credit')
            )
            .join(
                FinanceJournalEntry,
                FinanceJournalLine.entry_id == FinanceJournalEntry.id
            )
            .join(
                FinanceAccount,
                and_(
                    FinanceJournalLine.account_code == FinanceAccount.code,
                    or_(
                        FinanceAccount.entity_id == entity_id,   # entity-specific (bank accounts)
                        FinanceAccount.entity_id == None          # group-level (all other accounts)
                    )
                )
            )
            .filter(FinanceJournalLine.entity_id == entity_id)
            .filter(FinanceJournalEntry.status == JournalEntryStatus.POSTED)
            .filter(FinanceJournalEntry.entry_date <= as_of_date)
            .group_by(
                FinanceJournalLine.account_code,
                FinanceAccount.name,
                FinanceAccount.account_type
            )
            .order_by(FinanceJournalLine.account_code)
        )
        
        results = query.all()
        
        # Process results into structured output
        accounts = []
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")
        
        for row in results:
            debit_balance = row.total_debit or Decimal("0.00")
            credit_balance = row.total_credit or Decimal("0.00")
            net_balance = debit_balance - credit_balance
            
            accounts.append({
                "account_code": row.account_code,
                "account_name": row.account_name,
                "account_type": row.account_type.value,
                "debit_balance": float(debit_balance),
                "credit_balance": float(credit_balance),
                "net_balance": float(net_balance)
            })
            
            total_debits += debit_balance
            total_credits += credit_balance
        
        # Group accounts by type
        grouped_accounts: dict[str, list[dict]] = {}
        for account_type in AccountType:
            grouped_accounts[account_type.value] = []
        
        for account in accounts:
            account_type = account["account_type"]
            grouped_accounts[account_type].append(account)
        
        # Remove empty groups
        grouped_accounts = {
            k: v for k, v in grouped_accounts.items() if v
        }
        
        return {
            "entity_id": entity_id,
            "as_of_date": as_of_date.isoformat(),
            "accounts_by_type": grouped_accounts,
            "accounts": accounts,  # Flat list for backward compatibility
            "totals": {
                "total_debits": float(total_debits),
                "total_credits": float(total_credits)
            }
        }


# Singleton instance
report_service = ReportService()
