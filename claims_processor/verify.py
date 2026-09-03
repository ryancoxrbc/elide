"""Independent proof that the redaction did what it claimed.

The build fails rather than hands over a bundle that leaks.  Checks run against
the finished file, not against the plan, so a bug in the redaction step cannot
mark its own homework.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MIN_LEAK_LENGTH = 4


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


@dataclass
class VerificationResult:
    passed: bool = True
    leaks: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ocr_leaks: list[str] = field(default_factory=list)
    ocr_ran: bool = False

    def as_report(self) -> str:
        lines = ["PASSED" if self.passed else "FAILED"]
        if self.leaks:
            lines.append(f"\nText that should have been removed but survives ({len(self.leaks)}):")
            lines += [f"  - {t}" for t in self.leaks[:40]]
        if self.missing:
            lines.append(f"\nText that should have been kept but is gone ({len(self.missing)}):")
            lines += [f"  - {t}" for t in self.missing[:40]]
        if self.ocr_leaks:
            lines.append(f"\nStill legible in the rendered page ({len(self.ocr_leaks)}):")
            lines += [f"  - {t}" for t in self.ocr_leaks[:40]]
        if self.notes:
            lines.append("\nNotes:")
            lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


def extract_text(path: str | Path, pages: range | None = None) -> str:
    """All text in the file, or only in the given 1-based page range."""
    doc = pymupdf.open(path)
    try:
        wanted = range(1, len(doc) + 1) if pages is None else pages
        return _normalise(
            " ".join(doc[n - 1].get_text("text") for n in wanted if 1 <= n <= len(doc))
        )
    finally:
        doc.close()


def verify(
    path: str | Path,
    removed: list[str],
    kept: list[str],
    *,
    run_ocr: bool = False,
    pages: range | None = None,
) -> VerificationResult:
    """Check a finished PDF for leaks and for over-redaction.

    ``pages`` limits the scan to the statement's own pages. Without it, a bundle
    would be checked against strings removed from the statement, and any receipt
    that legitimately shows the same date or amount would look like a leak.
    """
    result = VerificationResult()
    haystack = extract_text(path, pages)

    kept_norm = {_normalise(k) for k in kept if len(_normalise(k)) >= MIN_LEAK_LENGTH}

    # A removed string is only a leak if nothing we deliberately kept contains
    # it - two rows can legitimately share an amount or a merchant name.
    seen: set[str] = set()
    for raw in removed:
        needle = _normalise(raw)
        if len(needle) < MIN_LEAK_LENGTH or needle in seen:
            continue
        seen.add(needle)
        if any(needle in k for k in kept_norm):
            continue
        if needle in haystack:
            result.leaks.append(raw)

    for raw in kept:
        needle = _normalise(raw)
        if len(needle) < MIN_LEAK_LENGTH:
            continue
        if needle not in haystack:
            result.missing.append(raw)

    _check_content_streams(path, seen, kept_norm, result, pages)
    _check_extras(path, result)

    if run_ocr:
        _check_ocr(path, seen, kept_norm, result, pages)

    result.passed = not (result.leaks or result.missing or result.ocr_leaks)
    return result


def _check_content_streams(
    path: str | Path,
    needles: set[str],
    kept: set[str],
    result: VerificationResult,
    pages: range | None = None,
) -> None:
    """Coarse scan of the raw page streams for literal leftovers.

    Text in a content stream is font-encoded, so this cannot see everything -
    it is a cheap backstop under the extractor, not the primary check.
    """
    doc = pymupdf.open(path)
    try:
        wanted = range(1, len(doc) + 1) if pages is None else pages
        blob = b"".join(
            doc[n - 1].read_contents() for n in wanted if 1 <= n <= len(doc)
        )
    finally:
        doc.close()

    try:
        text = _normalise(blob.decode("latin-1"))
    except Exception:
        result.notes.append("raw content streams could not be decoded for scanning")
        return

    hits = [n for n in needles if len(n) >= 8 and n in text and not any(n in k for k in kept)]
    for hit in hits:
        if hit not in result.leaks:
            result.leaks.append(f"{hit} (in raw content stream)")


def _check_extras(path: str | Path, result: VerificationResult) -> None:
    """Nothing should be smuggled out in attachments, scripts or annotations."""
    doc = pymupdf.open(path)
    try:
        if doc.embfile_count():
            result.leaks.append(f"{doc.embfile_count()} embedded file(s) present")
        annots = sum(1 for page in doc for _ in page.annots())
        if annots:
            result.leaks.append(f"{annots} annotation(s) left on the page")
        # "format" is the PDF version string, always present and not removable,
        # so it is not evidence of leftover metadata.
        carried = {
            k: v for k, v in (doc.metadata or {}).items() if v and k != "format"
        }
        if carried:
            result.notes.append(
                "metadata fields carried through: " + ", ".join(sorted(carried))
            )
    finally:
        doc.close()


def _check_ocr(
    path: str | Path,
    needles: set[str],
    kept: set[str],
    result: VerificationResult,
    pages: range | None = None,
) -> None:
    """Render each page and read it back, catching anything still visible.

    This is the check that would catch text baked into a background image,
    which glyph removal cannot touch.
    """
    if not shutil.which("tesseract"):
        result.notes.append("tesseract not installed - OCR check skipped")
        return

    result.ocr_ran = True
    doc = pymupdf.open(path)
    try:
        wanted = range(1, len(doc) + 1) if pages is None else pages
        with tempfile.TemporaryDirectory() as tmp:
            chunks = []
            for index in wanted:
                if not 1 <= index <= len(doc):
                    continue
                image = Path(tmp) / f"page{index}.png"
                doc[index - 1].get_pixmap(dpi=200).save(image)
                proc = subprocess.run(
                    ["tesseract", str(image), "stdout"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                chunks.append(proc.stdout)
            seen_text = _normalise(" ".join(chunks))
    finally:
        doc.close()

    # OCR is noisy, so only chase distinctive strings.
    for needle in needles:
        if len(needle) >= 8 and needle in seen_text and not any(needle in k for k in kept):
            result.ocr_leaks.append(needle)
