"""Assemble the final single-PDF claim bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from .models import ClaimItem, Match, Source, format_amount

A4 = pymupdf.paper_rect("a4")
MARGIN = 36.0

INK = (0.08, 0.09, 0.11)
MUTED = (0.42, 0.45, 0.5)
RULE = (0.80, 0.82, 0.85)


@dataclass
class BundleEntry:
    item: ClaimItem
    source: Source
    match: Match | None


def build_bundle(
    dest: str | Path,
    *,
    redacted_statement: str | Path,
    entries: list[BundleEntry],
    claim_folder: str | Path,
    statement_pages: list[int] | None = None,
    include_index: bool = True,
) -> dict:
    """Index page, then the redacted statement, then each receipt.

    The statement is inserted verbatim - the certified document is never
    re-rendered.  Receipts are scaled onto A4 portrait so the bundle reads as
    one document instead of a pile of mismatched page sizes.
    """
    out = pymupdf.open()
    toc: list[list] = []
    summary = {"index_pages": 0, "statement_pages": 0, "receipt_pages": 0}

    if include_index:
        pages = _draw_index(out, entries, Path(claim_folder).name)
        summary["index_pages"] = pages
        toc.append([1, "Claim summary", 1])

    statement_start = len(out) + 1
    with pymupdf.open(redacted_statement) as src:
        if statement_pages is None:
            out.insert_pdf(src)
            summary["statement_pages"] = len(src)
        else:
            for number in statement_pages:
                out.insert_pdf(src, from_page=number - 1, to_page=number - 1)
            summary["statement_pages"] = len(statement_pages)
    if summary["statement_pages"]:
        toc.append([1, "Certified bank statement (redacted)", statement_start])

    for entry in entries:
        start = len(out) + 1
        added = _place_item(out, entry.item, entry.source, Path(claim_folder))
        if added:
            summary["receipt_pages"] += added
            label = entry.item.label or Path(entry.item.source).stem
            amount = entry.item.value
            title = f"{label} - {format_amount(amount)}" if amount else label
            toc.append([1, title, start])

    if toc:
        out.set_toc(toc)
    out.set_metadata(
        {
            "title": f"Claim bundle - {Path(claim_folder).name}",
            "producer": "claims_processor",
        }
    )
    out.save(str(dest), garbage=4, deflate=True)
    out.close()
    return summary


def _place_item(out: pymupdf.Document, item: ClaimItem, source: Source, root: Path) -> int:
    """Scale a claim item's own page range onto A4 portrait.

    Rotation is looked up per page - a scanned batch routinely has some pages
    sideways and others not, and two claim items can share a page with
    different rotation needs only if the underlying scan was itself rotated
    consistently, which is the common case this still handles correctly.
    """
    path = root / item.source
    if not path.exists():
        return 0

    added = 0
    with pymupdf.open(path) as src:
        for page_number in item.pages:
            index = page_number - 1
            if not 0 <= index < len(src):
                continue
            rotation = source.rotation_of(page_number)

            page_rect = src[index].rect
            width, height = page_rect.width, page_rect.height
            if rotation % 180 == 90:
                width, height = height, width

            page = out.new_page(width=A4.width, height=A4.height)
            frame = pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, A4.height - MARGIN)
            scale = min(frame.width / width, frame.height / height, 1.0)
            draw_w, draw_h = width * scale, height * scale
            target = pymupdf.Rect(
                frame.x0 + (frame.width - draw_w) / 2,
                frame.y0 + (frame.height - draw_h) / 2,
                frame.x0 + (frame.width - draw_w) / 2 + draw_w,
                frame.y0 + (frame.height - draw_h) / 2 + draw_h,
            )
            page.show_pdf_page(target, src, index, rotate=rotation)
            added += 1
    return added


def _draw_index(out: pymupdf.Document, entries: list[BundleEntry], claim_name: str) -> int:
    """A cover page tabulating what is being claimed and where it was found."""
    page = out.new_page(width=A4.width, height=A4.height)
    x0, x1 = MARGIN + 12, A4.width - MARGIN - 12
    y = MARGIN + 30

    page.insert_text((x0, y), "Claim summary", fontname="helv", fontsize=20, color=INK)
    y += 18
    page.insert_text(
        (x0, y),
        f"{claim_name}   \u00b7   prepared {date.today().isoformat()}",
        fontname="helv",
        fontsize=9,
        color=MUTED,
    )
    y += 26

    total = sum((e.item.value or 0) for e in entries)
    page.insert_text(
        (x0, y), f"Total claimed: {format_amount(total)}", fontname="hebo", fontsize=12, color=INK
    )
    y += 22

    columns = [
        ("Amount", x0, 82),
        ("Supporting document", x0 + 88, 190),
        ("Matched statement line", x0 + 284, 175),
    ]
    page.draw_line((x0, y - 10), (x1, y - 10), color=RULE, width=0.8)
    for title, cx, _ in columns:
        page.insert_text((cx, y), title, fontname="hebo", fontsize=8.5, color=MUTED)
    y += 6
    page.draw_line((x0, y), (x1, y), color=RULE, width=0.8)
    y += 16

    for entry in entries:
        item, match = entry.item, entry.match
        if y > A4.height - MARGIN - 60:
            page = out.new_page(width=A4.width, height=A4.height)
            y = MARGIN + 30

        amount = format_amount(item.value) if item.value else "-"
        page.insert_text((columns[0][1], y), amount, fontname="hebo", fontsize=9.5, color=INK)

        label = item.label or Path(item.source).stem
        # Multi-item sources repeat the filename across several rows, so the
        # page range is what tells them apart.
        doc_line = f"{Path(item.source).name}  ({item.page_label})"
        page.insert_textbox(
            pymupdf.Rect(columns[1][1], y - 9, columns[1][1] + columns[1][2], y + 22),
            f"{label}\n{doc_line}",
            fontname="helv",
            fontsize=8.5,
            color=INK,
        )

        if match and match.confirmed and not match.not_found:
            detail = f"{match.date}  {match.description}\np.{match.page} \u00b7 {match.account}"
            colour = INK
        elif match and match.not_found:
            detail = "Not found on statement"
            colour = MUTED
        else:
            detail = "Not matched"
            colour = MUTED
        page.insert_textbox(
            pymupdf.Rect(columns[2][1], y - 9, columns[2][1] + columns[2][2], y + 22),
            detail,
            fontname="helv",
            fontsize=8.5,
            color=colour,
        )

        y += 32
        page.draw_line((x0, y - 10), (x1, y - 10), color=RULE, width=0.4)

    page.insert_textbox(
        pymupdf.Rect(x0, A4.height - MARGIN - 40, x1, A4.height - MARGIN),
        "Transactions unrelated to this claim have been permanently removed from the "
        "statement pages that follow, along with all account balances.",
        fontname="helv",
        fontsize=7.5,
        color=MUTED,
    )
    return len(out)
