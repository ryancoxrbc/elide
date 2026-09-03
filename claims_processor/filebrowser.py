"""Folder picking: an in-page browser plus the system's native dialog.

The server is local, so it can both walk the filesystem for the browser panel
and pop a real GTK folder chooser on the user's own desktop session.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .project import OUTPUT_DIR, PROJECT_FILE

# Probing every subfolder for PDFs costs a stat per entry, which is slow on a
# network mount like pCloudDrive. Cap it - the badge is a convenience, not a
# guarantee, and folders past the cap simply show no count.
PDF_PROBE_LIMIT = 60

RECENT_FILE = Path.home() / ".config" / "claims_processor" / "recent.json"
RECENT_MAX = 8

# Dialog tools in order of preference. zenity and qarma are GTK/Qt equivalents.
NATIVE_PICKERS = ("zenity", "qarma", "kdialog", "yad")


def _is_source_pdf(name: str) -> bool:
    """A PDF the user brought, not one this tool generated."""
    if not name.lower().endswith(".pdf"):
        return False
    return not (name.startswith("Claim_Bundle") or name.endswith("_redacted.pdf"))


def _count_pdfs(path: Path) -> int | None:
    try:
        return sum(1 for e in os.scandir(path) if e.is_file() and _is_source_pdf(e.name))
    except (PermissionError, OSError):
        return None


def list_dir(path: str | Path) -> dict:
    """Describe a folder: its crumbs, its subfolders, and what it holds."""
    target = Path(path).expanduser()
    try:
        target = target.resolve(strict=True)
    except (OSError, RuntimeError):
        return {"error": f"{path} is not reachable.", "path": str(path), "entries": []}

    if not target.is_dir():
        return {"error": f"{target} is not a folder.", "path": str(target), "entries": []}

    # Hide our own output subfolder when browsing inside a claim folder, so it
    # is not offered as somewhere to start a claim.
    hidden = {OUTPUT_DIR} if (target / PROJECT_FILE).exists() else set()
    try:
        children = sorted(
            (
                e
                for e in os.scandir(target)
                if e.is_dir() and not e.name.startswith(".") and e.name not in hidden
            ),
            key=lambda e: e.name.lower(),
        )
    except PermissionError:
        return {
            "error": f"No permission to read {target}.",
            "path": str(target),
            "entries": [],
        }

    entries = []
    for index, entry in enumerate(children):
        child = Path(entry.path)
        entries.append(
            {
                "name": entry.name,
                "path": str(child),
                "pdfs": _count_pdfs(child) if index < PDF_PROBE_LIMIT else None,
                "has_project": (child / "claim_project.json").exists(),
            }
        )

    crumbs = [{"name": p.name or str(p), "path": str(p)} for p in reversed(target.parents)]
    crumbs.append({"name": target.name or str(target), "path": str(target)})

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "crumbs": crumbs,
        "entries": entries,
        "pdfs_here": _count_pdfs(target) or 0,
        "has_project": (target / "claim_project.json").exists(),
        "error": "",
    }


def recent_folders() -> list[dict]:
    """Folders opened before, newest first, dropping any that have gone away."""
    try:
        raw = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        path = Path(str(item))
        if path.is_dir():
            out.append({"path": str(path), "name": path.name or str(path)})
    return out[:RECENT_MAX]


def remember_folder(path: str | Path) -> None:
    folder = str(Path(path).expanduser().resolve())
    existing = [r["path"] for r in recent_folders() if r["path"] != folder]
    try:
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(
            json.dumps([folder, *existing][:RECENT_MAX], indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # A missing recents list is a convenience lost, not an error.


def native_picker() -> str | None:
    """The first available system folder dialog, if any."""
    return next((tool for tool in NATIVE_PICKERS if shutil.which(tool)), None)


def pick_folder_natively(start: str | Path | None = None) -> tuple[str | None, str]:
    """Open the desktop's own folder chooser. Returns (path, error).

    ``path`` is None when the user cancelled or no dialog is installed; the
    in-page browser is always there as the fallback.
    """
    tool = native_picker()
    if tool is None:
        return None, "No system folder dialog is installed."

    start_dir = str(Path(start).expanduser()) if start else str(Path.home())
    if tool in ("zenity", "qarma", "yad"):
        command = [
            tool,
            "--file-selection",
            "--directory",
            "--title=Choose the claim folder",
            f"--filename={start_dir.rstrip('/')}/",
        ]
    else:  # kdialog
        command = [tool, "--getexistingdirectory", start_dir]

    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, "The folder dialog timed out."
    except OSError as exc:
        return None, f"Could not open the folder dialog: {exc}"

    chosen = proc.stdout.strip()
    if proc.returncode != 0 or not chosen:
        return None, ""  # cancelled - not an error worth showing
    if not Path(chosen).is_dir():
        return None, f"{chosen} is not a folder."
    return chosen, ""
