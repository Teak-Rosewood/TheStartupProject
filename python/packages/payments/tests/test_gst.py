from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tsp.payments.gst import (
    EXPORT,
    INTER,
    INTRA,
    financial_year,
    invoice_number,
    split_inclusive,
    state_of_gstin,
    supply_type,
    valid_gstin,
)


def test_gstin_checksum():
    assert valid_gstin("27AAPFU0939F1ZV")  # well-known sample GSTIN
    assert not valid_gstin("27AAPFU0939F1ZW")  # wrong check character
    assert not valid_gstin("27AAPFU0939F1AV") and not valid_gstin("") and not valid_gstin("short")
    assert state_of_gstin("27AAPFU0939F1ZV") == "27"


@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc), ("2026-27", "2627")),
        (datetime(2027, 3, 31, 12, 0, tzinfo=timezone.utc), ("2026-27", "2627")),
        # 31 March 20:00 UTC is already 1 April 01:30 in India: the new financial year
        (datetime(2027, 3, 31, 20, 0, tzinfo=timezone.utc), ("2027-28", "2728")),
        (datetime(2026, 1, 15, tzinfo=timezone.utc), ("2025-26", "2526")),
    ],
)
def test_financial_year_is_judged_in_ist(when, expected):
    assert financial_year(when) == expected


def test_invoice_number_fits_gst_limit():
    assert invoice_number("INV", "2627", 1) == "INV/2627/000001"
    assert len(invoice_number("INV", "2627", 999999)) <= 16
    with pytest.raises(ValueError, match="16"):
        invoice_number("INVOICE", "2627", 1)


def test_supply_type():
    assert supply_type("29", "IN", "29") == INTRA
    assert supply_type("29", "IN", "27") == INTER
    assert (
        supply_type("29", "IN", None) == INTRA
    )  # consumer with no state: place of supply = supplier
    assert supply_type("29", "US", None) == EXPORT


def test_intra_state_splits_eighteen_percent_evenly():
    t = split_inclusive(11800, 18, INTRA)
    assert (t.taxable, t.cgst, t.sgst, t.igst, t.total) == (10000, 900, 900, 0, 11800)


def test_inter_state_and_export():
    t = split_inclusive(11800, 18, INTER)
    assert (t.taxable, t.cgst, t.sgst, t.igst) == (10000, 0, 0, 1800)
    e = split_inclusive(11800, 18, EXPORT)
    assert (e.taxable, e.igst, e.total) == (11800, 0, 11800)


def test_odd_paisa_stays_in_taxable_so_halves_match():
    t = split_inclusive(10001, 18, INTRA)  # tax 1525.6 -> 1526; half 763
    assert t.cgst == t.sgst == 763 and t.total == 10001


@given(
    gross=st.integers(0, 10**10),
    rate=st.sampled_from([0, 5, 12, 18, 28]),
    supply=st.sampled_from([INTRA, INTER, EXPORT]),
)
def test_parts_always_sum_to_the_amount_charged(gross, rate, supply):
    t = split_inclusive(gross, rate, supply)
    assert t.total == gross
    assert min(t.taxable, t.cgst, t.sgst, t.igst) >= 0
    assert t.cgst == t.sgst
