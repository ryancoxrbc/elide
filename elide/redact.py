"""Physically remove unrelated text from the certified statement.

PyMuPDF's ``apply_redactions`` rewrites the page content stream and deletes the
glyphs inside each annotation rectangle, so the text is gone rather than hidden
under a box.  The page stays vector art - no rasterising - which keeps the
result a genuine digital PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .models import Rect, StatementPage, StatementRow

# Rows sit on a 16.5pt pitch with 12.3pt glyphs, leaving a ~4.2pt gutter.
# 1.5pt of padding keeps bars visually solid while leaving ~1.2pt clearance
# into the neighbouring row.
ROW_PAD_Y = 1.5
CELL_PAD_X = 2.0
CELL_PAD_Y = 1.0

BAR_COLOUR = (0, 0, 0)


class RedactionError(RuntimeError):
    """Raised when the plan would clip text that must survive."""


@dataclass
class RedactionBox:
    page: int
    rect: Rect
    reason: str
    text: str  # what sits inside, recorded so verification can hunt for leaks


@dataclass
class RedactionPlan:
    boxes: list[RedactionBox]
    removed_text: list[str]
    kept_text: list[str]
    pages_with_content: set[int]

    def for_page(self, page: int) -> list[RedactionBox]:
        return [b for b in self.boxes if b.page == page]


def build_plan(
    pages: list[StatementPage],
    kept_rows: set[tuple[int, int]],
    *,
    redact_balance_column: bool = True,
    redact_summary_balances: bool = True,
) -> RedactionPlan:
    """Decide exactly what to cover, under the strict policy.

    - An unmatched transaction row is wiped whole: date, description, amounts.
    - A matched row keeps everything except its running balance.
    - The page-1 closing balances go, while account numbers and the period stay.
    """
    boxes: list[RedactionBox] = []
    removed: list[str] = []
    kept: list[str] = []
    pages_with_content: set[int] = set()

    for spage in pages:
        # Headings, the statement period and the bank footer all survive; record
        # them so a value they share with a removed row is not read as a leak.
        kept.extend(spage.furniture)

        for index, row in enumerate(spage.rows):
            is_kept = (spage.number, index) in kept_rows

            if not row.is_transaction:
                # Page-1 summary line: cover the balance figure only.
                if redact_summary_balances and row.balance is not None:
                    boxes.append(
                        RedactionBox(
                            spage.number,
                            row.balance.rect.padded(CELL_PAD_X, CELL_PAD_Y),
                            "summary balance",
                            row.balance.text,
                        )
                    )
                    removed.append(row.balance.text)
                if row.description is not None:
                    kept.append(row.description_text)
                continue

            if is_kept:
                pages_with_content.add(spage.number)
                kept.append(f"{row.date_text} {row.description_text}")
                if row.debit:
                    kept.append(row.debit.text)
                if row.credit:
                    kept.append(row.credit.text)
                if redact_balance_column and row.balance is not None:
                    boxes.append(
                        RedactionBox(
                            spage.number,
                            _balance_band(spage, row),
                            "balance on claimed row",
                            row.balance.text,
                        )
                    )
                    removed.append(row.balance.text)
            else:
                boxes.append(
                    RedactionBox(
                        spage.number,
                        _row_band(spage, row),
                        "unrelated transaction",
                        " ".join(w.text for w in row.all_words()),
                    )
                )
                removed.extend(
                    t for t in (row.date_text, row.description_text) if t
                )
                for cell in row.amount_cells():
                    removed.append(cell.text)

    return RedactionPlan(boxes, removed, kept, pages_with_content)


def _row_band(spage: StatementPage, row: StatementRow) -> Rect:
    """Full-table-width bar so redactions line up down the page."""
    x0 = spage.table_x0 if spage.table_x0 is not None else row.rect.x0
    x1 = spage.table_x1 if spage.table_x1 is not None else row.rect.x1
    return Rect(
        min(x0, row.rect.x0) - CELL_PAD_X,
        row.rect.y0 - ROW_PAD_Y,
        max(x1, row.rect.x1) + CELL_PAD_X,
        row.rect.y1 + ROW_PAD_Y,
    )


def _balance_band(spage: StatementPage, row: StatementRow) -> Rect:
    """Cover the balance cell, widened to the balance column for a tidy edge."""
    cell = row.balance.rect
    header = spage.header.get("balance")
    x0 = min(cell.x0, header.x0) if header else cell.x0
    x1 = max(cell.x1, header.x1) if header else cell.x1
    return Rect(x0 - CELL_PAD_X, cell.y0 - CELL_PAD_Y, x1 + CELL_PAD_X, cell.y1 + CELL_PAD_Y)


def check_plan(pages: list[StatementPage], plan: RedactionPlan, kept_rows: set[tuple[int, int]]) -> None:
    """Refuse to proceed if the geometry is wrong.

    Two failure modes matter and both are caught before anything is written:
    a bar bleeding into a line we must keep, and a bar failing to cover text we
    promised to remove.
    """
    by_page: dict[int, list[RedactionBox]] = {}
    for box in plan.boxes:
        by_page.setdefault(box.page, []).append(box)

    problems: list[str] = []

    for spage in pages:
        boxes = by_page.get(spage.number, [])
        for index, row in enumerate(spage.rows):
            is_kept_row = (spage.number, index) in kept_rows

            for word in row.all_words():
                covered = any(box.rect.intersects(word.rect) for box in boxes)

                if is_kept_row or not row.is_transaction:
                    # The balance cell of a kept row is meant to be covered.
                    is_balance = row.balance is not None and word in row.balance.words
                    if covered and not is_balance:
                        problems.append(
                            f"page {spage.number}: bar would clip kept text "
                            f"{word.text!r} on row {index}"
                        )
                elif not covered:
                    problems.append(
                        f"page {spage.number}: {word.text!r} on unrelated row "
                        f"{index} is not covered"
                    )

    if problems:
        raise RedactionError(
            "Redaction geometry check failed:\n  " + "\n  ".join(problems[:20])
        )


def apply_plan(source: str | Path, dest: str | Path, plan: RedactionPlan) -> None:
    """Burn the plan into a new PDF and drop the leftovers."""
    doc = pymupdf.open(source)
    try:
        for page_no, page in enumerate(doc, start=1):
            for box in plan.for_page(page_no):
                page.add_redact_annot(pymupdf.Rect(*box.rect.as_tuple()), fill=BAR_COLOUR)
            if plan.for_page(page_no):
                # Keep the table rules and the bank logo; only text is removed.
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                    text=pymupdf.PDF_REDACT_TEXT_REMOVE,
                )

        doc.set_metadata({})
        doc.del_xml_metadata()
        doc.save(str(dest), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
