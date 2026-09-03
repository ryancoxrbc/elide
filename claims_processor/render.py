"""Page images for the browser preview."""

from __future__ import annotations

from pathlib import Path

import pymupdf


def page_png(path: str | Path, page_number: int, dpi: int = 110, rotation: int = 0) -> bytes:
    """Render one 1-based page to PNG bytes."""
    doc = pymupdf.open(path)
    try:
        page = doc[page_number - 1]
        if rotation:
            page.set_rotation((page.rotation + rotation) % 360)
        return doc[page_number - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()


def page_count(path: str | Path) -> int:
    doc = pymupdf.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def page_size(path: str | Path, page_number: int) -> tuple[float, float]:
    doc = pymupdf.open(path)
    try:
        rect = doc[page_number - 1].rect
        return rect.width, rect.height
    finally:
        doc.close()
