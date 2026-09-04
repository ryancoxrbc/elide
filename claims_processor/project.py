"""Persistent per-claim state.

State lives in ``claim_project.json`` inside the claim folder the user selects,
so a claim is self-contained and the run is resumable.  Everything the build
*generates* - the redacted statement, the bundle, the report - goes into a
``claim_output/`` subfolder instead, so reopening the claim folder never
mistakes a previous run's output for a source document.  The code directory
never accumulates claim data.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from .models import ClaimItem, Match, Source, allocate_pages

PROJECT_FILE = "claim_project.json"
OUTPUT_DIR = "claim_output"  # where every generated file is written


@dataclass
class ClaimProject:
    folder: str
    statement: str = ""  # relative path of the certified statement
    sources: list[Source] = field(default_factory=list)
    items: list[ClaimItem] = field(default_factory=list)
    matches: dict[str, Match] = field(default_factory=dict)  # keyed by ClaimItem.key
    keep_empty_pages: bool = True
    redact_balance_column: bool = True
    redact_summary_balances: bool = True
    include_index_page: bool = True
    build_report: str = ""
    output_name: str = ""

    # ---------------------------------------------------------------- paths

    @property
    def root(self) -> Path:
        return Path(self.folder)

    def abs_path(self, relative: str) -> Path:
        return self.root / relative

    @property
    def statement_path(self) -> Path | None:
        return self.abs_path(self.statement) if self.statement else None

    @property
    def output_root(self) -> Path:
        return self.root / OUTPUT_DIR

    def ensure_output_dir(self) -> Path:
        """Create ``claim_output/`` and return it. Called before a build writes."""
        self.output_root.mkdir(exist_ok=True)
        return self.output_root

    @property
    def redacted_path(self) -> Path:
        stem = Path(self.statement).stem if self.statement else "Statement"
        return self.output_root / f"{stem}_redacted.pdf"

    @property
    def bundle_path(self) -> Path:
        name = self.output_name or f"Claim_Bundle_{self.root.name}.pdf"
        return self.output_root / name

    @property
    def report_path(self) -> Path:
        return self.output_root / "redaction_report.txt"

    # -------------------------------------------------------------- sources

    def source(self, relative: str) -> Source | None:
        return next((s for s in self.sources if s.path == relative), None)

    def included_sources(self) -> list[Source]:
        return [s for s in self.sources if s.include]

    # --------------------------------------------------------- claim items

    def items_for(self, source_path: str) -> list[ClaimItem]:
        return [i for i in self.items if i.source == source_path]

    def item(self, key: str) -> ClaimItem | None:
        return next((i for i in self.items if i.key == key), None)

    def included_items(self) -> list[ClaimItem]:
        included = {s.path for s in self.included_sources()}
        return [i for i in self.items if i.source in included]

    def claimed_items(self) -> list[ClaimItem]:
        """Claim items that carry a usable amount."""
        return [i for i in self.included_items() if i.value is not None]

    def reallocate_pages(self) -> None:
        """Re-lay every source's items over its pages, so none overlap.

        Applied on load as well as on save: a project written before receipts
        owned whole pages can hold two items on one page, which would otherwise
        put that page into the bundle twice.
        """
        ordered: list[ClaimItem] = []
        for src in self.sources:
            ordered.extend(allocate_pages(self.items_for(src.path), src.page_count))
        known = {s.path for s in self.sources}
        # Items whose source vanished keep their place rather than disappearing
        # here; step 1 is what prunes them.
        self.items = ordered + [i for i in self.items if i.source not in known]

    def ensure_default_item(self, src: Source) -> None:
        """Give every source at least one claim item spanning all its pages.

        Called when step 2 is first shown for a source, so the common case -
        one PDF, one receipt - needs no extra clicks. Splitting or trimming the
        range is opt-in from there.
        """
        if self.items_for(src.path):
            return
        self.items.append(ClaimItem(source=src.path, first_page=1, last_page=max(src.page_count, 1)))

    def confirmed_matches(self) -> list[Match]:
        return [m for m in self.matches.values() if m.confirmed and not m.not_found]

    def kept_rows(self) -> set[tuple[int, int]]:
        """(page, row index) pairs that must survive redaction."""
        return {(m.page, m.row_index) for m in self.confirmed_matches()}

    # ------------------------------------------------------- serialisation

    def save(self) -> None:
        payload = {
            "statement": self.statement,
            "sources": [asdict(s) for s in self.sources],
            "items": [asdict(i) for i in self.items],
            "matches": {k: asdict(v) for k, v in self.matches.items()},
            "keep_empty_pages": self.keep_empty_pages,
            "redact_balance_column": self.redact_balance_column,
            "redact_summary_balances": self.redact_summary_balances,
            "include_index_page": self.include_index_page,
            "build_report": self.build_report,
            "output_name": self.output_name,
        }
        path = self.root / PROJECT_FILE
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, folder: str | Path) -> "ClaimProject":
        root = Path(folder).expanduser().resolve()
        project = cls(folder=str(root))
        path = root / PROJECT_FILE
        if not path.exists():
            return project

        data = json.loads(path.read_text(encoding="utf-8"))
        project.statement = data.get("statement", "")
        project.keep_empty_pages = data.get("keep_empty_pages", True)
        project.redact_balance_column = data.get("redact_balance_column", True)
        project.redact_summary_balances = data.get("redact_summary_balances", True)
        project.include_index_page = data.get("include_index_page", True)
        project.build_report = data.get("build_report", "")
        project.output_name = data.get("output_name", "")

        if "receipts" in data and "sources" not in data:
            _migrate_legacy_receipts(project, root, data)
        else:
            project.sources = [Source(**s) for s in data.get("sources", [])]
            project.items = [ClaimItem(**i) for i in data.get("items", [])]
            project.matches = {k: Match(**v) for k, v in data.get("matches", {}).items()}
            project.reallocate_pages()

        return project


def _migrate_legacy_receipts(project: "ClaimProject", root: Path, data: dict) -> None:
    """Upgrade a claim_project.json from before receipts could span pages.

    Each old receipt becomes one Source plus one ClaimItem covering every page
    it had, so a project already in progress keeps working exactly as it did -
    splitting a document into several claims is then opt-in from step 2.
    """
    for entry in data.get("receipts", []):
        path = entry.get("path", "")
        rotation = int(entry.get("rotation", 0) or 0)
        try:
            with pymupdf.open(root / path) as doc:
                page_count = len(doc)
        except Exception:
            page_count = 1

        rotations = {str(p): rotation for p in range(1, page_count + 1)} if rotation else {}
        project.sources.append(
            Source(
                path=path,
                include=entry.get("include", True),
                page_count=page_count,
                rotations=rotations,
            )
        )
        item = ClaimItem(
            source=path,
            first_page=1,
            last_page=max(page_count, 1),
            label=entry.get("label", ""),
            amount=entry.get("amount", ""),
            note=entry.get("note", ""),
        )
        project.items.append(item)

        old_match = data.get("matches", {}).get(path)
        if old_match:
            fields = {k: v for k, v in old_match.items() if k != "receipt_path"}
            project.matches[item.key] = Match(item_key=item.key, **fields)


def discover_pdfs(folder: str | Path) -> list[str]:
    """Source PDFs in the claim folder.

    Generated files now live in ``claim_output/``, which this skips for free by
    only looking at files in the top level. The name check is kept as a fallback
    for claim folders written by an older version, when the redacted statement
    and the bundle still sat alongside the sources.
    """
    root = Path(folder)
    generated = {"_redacted.pdf"}
    out = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        name = path.name
        if name.startswith("Claim_Bundle") or any(name.endswith(g) for g in generated):
            continue
        out.append(name)
    return out


def guess_statement(folder: str | Path, names: list[str]) -> str:
    """Pick the most likely certified statement.

    Prefers an obvious filename, then falls back to the PDF with the most pages
    carrying a text layer - a 3-month statement is always the longest document
    in a claim folder.
    """
    import pymupdf

    for name in names:
        lowered = name.lower()
        if "statement" in lowered or "certified" in lowered:
            return name

    # Otherwise score on what a bank statement actually contains. Page count
    # alone picks whichever invoice happens to be longest.
    markers = (
        "account holder",
        "account number",
        "account type",
        "opening balance",
        "closing balance",
        "statement",
        "debit",
        "credit",
        "balance",
    )
    best, best_score = "", 0
    for name in names:
        try:
            doc = pymupdf.open(Path(folder) / name)
        except Exception:
            continue
        try:
            text = " ".join(
                doc[i].get_text("text") for i in range(min(2, len(doc)))
            ).lower()
            if not text.strip():
                continue
            score = sum(4 for m in markers if m in text) + min(len(doc), 20)
            if score > best_score:
                best, best_score = name, score
        finally:
            doc.close()
    return best
