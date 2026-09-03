from decimal import Decimal

import pytest

from claims_processor.matching import find_candidates, suggest_amounts
from claims_processor.models import parse_amount
from claims_processor.statement import parse_statement


@pytest.mark.parametrize(
    "text,expected",
    [
        ("R 1 149,80", "1149.80"),   # space thousands, comma decimal
        ("R1,150.00", "1150.00"),    # glued, dot decimal
        ("R 6,947.64-", "6947.64"),  # trailing minus is a sign, not a digit
        ("1,322.98", "1322.98"),     # no currency symbol
        ("R 0.36", "0.36"),
    ],
)
def test_amount_spellings_normalise(text, expected):
    assert parse_amount(text) == Decimal(expected)


def test_junk_does_not_parse_as_an_amount():
    assert parse_amount("Registration number") is None
    assert parse_amount("") is None


def test_single_match_is_found(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    found = find_candidates(pages, Decimal("1322.98"))
    assert len(found) == 1
    assert found[0].description == "NETPAY*Harborlight"
    assert found[0].column == "debit"


def test_repeated_amount_yields_every_candidate(statement_pdf):
    """Two rows move R809.00 - both must be offered, never auto-picked."""
    pages = parse_statement(str(statement_pdf))
    found = find_candidates(pages, Decimal("809.00"))
    assert len(found) == 2
    assert {c.date for c in found} == {"2026-07-02", "2026-08-30"}


def test_credit_side_is_matched_too(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    found = find_candidates(pages, Decimal("967.50"))
    assert len(found) == 1
    assert found[0].column == "credit"


def test_no_match_returns_nothing(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    assert find_candidates(pages, Decimal("99999.00")) == []


def test_total_is_suggested_first(receipt_pdf):
    assert suggest_amounts(str(receipt_pdf))[0] == "1,322.98"
