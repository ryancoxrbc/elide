"""Assemble the final single-PDF claim bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from .models import ClaimItem, Match, Source, format_amount, page_label, pages_of

A4 = pymupdf.paper_rect("a4")
MARGIN = 36.0

INK = (0.08, 0.09, 0.11)
MUTED = (0.42, 0.45, 0.5)
RULE = (0.80, 0.82, 0.85)

# ---- the claim summary table ------------------------------------------------
CELL_FONT, HEAD_FONT = "helv", "hebo"
CELL_SIZE = 8.5
LINE = 11.0  # baseline pitch inside a cell
ROW_GAP = 11.0  # from a row's last baseline to the rule under it
FOOTNOTE_SPACE = 52.0  # kept clear at the foot of every index page

COL_X0 = MARGIN + 12
COL_X1 = A4.width - MARGIN - 12
# (title, left edge, usable width) - together they span the full text column.
COLUMNS = (
    ("Amount", COL_X0, 80.0),
    ("Supporting document", COL_X0 + 86, 196.0),
    ("Matched statement line", COL_X0 + 290, COL_X1 - (COL_X0 + 290)),
)

FOOTNOTE = (
    "Transactions unrelated to this claim have been permanently removed from the "
    "statement pages that follow, along with all account balances."
)


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
            title = f"{label} - {format_amount(amount)}" if amount is not None else label
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

    Rotation is looked up per page rather than per document, since a scanned
    batch routinely comes out with some pages sideways and others not. Pages
    the user marked as ignored are skipped, so a blank reverse side inside a
    receipt's range never reaches the bundle.
    """
    path = root / item.source
    if not path.exists():
        return 0

    added = 0
    with pymupdf.open(path) as src:
        for page_number in pages_of(item, source):
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
            # Stored rotations are anticlockwise (see Source.rotate_anticlockwise),
            # matching Page.set_rotation used by the browser preview. show_pdf_page's
            # rotate argument turns the other way, so negate it to keep the bundle
            # consistent with what the web app shows.
            page.show_pdf_page(target, src, index, rotate=-rotation)
            added += 1
    return added


def _text_width(text: str, fontname: str, fontsize: float) -> float:
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=fontsize)


def _split_word(word: str, width: float, fontname: str, fontsize: float) -> list[str]:
    """Break a word wider than its column - a long filename, typically."""
    chunks, current = [], ""
    for char in word:
        if current and _text_width(current + char, fontname, fontsize) > width:
            chunks.append(current)
            current = char
        else:
            current += char
    if current:
        chunks.append(current)
    return chunks


def _shorten(line: str, width: float, fontname: str, fontsize: float) -> str:
    while line and _text_width(line + "...", fontname, fontsize) > width:
        line = line[:-1]
    return line.rstrip() + "..."


def _wrap(
    text: str,
    width: float,
    *,
    fontname: str = CELL_FONT,
    fontsize: float = CELL_SIZE,
    max_lines: int = 3,
) -> list[str]:
    """Break text into lines that fit ``width``, capped at ``max_lines``.

    Cells are wrapped and drawn line by line rather than handed to
    ``insert_textbox``, which draws *nothing at all* when its text does not fit
    the rectangle: one long supplier name silently took the filename and page
    range down with it, and a long statement description took the page number
    and account with it.  Measuring here means a cell is always drawn, and the
    worst an over-long value can do is show an ellipsis.
    """
    words: list[str] = []
    for word in text.split():
        if _text_width(word, fontname, fontsize) <= width:
            words.append(word)
        else:
            words.extend(_split_word(word, width, fontname, fontsize))

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and _text_width(candidate, fontname, fontsize) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _shorten(lines[-1], width, fontname, fontsize)
    return lines or [""]


