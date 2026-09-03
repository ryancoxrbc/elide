"""Folder browsing and the native-dialog wrapper."""

from __future__ import annotations

import subprocess

import pytest

from claims_processor import filebrowser
from claims_processor.filebrowser import list_dir, pick_folder_natively


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "claim_a").mkdir()
    (tmp_path / "claim_a" / "receipt.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "claim_a" / "statement.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "claim_b").mkdir()
    (tmp_path / "claim_b" / "claim_project.json").write_text("{}")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "loose.pdf").write_bytes(b"%PDF-1.4\n")
    return tmp_path


def test_lists_subfolders_with_pdf_counts(tree):
    data = list_dir(tree)
    names = [e["name"] for e in data["entries"]]
    assert names == ["claim_a", "claim_b"]
    by_name = {e["name"]: e for e in data["entries"]}
    assert by_name["claim_a"]["pdfs"] == 2
    assert by_name["claim_b"]["pdfs"] == 0


def test_hidden_folders_are_skipped(tree):
    assert ".hidden" not in [e["name"] for e in list_dir(tree)["entries"]]


def test_a_folder_already_started_is_flagged(tree):
    by_name = {e["name"]: e for e in list_dir(tree)["entries"]}
    assert by_name["claim_b"]["has_project"] is True
    assert by_name["claim_a"]["has_project"] is False


def test_pdfs_in_the_current_folder_are_counted(tree):
    assert list_dir(tree)["pdfs_here"] == 1


def test_crumbs_walk_back_to_the_root(tree):
    crumbs = list_dir(tree)["crumbs"]
    assert crumbs[0]["path"] == "/"
    assert crumbs[-1]["path"] == str(tree.resolve())


def test_missing_folder_reports_an_error_rather_than_raising(tmp_path):
    data = list_dir(tmp_path / "nope")
    assert data["error"]
    assert data["entries"] == []


def test_a_file_is_not_browsable(tree):
    assert "not a folder" in list_dir(tree / "loose.pdf")["error"]


def test_recents_round_trip_and_drop_dead_paths(tree, tmp_path, monkeypatch):
    store = tmp_path / "recent.json"
    monkeypatch.setattr(filebrowser, "RECENT_FILE", store)

    filebrowser.remember_folder(tree / "claim_a")
    filebrowser.remember_folder(tree / "claim_b")
    assert [r["name"] for r in filebrowser.recent_folders()] == ["claim_b", "claim_a"]

    # Re-picking a folder moves it to the front rather than duplicating it.
    filebrowser.remember_folder(tree / "claim_a")
    assert [r["name"] for r in filebrowser.recent_folders()] == ["claim_a", "claim_b"]

    (tree / "claim_b").rename(tree / "gone")
    assert [r["name"] for r in filebrowser.recent_folders()] == ["claim_a"]


def test_no_dialog_installed_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(filebrowser, "native_picker", lambda: None)
    path, error = pick_folder_natively("/tmp")
    assert path is None
    assert "No system folder dialog" in error


def test_cancelling_the_dialog_is_not_an_error(monkeypatch):
    """Exit 1 with no output means the user closed the dialog."""
    monkeypatch.setattr(filebrowser, "native_picker", lambda: "zenity")
    monkeypatch.setattr(
        filebrowser.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""),
    )
    assert pick_folder_natively("/tmp") == (None, "")


def test_a_hanging_dialog_times_out_cleanly(monkeypatch):
    """A dialog that never surfaces must not block the request for ever."""
    monkeypatch.setattr(filebrowser, "native_picker", lambda: "zenity")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("zenity", 120)

    monkeypatch.setattr(filebrowser.subprocess, "run", boom)
    path, error = pick_folder_natively("/tmp")
    assert path is None
    assert "timed out" in error


def test_a_chosen_folder_comes_back(monkeypatch, tree):
    monkeypatch.setattr(filebrowser, "native_picker", lambda: "zenity")
    monkeypatch.setattr(
        filebrowser.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, f"{tree / 'claim_a'}\n", ""),
    )
    path, error = pick_folder_natively(tree)
    assert path == str(tree / "claim_a")
    assert error == ""


def test_a_returned_path_that_is_not_a_folder_is_rejected(monkeypatch, tree):
    monkeypatch.setattr(filebrowser, "native_picker", lambda: "zenity")
    monkeypatch.setattr(
        filebrowser.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, f"{tree / 'loose.pdf'}\n", ""),
    )
    path, error = pick_folder_natively(tree)
    assert path is None
    assert "not a folder" in error


def test_kdialog_gets_its_own_flags(monkeypatch, tree):
    """kdialog uses a different argument shape to zenity."""
    seen = {}
    monkeypatch.setattr(filebrowser, "native_picker", lambda: "kdialog")

    def capture(cmd, **k):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, str(tree), "")

    monkeypatch.setattr(filebrowser.subprocess, "run", capture)
    pick_folder_natively(tree)
    assert seen["cmd"][:2] == ["kdialog", "--getexistingdirectory"]


def test_generated_files_are_not_counted_in_the_badge(tmp_path):
    """A finished claim folder should still advertise its source documents."""
    folder = tmp_path / "done"
    folder.mkdir()
    for name in ("receipt.pdf", "CertifiedStatements.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4\n")
    for name in ("Claim_Bundle_done.pdf", "CertifiedStatements_redacted.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4\n")

    by_name = {e["name"]: e for e in list_dir(tmp_path)["entries"]}
    assert by_name["done"]["pdfs"] == 2
