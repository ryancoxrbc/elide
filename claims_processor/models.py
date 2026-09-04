"""Data model shared across the pipeline.

Geometry is kept in PDF points with the origin top-left, matching PyMuPDF.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# A date cell in the statement, e.g. "2026-08-26".
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The numeric half of an amount, e.g. "1,322.98" or "6,947.64-" (Rivermarch marks
# credit-card balances as owing with a trailing minus).
NUMBER_RE = re.compile(r"^\d[\d,. ]*\d\.\d{2}-?$|^\d\.\d{2}-?$")

# An amount written as one glued token, e.g. "R1,322.98".
GLUED_AMOUNT_RE = re.compile(r"^R\s?(\d[\d,. ]*\.\d{2}-?)$")

AMOUNT_COLUMNS = ("debit", "credit", "balance")


def parse_amount(text: str) -> Decimal | None:
    """Normalise any of the currency spellings we see into a positive Decimal.

    Handles the South African variants that show up across these documents:
    ``R 1 149,80`` (space thousands, comma decimal), ``R1,150.00``, ``R 228.00``
    and ``R 6,947.64-``.  Sign is dropped: the trailing minus only ever means
    "this credit-card balance is owing", never a different transaction amount.
    """
    if text is None:
        return None
    cleaned = text.strip().upper().replace("R", "", 1).strip()
    cleaned = cleaned.rstrip("-").strip()
    if not cleaned:
        return None

    # Decide which separator is the decimal point by looking at the last one.
    last_dot, last_comma = cleaned.rfind("."), cleaned.rfind(",")
    if last_comma > last_dot:
        # comma-decimal: "1 149,80"
        cleaned = cleaned.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        # dot-decimal: "1,149.80"
        cleaned = cleaned.replace(",", "").replace(" ", "")

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value >= 0 else -value


def format_amount(value: Decimal) -> str:
    return f"R {value:,.2f}"


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle in PDF points."""

    x0: float
    y0: float
    x1: float
    y1: float

    def union(self, other: "Rect") -> "Rect":
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x1 <= other.x0
            or other.x1 <= self.x0
            or self.y1 <= other.y0
            or other.y1 <= self.y0
        )

    def padded(self, dx: float = 0.0, dy: float = 0.0) -> "Rect":
        return Rect(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)

    @property
    def centre_x(self) -> float:
        return (self.x0 + self.x1) / 2

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    @staticmethod
    def union_all(rects: list["Rect"]) -> "Rect":
        out = rects[0]
        for r in rects[1:]:
            out = out.union(r)
        return out


@dataclass(frozen=True)
class Word:
    text: str
    rect: Rect


@dataclass
class Cell:
    """One logical column value: the words that make it up, plus their bounds."""

    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def rect(self) -> Rect:
        return Rect.union_all([w.rect for w in self.words])

    @property
    def value(self) -> Decimal | None:
        return parse_amount(self.text)


@dataclass
class StatementRow:
    """A single line of the statement table."""

    page: int
    account: str
    rect: Rect
    date: Cell | None = None
    description: Cell | None = None
    debit: Cell | None = None
    credit: Cell | None = None
    balance: Cell | None = None

    @property
    def is_transaction(self) -> bool:
        return self.date is not None and DATE_RE.match(self.date.text) is not None

    @property
    def date_text(self) -> str:
        return self.date.text if self.date else ""

    @property
    def description_text(self) -> str:
        return self.description.text if self.description else ""

    def amount_cells(self) -> list[Cell]:
        return [c for c in (self.debit, self.credit, self.balance) if c is not None]

    def all_words(self) -> list[Word]:
        words: list[Word] = []
        for cell in (self.date, self.description, self.debit, self.credit, self.balance):
            if cell is not None:
                words.extend(cell.words)
        return words

    def matches_amount(self, amount: Decimal) -> str | None:
        """Return 'debit' or 'credit' if this row moved exactly ``amount``."""
        for column in ("debit", "credit"):
            cell: Cell | None = getattr(self, column)
            if cell is not None and cell.value == amount:
                return column
        return None


@dataclass
class StatementPage:
    number: int  # 1-based, as printed/displayed
    width: float
    height: float
    account: str
    rows: list[StatementRow] = field(default_factory=list)
    header: dict[str, Rect] = field(default_factory=dict)
    # Text outside the table (headings, the period, the bank footer). It stays
    # on the page, so verification must not mistake it for a leak when a row
    # we removed happened to repeat one of its values - a transaction dated
    # 2026-06-03 on a statement running "From: 2026-06-03", for instance.
    furniture: list[str] = field(default_factory=list)
    table_x0: float | None = None
    table_x1: float | None = None


