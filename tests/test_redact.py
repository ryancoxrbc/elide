"""The tests that matter: text must be gone, and the right text must survive."""

from decimal import Decimal

import pytest

from claims_processor.matching import find_candidates
from claims_processor.redact import RedactionError, apply_plan, build_plan, check_plan
from claims_processor.statement import parse_statement
from claims_processor.verify import extract_text, verify


def _plan_for(statement_pdf, amounts):
    pages = parse_statement(str(statement_pdf))
    kept = set()
    for amount in amounts:
        found = find_candidates(pages, Decimal(amount))
        assert len(found) == 1, f"{amount} was not unambiguous"
        kept.add((found[0].page, found[0].row_index))
    return pages, kept, build_plan(pages, kept)


def test_geometry_check_passes_on_a_clean_plan(statement_pdf):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98", "228.00"])
    check_plan(pages, plan, kept)


def test_geometry_check_catches_a_bar_that_would_clip_a_kept_row(statement_pdf):
    """Fattening the bars until they bleed must be refused, not silently applied."""
    import claims_processor.redact as redact

    pages, kept, _ = _plan_for(statement_pdf, ["1322.98"])
    original = redact.ROW_PAD_Y
    redact.ROW_PAD_Y = 9.0  # wider than the 4.2pt gutter
    try:
        bad = build_plan(pages, kept)
        with pytest.raises(RedactionError):
            check_plan(pages, bad, kept)
    finally:
        redact.ROW_PAD_Y = original


def test_unrelated_text_is_removed_from_the_output(statement_pdf, tmp_path):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98", "228.00"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    text = extract_text(out)
    assert "PAYSHAP NORTHWIND TRADING" not in text
    assert "LUMENNET" not in text
    assert "INTEREST EARNED" not in text


def test_claimed_lines_survive(statement_pdf, tmp_path):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98", "228.00"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    text = extract_text(out)
    assert "NETPAY*HARBORLIGHT" in text
    assert "QUAYSIDE MARKET" in text
    assert "1,322.98" in text
    assert "228.00" in text


def test_page_furniture_survives(statement_pdf, tmp_path):
    """Headings and the bank's footer must stay - the document has to stay identifiable."""
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    text = extract_text(out)
    assert "ACCOUNT HOLDER: DOCTOR CORNELIUS" in text
    assert "FSP NUMBER 40100" in text
    assert "DESCRIPTION" in text


def test_balances_are_removed_even_on_claimed_rows(statement_pdf, tmp_path):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    text = extract_text(out)
    assert "1,905.07" not in text  # the balance on the claimed row
    assert "3,226.91" not in text  # a balance on an unrelated row


def test_output_is_still_selectable_text_not_an_image(statement_pdf, tmp_path):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    import pymupdf

    with pymupdf.open(out) as doc:
        page = doc[0]
        assert page.get_text("text").strip(), "page has no extractable text"
        assert not page.get_images(), "page was flattened into an image"


def test_verification_passes_on_a_good_build(statement_pdf, tmp_path):
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98", "228.00"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    result = verify(out, removed=plan.removed_text, kept=plan.kept_text)
    assert result.passed, result.as_report()


def test_verification_fails_when_nothing_was_redacted(statement_pdf):
    """A build that skipped the redaction step must be caught, not shipped."""
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    result = verify(statement_pdf, removed=plan.removed_text, kept=plan.kept_text)
    assert not result.passed
    assert result.leaks


def test_a_receipt_sharing_a_removed_date_is_not_a_false_leak(statement_pdf, tmp_path):
    """Verification must be scoped to the statement's own pages.

    Receipts routinely carry a date or a round amount that also appeared on a
    row we removed. Scanning the whole bundle for those strings flags the
    receipt as a leak and fails a build that is in fact correct.
    """
    import pymupdf

    from claims_processor.bundle import BundleEntry, build_bundle
    from claims_processor.models import ClaimItem, Source

    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    redacted = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), redacted, plan)

    # A receipt quoting a date and an amount that were removed from the statement.
    receipt = tmp_path / "receipt.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Invoice dated 2026-06-03", fontname="helv", fontsize=10)
    page.insert_text((72, 120), "Amount R 809.00", fontname="helv", fontsize=10)
    doc.save(receipt)
    doc.close()

    source = Source(path=receipt.name, page_count=1)
    item = ClaimItem(source=source.path, first_page=1, last_page=1, amount="809.00")
    bundle = tmp_path / "bundle.pdf"
    summary = build_bundle(
        bundle,
        redacted_statement=redacted,
        entries=[BundleEntry(item, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )

    first = summary["index_pages"] + 1
    scoped = verify(
        bundle,
        removed=plan.removed_text,
        kept=plan.kept_text,
        pages=range(first, first + summary["statement_pages"]),
    )
    assert scoped.passed, scoped.as_report()

    # Unscoped, the receipt's own text is misread as a leak - the bug this guards.
    unscoped = verify(bundle, removed=plan.removed_text, kept=plan.kept_text)
    assert not unscoped.passed


def test_a_date_in_the_page_header_is_not_a_leak(statement_pdf, tmp_path):
    """A removed row's date also appears in surviving page furniture."""
    pages, kept, plan = _plan_for(statement_pdf, ["1322.98"])
    out = tmp_path / "redacted.pdf"
    apply_plan(str(statement_pdf), out, plan)

    result = verify(out, removed=plan.removed_text, kept=plan.kept_text)
    assert result.passed, result.as_report()
    assert "ACCOUNT HOLDER: DOCTOR CORNELIUS" in extract_text(out)
