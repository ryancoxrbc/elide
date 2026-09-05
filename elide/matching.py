"""Find the statement lines that correspond to the claimed receipt amounts."""

from __future__ import annotations

import re
from decimal import Decimal

import pymupdf

from .models import Candidate, parse_amount
from .models import StatementPage

# Currency-looking runs in a receipt's text layer, used only to offer
# one-click suggestions - the user always confirms the figure themselves.
RECEIPT_AMOUNT_RE = re.compile(r"R\s?\d[\d,. ]*[.,]\d{2}")

# Lines that usually carry the payable figure, ranked before other amounts.
TOTAL_HINTS = ("total paid", "balance due", "total incl", "total (zar)", "amount", "total")


def find_candidates(pages: list[StatementPage], amount: Decimal) -> list[Candidate]:
    """Every dated row that moved exactly ``amount``, in document order."""
    out: list[Candidate] = []
    for spage in pages:
        for index, row in enumerate(spage.rows):
            if not row.is_transaction:
                continue
            column = row.matches_amount(amount)
            if column is None:
                continue
            cell = getattr(row, column)
            out.append(
                Candidate(
                    page=spage.number,
                    row_index=index,
                    column=column,
                    date=row.date_text,
                    description=row.description_text,
                    account=row.account,
                    amount=cell.text,
                )
            )
    return out


def suggest_amounts(pdf_path: str, limit: int = 8, pages: list[int] | None = None) -> list[str]:
    """Amounts detected in a receipt, best guess for the total first.

    ``pages`` (1-based) restricts the scan to a claim item's own page range, so
    a document holding several receipts offers each one only its own figures.
    Returns display strings; a scanned receipt with no text layer yields none
    and the amount is simply typed.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:
        return []

    scored: dict[Decimal, int] = {}
    try:
        wanted = doc if pages is None else (doc[p - 1] for p in pages if 1 <= p <= len(doc))
        for page in wanted:
            for line in page.get_text("text").splitlines():
                lowered = line.lower()
                rank = next(
                    (len(TOTAL_HINTS) - i for i, h in enumerate(TOTAL_HINTS) if h in lowered),
                    0,
                )
                for raw in RECEIPT_AMOUNT_RE.findall(line):
                    value = parse_amount(raw)
                    if value is None or value == 0:
                        continue
                    scored[value] = max(scored.get(value, 0), rank)
    finally:
        doc.close()

    # Hinted amounts first, then largest - the total is rarely a small figure.
    ordered = sorted(scored.items(), key=lambda kv: (-kv[1], -kv[0]))
    return [f"{value:,.2f}" for value, _ in ordered[:limit]]

