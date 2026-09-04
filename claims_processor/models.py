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
    # Pages left out of the claim: a blank back page, a separator sheet, a
    # duplicate scan. They stay in the source document but never reach the
    # bundle, and no receipt is expected to account for them.
    ignored: list[int] = field(default_factory=list)

    def rotation_of(self, page: int) -> int:
        return int(self.rotations.get(str(page), 0)) % 360

    def is_ignored(self, page: int) -> bool:
        return page in self.ignored

    def toggle_ignored(self, page: int) -> bool:
        """Flip one page in or out of the claim; returns its new state."""
        if page in self.ignored:
            self.ignored.remove(page)
            return False
        self.ignored = sorted(self.ignored + [page])
        return True

    def live_pages(self) -> list[int]:
        """Every page of this document that is not ignored."""
        return [p for p in range(1, max(self.page_count, 1) + 1) if not self.is_ignored(p)]

    def rotate_anticlockwise(self, page: int, step: int = 90) -> int:
        turned = (self.rotation_of(page) - step) % 360
        if turned:
            self.rotations[str(page)] = turned
        else:
            self.rotations.pop(str(page), None)
        return turned


@dataclass
class ClaimItem:
    """One receipt: the pages of a source it is made of, and the amount claimed.

    A receipt owns exactly the pages it was given.  They need not run
    consecutively - a two-page invoice with an unrelated slip between its
    halves is still one receipt - and two receipts may name the same page,
    which is how two till slips scanned onto one sheet each claim it.  Both are
    said deliberately, by pointing at the pages, so there is no layout rule
    here to second-guess them.

    Identity is not derived from source and pages, because the pages change
    whenever the user re-cuts the document; each item carries its own id,
    assigned once and never recomputed, so it survives an edit to its pages or
    its amount, and the match made against it stays attached.
    """

    source: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    # Page numbers within the source, 1-based, ascending and without repeats.
    pages: list[int] = field(default_factory=list)
    label: str = ""
    amount: str = ""  # kept as typed; parsed on demand
    note: str = ""

    @property
    def key(self) -> str:
        """Stable identifier, used for form fields and match lookups."""
        return self.id

    @property
    def value(self) -> Decimal | None:
        return parse_amount(self.amount)

    @property
    def page_label(self) -> str:
        return page_label(self.pages)


def page_label(pages: list[int]) -> str:
    """Name a set of pages: 'p.4', 'pp.1-2', or 'pp.1, 2 and 4' when one is skipped."""
    if not pages:
        return "no pages"
    if len(pages) == 1:
        return f"p.{pages[0]}"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"pp.{pages[0]}-{pages[-1]}"
    listed = ", ".join(str(p) for p in pages[:-1])
    return f"pp.{listed} and {pages[-1]}"


def pages_of(item: ClaimItem, source: Source) -> list[int]:
    """The pages of a claim item that actually reach the bundle.

    A page the user has since taken out of the claim - the blank reverse of a
    two-sided invoice, say - drops out here rather than having to be unpicked
    from every receipt that names it.
    """
    return [p for p in item.pages if not source.is_ignored(p)]


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
