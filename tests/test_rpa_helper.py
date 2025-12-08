"""
Simple tests for rpa_helper functions.
Run with: pytest tests/ -v
"""
import sys
import os

# Add parent directory to path so we can import rpa_helper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa_helper import check_owed, check_paid


# ==================== check_owed tests ====================

def test_check_owed_finds_keyword():
    """Should return True when keyword exists in a row"""
    payment_owed = [
        "YAZILI SINAV HARCI 05.12.2025 1200",
        "UYGULAMA SINAV HARCI 06.12.2025 1600"
    ]
    assert check_owed("YAZILI SINAV HARCI", payment_owed) == True


def test_check_owed_returns_false_when_not_found():
    """Should return False when keyword is not in any row"""
    payment_owed = [
        "YAZILI SINAV HARCI 05.12.2025 1200",
    ]
    assert check_owed("ÖZEL DERS", payment_owed) == False


def test_check_owed_handles_empty_list():
    """Should return False for empty list"""
    assert check_owed("YAZILI SINAV HARCI", []) == False


def test_check_owed_partial_match():
    """Should find partial matches (substring)"""
    payment_owed = ["YZL. SNV. HARCI 1200"]
    assert check_owed("YZL. SNV. HARCI", payment_owed) == True


# ==================== check_paid tests ====================

def test_check_paid_finds_keyword():
    """Should return True when keyword exists in payments"""
    payments_paid = [
        "YAZILI SINAV HARCI 01.12.2025 1200 ÖDEDİ",
        "TAKSİT 02.12.2025 500 ÖDEDİ"
    ]
    assert check_paid("TAKSİT", payments_paid) == True


def test_check_paid_returns_false_when_not_found():
    """Should return False when keyword is not found"""
    payments_paid = ["YAZILI SINAV HARCI 1200 ÖDEDİ"]
    assert check_paid("BELGE ÜCRETİ", payments_paid) == False


def test_check_paid_handles_empty_list():
    """Should return False for empty list"""
    assert check_paid("TAKSİT", []) == False
