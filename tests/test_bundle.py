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


def _index_text(path):
    """Text of every index page - the ones carrying the claim summary table."""
    with pymupdf.open(path) as doc:
        pages = [doc[i].get_text("text") for i in range(len(doc))]
    return "\n".join(p for p in pages if "Supporting document" in p)


def test_a_long_name_and_description_still_print_in_full_rows(statement_pdf, receipt_pdf, tmp_path):
    """The summary table used to blank whole cells rather than wrap them.

    ``insert_textbox`` draws nothing at all when its text does not fit the
    rectangle, so one long supplier name took the filename and page range down
    with it, and a long statement description took the matched page number and
    account with it - intermittently, depending on how long the values were.
    """
    long_name = tmp_path / "Scanned_Documents_From_The_Office_Printer_2026_08_26.pdf"
    long_name.write_bytes(receipt_pdf.read_bytes())

    source = Source(path=long_name.name, page_count=1)
    item = ClaimItem(
        source=source.path,
        first_page=1,
        last_page=1,
        label="Woolworths Food Sandton City Superstore Branch 4471",
        amount="1322.98",
    )
    match = Match(
        item_key=item.key,
        page=7,
        row_index=4,
        column="debit",
        date="2026-08-26",
        description="CARD PURCHASE WOOLWORTHS SANDTON CITY SUPERSTORE 447102",
        account="Gold Business Cheque Account",
        confirmed=True,
    )
    out = tmp_path / "bundle.pdf"
    build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, match)],
        claim_folder=tmp_path,
    )

    # Wrapping breaks lines, so compare on text with the line breaks taken out.
    flat = " ".join(_index_text(out).split())
    assert "Woolworths Food Sandton City Superstore Branch 4471" in flat
    assert "(p.1)" in flat
    assert "p.7" in flat  # the statement page the line was found on
    assert "Gold Business Cheque Account" in flat
    assert "CARD PURCHASE WOOLWORTHS SANDTON CITY SUPERSTORE 447102" in flat

    # A filename with nothing to break on is split wherever it has to be, so
    # look for it with the wrapping squeezed out entirely.
    assert long_name.name in "".join(_index_text(out).split())


def test_an_unmatched_claim_says_so(statement_pdf, receipt_pdf, tmp_path):
    source = Source(path=receipt_pdf.name, page_count=1)
    item = ClaimItem(source=source.path, first_page=1, last_page=1, label="Vet", amount="10.00")
    not_found = Match(
        item_key=item.key,
        page=0,
        row_index=-1,
        column="",
        date="",
        description="",
        account="",
        confirmed=True,
        not_found=True,
    )
    out = tmp_path / "bundle.pdf"
    build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, None), BundleEntry(item, source, not_found)],
        claim_folder=tmp_path,
    )
    flat = " ".join(_index_text(out).split())
    assert "Not matched" in flat
    assert "Not found on statement" in flat


def test_a_summary_too_long_for_one_page_repeats_the_column_headings(
    statement_pdf, receipt_pdf, tmp_path
):
    source = Source(path=receipt_pdf.name, page_count=1)
    entries = []
    for n in range(40):
        item = ClaimItem(
            source=source.path,
            first_page=1,
            last_page=1,
            label=f"Supplier {n} trading as a name long enough to need two lines",
            amount="100.00",
        )
        entries.append(BundleEntry(item, source, None))

    out = tmp_path / "bundle.pdf"
    build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=entries,
        claim_folder=tmp_path,
    )
    with pymupdf.open(out) as doc:
        headed = [i for i in range(len(doc)) if "Supporting document" in doc[i].get_text("text")]
        footed = [i for i in range(len(doc)) if "permanently removed" in doc[i].get_text("text")]
    assert len(headed) > 1  # the table ran on, and every page of it is headed
    assert headed == list(range(len(headed)))  # index pages come first, in a run
    assert len(footed) == 1  # the note sits once, at the end of the summary

    flat = " ".join(_index_text(out).split())
    for n in range(40):
        assert f"Supplier {n} trading as a name long enough to need two lines" in flat


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


def _ink_centre(pix):
    """Centre of mass of the dark pixels in a pixmap (x, y as 0..1 fractions)."""
    sx = sy = n = 0
    for y in range(pix.height):
        for x in range(pix.width):
            if sum(pix.pixel(x, y)[:3]) < 384:  # noticeably darker than white
                sx += x
                sy += y
                n += 1
    assert n, "expected some ink on the page"
    return sx / n / pix.width, sy / n / pix.height


def test_bundle_rotation_matches_web_preview(statement_pdf, tmp_path):
    """A rotated receipt page turns the same way in the bundle as in the browser."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((20, 30), "MARK", fontsize=24)  # ink in the top-left corner
    receipt = tmp_path / "receipt.pdf"
    doc.save(receipt)
    doc.close()

    source = Source(path=receipt.name, page_count=1, rotations={"1": 90})
    item = ClaimItem(source=source.path, first_page=1, last_page=1, amount="1.00")
    out = tmp_path / "bundle.pdf"
    build_bundle(
        out,
        redacted_statement=statement_pdf,
        entries=[BundleEntry(item, source, None)],
        claim_folder=tmp_path,
        include_index=False,
    )

    web = pymupdf.open(receipt)
    web[0].set_rotation((web[0].rotation + source.rotation_of(1)) % 360)
    web_x, web_y = _ink_centre(web[0].get_pixmap(dpi=40))
    web.close()

    bundle = pymupdf.open(out)
    bundle_x, bundle_y = _ink_centre(bundle[len(bundle) - 1].get_pixmap(dpi=40))
    bundle.close()

    # The bundle must turn the page the same way as the browser preview, so the
    # mark lands in the same half of the page for both renderers.
    assert (web_x < 0.5) == (bundle_x < 0.5)
    assert (web_y < 0.5) == (bundle_y < 0.5)
