"""By default, every page of a source belongs to exactly one receipt.

The claim items of a source divide its pages up between them rather than
overlapping, because an accidental overlap is the common mistake and a page
holding two receipts is the rare case. ``allocate_pages`` is what enforces
that, and the browser mirrors it as you type.

Pinning a row is the way to say the rare case out loud: its range is then taken
exactly as typed, so two till slips scanned onto one sheet can both claim it.
"""

from __future__ import annotations

from claims_processor.models import ClaimItem, allocate_pages


def lay_out(ranges, page_count: int) -> list[tuple[int, int]]:
    """Lay out ``(first, last)`` ranges, or ``(first, last, pinned)`` triples."""
    items = [
        ClaimItem(source="scan.pdf", first_page=r[0], last_page=r[1],
                  pinned=bool(r[2]) if len(r) > 2 else False)
        for r in ranges
    ]
    return [(i.first_page, i.last_page) for i in allocate_pages(items, page_count)]


def test_splitting_a_two_page_pdf_gives_each_receipt_one_page():
    """Adding a second receipt to a two-page scan: p.1 and p.2, not both twice."""
    assert lay_out([(1, 2), (1, 2)], 2) == [(1, 1), (2, 2)]


def test_a_later_receipt_is_pushed_past_the_one_before_it():
    assert lay_out([(1, 3), (2, 4)], 4) == [(1, 3), (4, 4)]


def test_an_earlier_receipt_shrinks_when_the_later_one_has_nowhere_to_go():
    """Page 3 is claimed by both; the first gives it up rather than sharing."""
    assert lay_out([(1, 3), (3, 3)], 3) == [(1, 2), (3, 3)]


def test_every_receipt_keeps_a_page_of_its_own():
    assert lay_out([(1, 5), (1, 5), (1, 5)], 5) == [(1, 3), (4, 4), (5, 5)]


def test_rows_come_back_in_page_order():
    """The receipts screen lists them in the order the pages run."""
    assert lay_out([(3, 3), (1, 2)], 3) == [(1, 2), (3, 3)]


def test_a_page_claimed_by_nobody_stays_unclaimed():
    """A blank back page belongs to no receipt and simply stays out."""
    assert lay_out([(1, 1), (3, 3)], 3) == [(1, 1), (3, 3)]


def test_a_range_is_clamped_to_the_document():
    assert lay_out([(1, 99)], 4) == [(1, 4)]


def test_more_receipts_than_pages_keeps_them_all():
    """Not satisfiable, but nothing is silently dropped."""
    assert lay_out([(1, 1), (1, 1)], 1) == [(1, 1), (1, 1)]


def test_no_two_receipts_share_a_page_whatever_is_thrown_at_it():
    awkward = [(4, 9), (1, 1), (2, 7), (1, 9), (6, 6)]
    laid_out = lay_out(awkward, 9)
    claimed: set[int] = set()
    for first, last in laid_out:
        pages = set(range(first, last + 1))
        assert not pages & claimed, f"{laid_out} shares a page"
        claimed |= pages


# ---- setting a range by hand ------------------------------------------------
# Two till slips scanned onto one sheet are one page and two receipts. Pinning
# a row is how you say so, and nothing then moves it.


def test_a_pinned_receipt_keeps_the_range_it_was_given():
    assert lay_out([(1, 1, True), (1, 1, True)], 1) == [(1, 1), (1, 1)]


def test_two_pinned_receipts_may_share_a_page():
    assert lay_out([(2, 2, True), (2, 2, True)], 3) == [(2, 2), (2, 2)]


def test_a_pinned_receipt_takes_no_part_in_the_sweep():
    """Pinning one row must not shunt the automatic ones around it."""
    assert lay_out([(1, 1, False), (2, 2, True), (2, 2, False)], 2) == [(1, 1), (2, 2), (2, 2)]


def test_automatic_receipts_still_divide_the_pages_between_themselves():
    assert lay_out([(1, 3, True), (1, 3, False), (1, 3, False)], 3) == [(1, 3), (1, 2), (3, 3)]


def test_a_pinned_range_is_still_clamped_to_the_document():
    assert lay_out([(0, 99, True)], 4) == [(1, 4)]


def test_unpinning_puts_a_receipt_back_under_the_layout():
    shared = lay_out([(1, 1, True), (1, 1, True)], 2)
    assert shared == [(1, 1), (1, 1)]
    assert lay_out([(1, 1, False), (1, 1, False)], 2) == [(1, 1), (2, 2)]


def test_items_are_updated_in_place():
    """The caller holds the same ClaimItem objects, ids and amounts intact."""
    a = ClaimItem(source="scan.pdf", first_page=1, last_page=2, amount="10.00")
    b = ClaimItem(source="scan.pdf", first_page=1, last_page=2, amount="20.00")
    allocate_pages([a, b], 2)
    assert (a.first_page, a.last_page, a.amount) == (1, 1, "10.00")
    assert (b.first_page, b.last_page, b.amount) == (2, 2, "20.00")