def _paragraphs(text: str, width: float, max_lines: int = 3) -> list[str]:
    """Wrap each line of a multi-line cell separately.

    Keeping the paragraphs apart is what guarantees the second line survives:
    the ``p.3 - Cheque Account`` under a long description is capped on its own
    rather than being pushed off the end of a shared budget.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap(paragraph, width, max_lines=max_lines))
    return lines


def _new_index_page(out: pymupdf.Document) -> tuple[pymupdf.Page, float]:
    page = out.new_page(width=A4.width, height=A4.height)
    return page, MARGIN + 30


def _draw_column_header(page: pymupdf.Page, y: float) -> float:
    """The ruled column titles, repeated at the top of every index page."""
    page.draw_line((COL_X0, y - 10), (COL_X1, y - 10), color=RULE, width=0.8)
    for title, x, _ in COLUMNS:
        page.insert_text((x, y), title, fontname=HEAD_FONT, fontsize=8.5, color=MUTED)
    y += 6
    page.draw_line((COL_X0, y), (COL_X1, y), color=RULE, width=0.8)
    return y + 16


def _match_cell(match: Match | None) -> tuple[str, tuple[float, float, float]]:
    """What the 'Matched statement line' column says about one claim."""
    if match and match.confirmed and not match.not_found:
        return f"{match.date}  {match.description}\np.{match.page} · {match.account}", INK
    if match and match.not_found:
        return "Not found on statement", MUTED
    return "Not matched", MUTED


def _draw_index(out: pymupdf.Document, entries: list[BundleEntry], claim_name: str) -> int:
    """A cover page tabulating what is being claimed and where it was found."""
    page, y = _new_index_page(out)

    page.insert_text((COL_X0, y), "Claim summary", fontname=CELL_FONT, fontsize=20, color=INK)
    y += 18
    page.insert_text(
        (COL_X0, y),
        f"{claim_name}   ·   prepared {date.today().isoformat()}",
        fontname=CELL_FONT,
        fontsize=9,
        color=MUTED,
    )
    y += 26

    total = sum((e.item.value or 0) for e in entries)
    page.insert_text(
        (COL_X0, y),
        f"Total claimed: {format_amount(total)}",
        fontname=HEAD_FONT,
        fontsize=12,
        color=INK,
    )
    y = _draw_column_header(page, y + 22)

    for entry in entries:
        item, match = entry.item, entry.match
        amount = format_amount(item.value) if item.value is not None else "-"
        label = item.label or Path(item.source).stem
        # Several receipts can come out of one PDF, so the filename alone does
        # not identify a row - the page range is what tells them apart.
        where = page_label(pages_of(item, entry.source))
        document = f"{label}\n{Path(item.source).name}  ({where})"
        detail, colour = _match_cell(match)

        cells = [
            ([amount], HEAD_FONT, 9.5, INK),
            (_paragraphs(document, COLUMNS[1][2]), CELL_FONT, CELL_SIZE, INK),
            (_paragraphs(detail, COLUMNS[2][2]), CELL_FONT, CELL_SIZE, colour),
        ]
        height = max(len(lines) for lines, *_ in cells) * LINE

        if y + height > A4.height - MARGIN - FOOTNOTE_SPACE:
            page, y = _new_index_page(out)
            y = _draw_column_header(page, y)

        for (lines, fontname, fontsize, ink), (_, x, _width) in zip(cells, COLUMNS):
            for index, line in enumerate(lines):
                page.insert_text(
                    (x, y + index * LINE),
                    line,
                    fontname=fontname,
                    fontsize=fontsize,
                    color=ink,
                )

        y += height + ROW_GAP
        page.draw_line((COL_X0, y - ROW_GAP), (COL_X1, y - ROW_GAP), color=RULE, width=0.4)

    for index, line in enumerate(_wrap(FOOTNOTE, COL_X1 - COL_X0, fontsize=7.5, max_lines=3)):
        page.insert_text(
            (COL_X0, A4.height - MARGIN - 28 + index * 9),
            line,
            fontname=CELL_FONT,
            fontsize=7.5,
            color=MUTED,
        )
    return len(out)
