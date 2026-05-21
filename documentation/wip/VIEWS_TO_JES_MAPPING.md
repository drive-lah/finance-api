# ClickHouse Views to Journal Entry Mapping

**Strategy:** Views are the authoritative source of truth. query_builder.py will simply read from these views.

## Available AU Views (36 Total)

### Revenue Recognition (Accrual) - "_a_" views
| JE # | View Name | Purpose |
|------|-----------|---------|
| #2 | `view_AU_a_trip_revenue_earned` | Trip revenue accrued on bookingDisplayEnd |
| #4 | `view_AU_a_incidentals_invoiced` | Incidental invoices created |
| #6 | `view_AU_a_subscription_invoiced` | Subscription invoices created |
| #8 | `view_AU_a_host_trip_earnings_accrual` | Host earnings accrued when trip completes |
| — | `view_AU_a_trip_distance_invoiced` | Distance charges invoiced (if applicable) |
| — | `view_AU_a_host_fuel_charge_collected` | Host fuel charges |
| — | `view_AU_a_host_incidentals_damage` | Host damage payouts |
| — | `view_AU_a_host_incidentals_excess_mileage` | Host excess mileage payouts |
| — | `view_AU_a_host_incidentals_fuel` | Host fuel reimbursements |
| — | `view_AU_a_host_incidentals_tolls` | Host toll reimbursements |
| — | `view_AU_a_host_incidentals_cleanliness` | Host cleanliness payouts |
| — | `view_AU_a_host_flexplus_payout` | Host FlexPlus bonus |
| — | `view_AU_a_host_subscription_collected` | Host subscription collection |
| — | `view_AU_a_host_misc_charge_collected` | Host misc charges |
| — | `view_AU_a_host_misc_payout` | Host misc payouts |
| — | `view_AU_a_host_referral_payout` | Host referral bonus |

### Cash Collection - "_c_" views
| JE # | View Name | Purpose |
|------|-----------|---------|
| #1 | `view_AU_c_trip_cash_collected` | Trip charges collected |
| #3 | `view_AU_c_fuel_refunds` | Fuel charge refunds |
| #5 | `view_AU_c_incidentals_invoice_paid` | Incidental invoices paid |
| #7 | `view_AU_c_subscription_invoice_paid` | Subscription invoices paid |
| #16 | `view_AU_c_stripe_fees_paid` | Stripe platform fees |
| #17 | `view_AU_c_disputes` | Chargeback disputes |
| #18 | `view_AU_c_customer_deposits_received` | Customer deposits received |
| #19 | `view_AU_c_deposit_refunds` | Deposit refunds issued |
| #20 | `view_AU_c_trip_refunds` | Trip refunds issued |
| #21 | `view_AU_c_subscription_refunds` | Subscription refunds |
| #22 | `view_AU_c_invoice_payment_refunds` | Invoice payment refunds |
| #24 | `view_AU_c_stripe_payouts` | Stripe payouts (host transfers) |
| — | `view_AU_c_host_transfers` | Host transfers detail |
| — | `view_AU_c_trip_distance_cash_collected` | Distance charge cash |
| — | `view_AU_c_trip_distance_invoice_paid` | Distance invoice paid |
| — | `view_AU_c_verification_charge_received` | Verification charge received |
| — | `view_AU_c_verification_refunds` | Verification charge refunds |

### Support/Utility Views
| View Name | Purpose |
|-----------|---------|
| `view_AU_intermediate_payout_transactions` | Intermediate payout processing |
| `view_AU_transactions_flat` | Flattened transaction view |
| `view_AU_v_stripe_with_transaction_details` | Stripe data with transaction enrichment |

---

## Architecture: Query Builder Rewrite

### New Pattern
Instead of building complex SQL in query_builder.py, simplify to:

```python
def trip_charges(self, month_str: str) -> str:
    """JE #1: Read from view_AU_c_trip_cash_collected"""
    return f"""
    SELECT amount FROM view_AU_c_trip_cash_collected
    WHERE month = '{month_str}-01'
    """

def trip_revenue_accrual(self, month_str: str) -> str:
    """JE #2: Read from view_AU_a_trip_revenue_earned"""
    return f"""
    SELECT amount FROM view_AU_a_trip_revenue_earned
    WHERE month = '{month_str}-01'
    """
```

### Benefits
- ✅ Single source of truth (views in ClickHouse)
- ✅ No duplicate logic between Python and SQL
- ✅ If view logic changes, JE calculation automatically reflects it
- ✅ Easier to debug (check view, not complex Python query)
- ✅ DBA can fix views without touching Python code

---

## Next Steps

1. **Map all 24 JEs to their views** - Create query_builder methods that simply read from views
2. **Identify broken views** - Run compare_calculated_vs_views.py and see which views have issues
3. **Fix views in ClickHouse** - Update view SQL if needed
4. **Validate** - Re-run comparison, all should match 100%
