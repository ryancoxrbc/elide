import pymupdf

from claims_processor.bundle import BundleEntry, build_bundle
from claims_processor.models import ClaimItem, Match, Source


def test_bundle_contains_index_statement_and_receipts(statement_pdf, receipt_pdf, tmp_path):
    source = Source(path=receipt_pdf.name, page_count=1)
    item = ClaimItem(source=source.path, first_page=1, last_page=1, label="Harborlight", amount="1322.98")
    match = Match(
        item_key=item.key,
        page=1,
        row_index=4,
        column="debit",
        date="2026-08-26",
        description="NETPAY*Harborlight",
        account="Transaction Account",
        confirmed=True,
    )
    out = tmp_path / "bundle.pdf"
    summary = build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, match)],
        claim_folder=receipt_pdf.parent,
    )

    assert summary["statement_pages"] == 1
    assert summary["receipt_pages"] == 1

    with pymupdf.open(out) as doc:
        assert len(doc) == 3  # index + statement + receipt
        assert len(doc.get_toc()) == 3
        first = doc[0].get_text("text")
        assert "Claim summary" in first
        assert "R 1,322.98" in first
        assert "NETPAY*Harborlight" in first
        # Receipts keep their text layer - they are placed, not rasterised.
        assert "Invoice 4469" in doc[2].get_text("text")


def test_receipt_pages_are_normalised_to_a4(statement_pdf, receipt_pdf, tmp_path):
    letter = tmp_path / "letter.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "US Letter receipt")
    doc.save(letter)
    doc.close()

    source = Source(path=letter.name, page_count=1)
    item = ClaimItem(source=source.path, first_page=1, last_page=1, amount="10.00")
    out = tmp_path / "bundle.pdf"
    build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )
    with pymupdf.open(out) as bundle:
        assert round(bundle[-1].rect.width) == 595
        assert round(bundle[-1].rect.height) == 842


def test_one_pdf_can_hold_two_separate_receipts(receipt_pdf, statement_pdf, tmp_path):
    """A two-page scan where each page is a different receipt."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((72, 100), "Invoice A - R100.00")
    doc.new_page(width=595, height=842).insert_text((72, 100), "Invoice B - R200.00")
    scan = tmp_path / "scan.pdf"
    doc.save(scan)
    doc.close()

    source = Source(path=scan.name, page_count=2)
    item_a = ClaimItem(source=source.path, first_page=1, last_page=1, label="A", amount="100.00")
    item_b = ClaimItem(source=source.path, first_page=2, last_page=2, label="B", amount="200.00")
    assert item_a.key != item_b.key  # distinct ids even though the source is shared

    out = tmp_path / "bundle.pdf"
    summary = build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item_a, source, None), BundleEntry(item_b, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )
    assert summary["receipt_pages"] == 2
    with pymupdf.open(out) as bundle:
        assert "Invoice A" in bundle[-2].get_text("text")
        assert "Invoice B" in bundle[-1].get_text("text")


def test_a_receipt_spanning_several_pages_places_them_all(statement_pdf, tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"page {i}")
    multi = tmp_path / "multi.pdf"
    doc.save(multi)
    doc.close()

    source = Source(path=multi.name, page_count=3)
    item = ClaimItem(source=source.path, first_page=1, last_page=3, amount="50.00")
    out = tmp_path / "bundle.pdf"
    summary = build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )
    assert summary["receipt_pages"] == 3


def test_rotation_is_looked_up_per_page(statement_pdf, tmp_path):
    """One page rotated, one not - both in the same claim item."""
    doc = pymupdf.open()
    doc.new_page(width=400, height=600)
    doc.new_page(width=400, height=600)
    two_page = tmp_path / "two.pdf"
    doc.save(two_page)
    doc.close()

    source = Source(path=two_page.name, page_count=2, rotations={"1": 90})
    assert source.rotation_of(1) == 90
    assert source.rotation_of(2) == 0

    item = ClaimItem(source=source.path, first_page=1, last_page=2, amount="1.00")
    out = tmp_path / "bundle.pdf"
    summary = build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )
    assert summary["receipt_pages"] == 2  # both item pages placed despite differing rotation