@dataclass
class Source:
    """A supporting PDF in the claim folder.

    A source is evidence, not a claim. Its pages enter the bundle once, in
    order; what is being claimed is described by the ClaimItems drawn over it.
    That separation is what lets one PDF hold several receipts and one receipt
    span several pages, with the items dividing the pages up between them.
    """

    path: str  # relative to the claim folder
    include: bool = True
    page_count: int = 0
    # Page number (a string, since JSON object keys are strings) to degrees
    # clockwise. Per page rather than per document: a scanner batch routinely
    # comes out with its pages in different orientations.
    rotations: dict[str, int] = field(default_factory=dict)

    def rotation_of(self, page: int) -> int:
        return int(self.rotations.get(str(page), 0)) % 360

    def rotate_anticlockwise(self, page: int, step: int = 90) -> int:
        turned = (self.rotation_of(page) - step) % 360
        if turned:
            self.rotations[str(page)] = turned
        else:
            self.rotations.pop(str(page), None)
        return turned


@dataclass
class ClaimItem:
    """One receipt: a run of pages in a source, and the amount claimed for it.

    By default a receipt owns whole pages and the items of a source divide
    those pages between them (see ``allocate_pages``), because a page holding
    two receipts is the rare case and an accidental overlap is the common
    mistake.  ``pinned`` is the deliberate exception: the range is then taken
    exactly as typed, which is what lets two till slips scanned onto one sheet
    both claim that page.

    Identity is not derived from source+range either way, because a range moves
    whenever a neighbour does; each item carries its own id, assigned once and
    never recomputed, so it survives the user editing the range or the amount.
    """

    source: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    first_page: int = 1
    last_page: int = 1
    label: str = ""
    amount: str = ""  # kept as typed; parsed on demand
    note: str = ""
    # Set by hand: keep this range as typed, overlaps included.
    pinned: bool = False

    @property
    def key(self) -> str:
        """Stable identifier, used for form fields and match lookups."""
        return self.id

    @property
    def pages(self) -> list[int]:
        return list(range(self.first_page, self.last_page + 1))

    @property
    def value(self) -> Decimal | None:
        return parse_amount(self.amount)

    @property
    def page_label(self) -> str:
        if self.first_page == self.last_page:
            return f"p.{self.first_page}"
        return f"pp.{self.first_page}-{self.last_page}"


def allocate_pages(items: list[ClaimItem], page_count: int) -> list[ClaimItem]:
    """Lay a source's claim items out over its pages, one receipt per page.

    Items are sorted by where they start and placed in that order, each
    beginning no earlier than the page after the one before it and leaving a
    page for every item still to come.  Overlaps therefore resolve by pushing
    the later item forward and, where that would run off the end, by shrinking
    the earlier one.  Gaps are allowed: a blank back page belongs to no receipt
    and simply stays out of the bundle.  The one case that cannot be honoured
    is more items than pages; the surplus piles onto the last page rather than
    being silently dropped.

    A ``pinned`` item is left exactly where it was put - clamped to the
    document, but never moved, and taking no part in the sweep.  That is the
    escape hatch for a page holding two receipts: pin them and they both keep
    it.  Automatic items lay themselves out as though the pinned ones were not
    there, so pinning one row never shunts the rest around.

    The list is returned in page order, and the items are updated in place.
    """
    ordered = sorted(items, key=lambda i: (i.first_page, i.last_page))
    bound = max(page_count, 1)
    remaining = sum(1 for i in ordered if not i.pinned)
    cursor = 1
    for item in ordered:
        if item.pinned:
            item.first_page = min(max(item.first_page, 1), bound)
            item.last_page = min(max(item.last_page, item.first_page), bound)
            continue
        # Every automatic item after this one still needs a page of its own.
        remaining -= 1
        ceiling = max(1, bound - remaining)
        first = min(max(item.first_page, cursor), ceiling)
        last = min(max(item.last_page, first), ceiling)
        item.first_page, item.last_page = first, last
        cursor = last + 1
    return ordered


@dataclass
class Match:
    """A confirmed link between a claimed amount and a statement line."""

    item_key: str  # a ClaimItem.key
    page: int
    row_index: int
    column: str  # 'debit' or 'credit'
    date: str
    description: str
    account: str
    confirmed: bool = False
    not_found: bool = False


@dataclass
class Candidate:
    """A statement row offered to the user as a possible match."""

    page: int
    row_index: int
    column: str
    date: str
    description: str
    account: str
    amount: str
