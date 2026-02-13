"""
Tests for transaction fingerprinting utility.
"""
import pytest
from datetime import date
from decimal import Decimal

from src.utils.fingerprint import generate_fingerprint


class TestGenerateFingerprint:
    """Test suite for generate_fingerprint function."""
    
    def test_returns_64_character_hex_string(self):
        """Fingerprint should be a 64-character hexadecimal string."""
        fingerprint = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference="REF123"
        )
        
        assert len(fingerprint) == 64
        # Check that it's valid hex (all characters are 0-9 or a-f)
        assert all(c in "0123456789abcdef" for c in fingerprint)
    
    def test_consistent_hash_for_same_inputs(self):
        """Same inputs should always produce the same fingerprint."""
        fingerprint1 = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference="REF123"
        )
        fingerprint2 = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference="REF123"
        )
        
        assert fingerprint1 == fingerprint2
    
    def test_different_inputs_produce_different_fingerprints(self):
        """Different inputs should produce different fingerprints."""
        fp1 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp2 = generate_fingerprint(2, date(2024, 1, 15), 100.50, "REF123")  # Different account
        fp3 = generate_fingerprint(1, date(2024, 1, 16), 100.50, "REF123")  # Different date
        fp4 = generate_fingerprint(1, date(2024, 1, 15), 200.50, "REF123")  # Different amount
        fp5 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF456")  # Different reference
        
        # All fingerprints should be unique
        fingerprints = {fp1, fp2, fp3, fp4, fp5}
        assert len(fingerprints) == 5
    
    def test_missing_reference_handled_gracefully(self):
        """Missing reference number should use empty string."""
        fp_with_none = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference=None
        )
        fp_with_empty = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference=""
        )
        
        # None and empty string should produce the same fingerprint
        assert fp_with_none == fp_with_empty
        assert len(fp_with_none) == 64
    
    def test_date_object_vs_string(self):
        """Date object and ISO string should produce the same fingerprint."""
        fp_date_object = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=100.50,
            reference="REF123"
        )
        fp_date_string = generate_fingerprint(
            bank_account_id=1,
            transaction_date="2024-01-15",
            amount=100.50,
            reference="REF123"
        )
        
        assert fp_date_object == fp_date_string
    
    def test_amount_type_normalization(self):
        """Float, Decimal, and string amounts should normalize identically."""
        fp_float = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp_decimal = generate_fingerprint(1, date(2024, 1, 15), Decimal("100.50"), "REF123")
        fp_string = generate_fingerprint(1, date(2024, 1, 15), "100.50", "REF123")
        
        # All should produce the same fingerprint
        assert fp_float == fp_decimal == fp_string
    
    def test_amount_decimal_precision(self):
        """Amounts with different decimal representations should normalize to 2 decimal places."""
        # 100.5 and 100.50 should be treated as the same
        fp1 = generate_fingerprint(1, date(2024, 1, 15), 100.5, "REF123")
        fp2 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp3 = generate_fingerprint(1, date(2024, 1, 15), Decimal("100.500"), "REF123")
        
        assert fp1 == fp2 == fp3
    
    def test_negative_amounts(self):
        """Negative amounts should be handled correctly."""
        fp_positive = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp_negative = generate_fingerprint(1, date(2024, 1, 15), -100.50, "REF123")
        
        # Positive and negative should produce different fingerprints
        assert fp_positive != fp_negative
        assert len(fp_negative) == 64
    
    def test_zero_amount(self):
        """Zero amount should be handled correctly."""
        fingerprint = generate_fingerprint(1, date(2024, 1, 15), 0.0, "REF123")
        
        assert len(fingerprint) == 64
        # Zero should produce a different fingerprint than non-zero
        fp_nonzero = generate_fingerprint(1, date(2024, 1, 15), 1.0, "REF123")
        assert fingerprint != fp_nonzero
    
    def test_reference_normalization_lowercase(self):
        """Reference numbers should be normalized to lowercase."""
        fp_upper = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp_lower = generate_fingerprint(1, date(2024, 1, 15), 100.50, "ref123")
        fp_mixed = generate_fingerprint(1, date(2024, 1, 15), 100.50, "ReF123")
        
        # All should produce the same fingerprint
        assert fp_upper == fp_lower == fp_mixed
    
    def test_reference_whitespace_stripping(self):
        """Whitespace in reference numbers should be stripped."""
        fp_normal = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp_leading = generate_fingerprint(1, date(2024, 1, 15), 100.50, "  REF123")
        fp_trailing = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123  ")
        fp_both = generate_fingerprint(1, date(2024, 1, 15), 100.50, "  REF123  ")
        
        # All should produce the same fingerprint
        assert fp_normal == fp_leading == fp_trailing == fp_both
    
    def test_special_characters_in_reference(self):
        """Special characters in reference numbers should be preserved."""
        fp1 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF-123/A")
        fp2 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF-123/B")
        
        # Different special character patterns should produce different fingerprints
        assert fp1 != fp2
        assert len(fp1) == 64
    
    def test_large_amounts(self):
        """Large transaction amounts should be handled correctly."""
        fingerprint = generate_fingerprint(
            bank_account_id=1,
            transaction_date=date(2024, 1, 15),
            amount=1234567890.12,
            reference="REF123"
        )
        
        assert len(fingerprint) == 64
    
    def test_very_small_amounts(self):
        """Very small amounts (fractions of cents) should be rounded to 2 decimal places."""
        # 0.001 should be normalized to 0.00
        fp1 = generate_fingerprint(1, date(2024, 1, 15), 0.001, "REF123")
        fp2 = generate_fingerprint(1, date(2024, 1, 15), 0.00, "REF123")
        
        # Should produce the same fingerprint after rounding
        assert fp1 == fp2
    
    def test_date_format_edge_cases(self):
        """Different date representations should normalize correctly."""
        # Date object with single-digit day/month
        fp1 = generate_fingerprint(1, date(2024, 1, 5), 100.50, "REF123")
        # String with zero-padded format
        fp2 = generate_fingerprint(1, "2024-01-05", 100.50, "REF123")
        
        assert fp1 == fp2
    
    def test_account_id_types(self):
        """Different bank account IDs should produce different fingerprints."""
        fp1 = generate_fingerprint(1, date(2024, 1, 15), 100.50, "REF123")
        fp2 = generate_fingerprint(10, date(2024, 1, 15), 100.50, "REF123")
        fp3 = generate_fingerprint(100, date(2024, 1, 15), 100.50, "REF123")
        
        # All should be different
        assert fp1 != fp2 != fp3
        assert fp1 != fp3
    
    def test_real_world_scenario(self):
        """Test with realistic bank transaction data."""
        # Simulate importing the same transaction twice
        transaction_data = {
            "bank_account_id": 5,
            "transaction_date": date(2024, 2, 13),
            "amount": Decimal("1250.75"),
            "reference": "INV-2024-001"
        }
        
        # First import
        fp1 = generate_fingerprint(**transaction_data)
        
        # Second import (simulating duplicate)
        fp2 = generate_fingerprint(**transaction_data)
        
        # Should detect as duplicate
        assert fp1 == fp2
        assert len(fp1) == 64
