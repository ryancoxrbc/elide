"""Turn the certified statement's text layer into addressable table rows.

The bank renders a clean grid: rows sit on a uniform pitch and the amount
columns are right-aligned under their headers.  Column positions differ between
the Transaction Account pages and the Credit Card pages, so bands are derived
from the header row of each page rather than hardcoded.
"""

from __future__ import annotations

import re

import pymupdf

from .models import (
    AMOUNT_COLUMNS,
    DATE_RE,
    GLUED_AMOUNT_RE,
    NUMBER_RE,
    Cell,
    Rect,
    StatementPage,
    StatementRow,
    Word,
)

# Words whose baselines are within this many points belong to the same row.
ROW_TOLERANCE = 2.5

# An "R" and its number are one amount if they are no further apart than this.
AMOUNT_GAP = 8.0

HEADER_LABELS = ("Date", "Description", "Debit", "Credit", "Balance")

ACCOUNT_RE = re.compile(r"Account type:\s*(.+?)\s*(?:Account number|$)", re.I | re.M)


def _words(page: pymupdf.Page) -> list[Word]:
    out = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if text.strip():
            out.append(Word(text.strip(), Rect(x0, y0, x1, y1)))
    return out


def _group_rows(words: list[Word]) -> list[list[Word]]:
    """Cluster words into visual rows by their top edge."""
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.rect.y0, w.rect.x0)):
        if rows and abs(word.rect.y0 - rows[-1][0].rect.y0) <= ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(r, key=lambda w: w.rect.x0) for r in rows]


def _find_header(rows: list[list[Word]]) -> tuple[int, dict[str, Rect]] | None:
    """Locate the column-header row and the bounds of each label.

    Page 1's summary table uses ``Account Type / Account Number / Balance``; the
    transaction pages use the five-column header.  Both are recognised, since
    both carry a Balance column we need to redact.
    """
    for index, row in enumerate(rows):
        texts = [w.text for w in row]
        header: dict[str, Rect] = {}
        for label in HEADER_LABELS:
            for word in row:
                if word.text == label:
                    header[label.lower()] = word.rect
                    break
        if "balance" in header and ("debit" in header or "account" in " ".join(texts).lower()):
            if "debit" in header and "credit" in header:
                return index, header
            # Page-1 summary table: only a Balance column matters.
            if "date" not in header:
                return index, header
    return None


def _account_name(page: pymupdf.Page) -> str:
    match = ACCOUNT_RE.search(page.get_text("text"))
    return match.group(1).strip() if match else ""


def _amount_cells(row: list[Word]) -> tuple[list[Cell], set[int]]:
    """Extract currency values from a row, and the indices of words consumed."""
    cells: list[Cell] = []
    used: set[int] = set()
    i = 0
    while i < len(row):
        word = row[i]
        glued = GLUED_AMOUNT_RE.match(word.text)
        if glued:
            cells.append(Cell([word]))
            used.add(i)
            i += 1
            continue
        if word.text.upper() == "R" and i + 1 < len(row):
            nxt = row[i + 1]
            if NUMBER_RE.match(nxt.text) and nxt.rect.x0 - word.rect.x1 <= AMOUNT_GAP:
                cells.append(Cell([word, nxt]))
                used.update({i, i + 1})
                i += 2
                continue
        i += 1
    return cells, used


def _assign_columns(cells: list[Cell], header: dict[str, Rect]) -> dict[str, Cell]:
    """Match each amount to a column by nearest header centre.

    Right-alignment means an amount sits slightly right of its header, but the
    columns are far enough apart (>45pt) that nearest-centre is unambiguous.
    Where two amounts would land in one column, fall back to left-to-right order
    so a row is never silently mis-attributed.
    """
    available = [c for c in AMOUNT_COLUMNS if c in header]
    if not available or not cells:
        return {}

    assigned: dict[str, Cell] = {}
    collision = False
    for cell in cells:
        best = min(available, key=lambda col: abs(header[col].centre_x - cell.rect.centre_x))
        if best in assigned:
            collision = True
            break
        assigned[best] = cell

    if collision:
        assigned = {}
        # Anchor from the right: the last amount on a row is always the balance.
        for col, cell in zip(reversed(available), reversed(cells)):
            assigned[col] = cell
    return assigned


def parse_statement(path: str) -> list[StatementPage]:
    """Parse every page of the statement into rows with located cells."""
    doc = pymupdf.open(path)
    pages: list[StatementPage] = []
    current_account = ""

    try:
        for page_no, page in enumerate(doc, start=1):
            words = _words(page)
            grouped = _group_rows(words)
            found = _find_header(grouped)

            account = _account_name(page)
            if account:
                current_account = account

            spage = StatementPage(
                number=page_no,
                width=page.rect.width,
                height=page.rect.height,
                account=current_account,
            )
            if not found:
                spage.furniture = [" ".join(w.text for w in r) for r in grouped]
                pages.append(spage)
                continue

            header_index, header = found
            spage.header = header
            spage.furniture = [
                " ".join(w.text for w in r) for r in grouped[: header_index + 1]
            ]

            for row_words in grouped[header_index + 1 :]:
                cells, used = _amount_cells(row_words)
                columns = _assign_columns(cells, header)
                leftover = [w for i, w in enumerate(row_words) if i not in used]

                date_cell = None
                if leftover and DATE_RE.match(leftover[0].text):
                    date_cell = Cell([leftover[0]])
                    leftover = leftover[1:]

                # Rows with no date and no amount are page furniture (the bank's
                # address block, the FSP registration footer) - skip them.
                if date_cell is None and not columns:
                    spage.furniture.append(" ".join(w.text for w in row_words))
                    continue

                row = StatementRow(
                    page=page_no,
                    account=current_account,
                    rect=Rect.union_all([w.rect for w in row_words]),
                    date=date_cell,
                    description=Cell(leftover) if leftover else None,
                    debit=columns.get("debit"),
                    credit=columns.get("credit"),
                    balance=columns.get("balance"),
                )
                spage.rows.append(row)

            _measure_table(spage, header)
            pages.append(spage)
    finally:
        doc.close()

    return pages


def _measure_table(spage: StatementPage, header: dict[str, Rect]) -> None:
    """Record the horizontal extent of the table so redaction bars line up."""
    lefts = [header[k].x0 for k in header]
    rights = [header[k].x1 for k in header]
    for row in spage.rows:
        for word in row.all_words():
            lefts.append(word.rect.x0)
            rights.append(word.rect.x1)
    if lefts:
        spage.table_x0 = min(lefts)
        spage.table_x1 = max(rights)


def transaction_rows(pages: list[StatementPage]) -> list[tuple[int, int, StatementRow]]:
    """Every dated row, keyed by (page number, row index)."""
    out = []
    for spage in pages:
        for index, row in enumerate(spage.rows):
            if row.is_transaction:
                out.append((spage.number, index, row))
    return out
