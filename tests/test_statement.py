from decimal import Decimal

from elide.statement import parse_statement, transaction_rows


def test_rows_and_columns_are_located(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    assert len(pages) == 1

    rows = [r for r in pages[0].rows if r.is_transaction]
    assert len(rows) == 7

    first = rows[0]
    assert first.date_text == "2026-06-03"
    assert "Interest Earned" in first.description_text
    assert first.debit is None
    assert first.credit.value == Decimal("0.36")
    assert first.balance.value == Decimal("3226.91")

    debit_row = rows[2]
    assert debit_row.debit.value == Decimal("3967.50")
    assert debit_row.credit is None


def test_account_is_carried_onto_rows(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    assert all(r.account == "Transaction Account" for r in pages[0].rows if r.is_transaction)


def test_footer_is_not_mistaken_for_a_row(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    assert not any("FSP number" in r.description_text for r in pages[0].rows)


def test_transaction_rows_helper(statement_pdf):
    pages = parse_statement(str(statement_pdf))
    assert len(transaction_rows(pages)) == 7


def test_statement_is_guessed_from_content_not_page_count(tmp_path):
    """A long invoice must not outrank a short statement."""
    import pymupdf

    from elide.project import discover_pdfs, guess_statement

    long_invoice = pymupdf.open()
    for _ in range(6):
        long_invoice.new_page().insert_text((72, 100), "Tax Invoice - line items")
    long_invoice.save(tmp_path / "a_invoice.pdf")
    long_invoice.close()

    short = pymupdf.open()
    short.new_page().insert_text(
        (72, 100), "Account holder: Doctor Cornelius  Account number: 123  Debit Credit Balance"
    )
    short.new_page().insert_text((72, 100), "more rows")
    short.save(tmp_path / "b_bank.pdf")
    short.close()

    names = discover_pdfs(tmp_path)
    assert guess_statement(tmp_path, names) == "b_bank.pdf"


def test_an_obvious_filename_still_wins(tmp_path):
    import pymupdf

    from elide.project import discover_pdfs, guess_statement

    for name in ("CertifiedStatements.pdf", "invoice.pdf"):
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), "Debit Credit Balance Account holder")
        doc.save(tmp_path / name)
        doc.close()

    assert guess_statement(tmp_path, discover_pdfs(tmp_path)) == "CertifiedStatements.pdf"
