"""Route-level coverage for splitting a source into several claim items and
rotating individual pages - the two things request-shaped form data (not the
dataclasses directly) has to get right.
"""

from __future__ import annotations

import pymupdf
import pytest

from claims_processor.app import STATE, app
from claims_processor.project import ClaimProject


@pytest.fixture
def client():
    return app.test_client()


@pytest.fixture
def claim_folder(tmp_path):
    """A statement plus one two-page 'scan' holding two unrelated receipts."""
    stmt = pymupdf.open()
    stmt.new_page(width=595, height=842).insert_text((60, 100), "Account holder: Test")
    stmt.save(tmp_path / "CertifiedStatements.pdf")
    stmt.close()

    scan = pymupdf.open()
    scan.new_page(width=595, height=842).insert_text((72, 100), "Receipt A total R100.00")
    scan.new_page(width=595, height=842).insert_text((72, 100), "Receipt B total R200.00")
    scan.save(tmp_path / "scan.pdf")
    scan.close()
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_state():
    STATE["project"], STATE["pages"] = None, None
    yield
    STATE["project"], STATE["pages"] = None, None


def _start(client, folder):
    app.config["START_FOLDER"] = str(folder)
    client.post(
        "/",
        data={
            "folder": str(folder),
            "action": "save",
            "statement": "CertifiedStatements.pdf",
            "role::scan.pdf": "receipt",
        },
    )


def test_a_fresh_source_gets_one_default_item_spanning_all_pages(client, claim_folder):
    _start(client, claim_folder)
    r = client.get("/receipts")
    assert r.status_code == 200

    proj = ClaimProject.load(claim_folder)
    items = proj.items_for("scan.pdf")
    assert len(items) == 1
    assert (items[0].first_page, items[0].last_page) == (1, 2)


def test_splitting_a_source_into_two_rows_persists_two_items(client, claim_folder):
    _start(client, claim_folder)
    client.get("/receipts")  # create the default item

    r = client.post(
        "/receipts",
        data={
            "rows::scan.pdf": "new0,new1",
            "first::scan.pdf::new0": "1", "last::scan.pdf::new0": "1",
            "amount::scan.pdf::new0": "100.00", "label::scan.pdf::new0": "Receipt A",
            "note::scan.pdf::new0": "",
            "first::scan.pdf::new1": "2", "last::scan.pdf::new1": "2",
            "amount::scan.pdf::new1": "200.00", "label::scan.pdf::new1": "Receipt B",
            "note::scan.pdf::new1": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"] == "/match"

    proj = ClaimProject.load(claim_folder)
    items = sorted(proj.items_for("scan.pdf"), key=lambda i: i.first_page)
    assert [(i.first_page, i.last_page, i.label) for i in items] == [
        (1, 1, "Receipt A"),
        (2, 2, "Receipt B"),
    ]
    assert items[0].key != items[1].key


def test_a_page_range_is_clamped_to_the_document(client, claim_folder):
    _start(client, claim_folder)
    client.get("/receipts")
    client.post(
        "/receipts",
        data={
            "rows::scan.pdf": "new0",
            "first::scan.pdf::new0": "0", "last::scan.pdf::new0": "999",
            "amount::scan.pdf::new0": "5.00", "label::scan.pdf::new0": "",
            "note::scan.pdf::new0": "",
        },
    )
    item = ClaimProject.load(claim_folder).items_for("scan.pdf")[0]
    assert (item.first_page, item.last_page) == (1, 2)


def test_rotating_a_page_turns_90_anticlockwise_and_reports_the_angle(client, claim_folder):
    _start(client, claim_folder)

    r = client.post("/rotate/scan.pdf/1")
    assert r.status_code == 200
    assert r.get_json() == {"rotation": 270}

    r = client.post("/rotate/scan.pdf/1")
    assert r.get_json() == {"rotation": 180}

    proj = ClaimProject.load(claim_folder)
    assert proj.source("scan.pdf").rotation_of(1) == 180
    assert proj.source("scan.pdf").rotation_of(2) == 0  # untouched


def test_rotation_is_reflected_in_the_rendered_thumbnail(client, claim_folder):
    _start(client, claim_folder)
    before = client.get("/doc/scan.pdf/page/1.png?dpi=60").data
    client.post("/rotate/scan.pdf/1")
    after = client.get("/doc/scan.pdf/page/1.png?dpi=60").data
    assert before != after


def test_rotating_a_page_outside_the_document_is_rejected(client, claim_folder):
    _start(client, claim_folder)
    assert client.post("/rotate/scan.pdf/99").status_code == 404
    assert client.post("/rotate/does-not-exist.pdf/1").status_code == 404


def test_deleting_every_row_leaves_the_source_with_no_items(client, claim_folder):
    """Emptying a source's rows must not crash - the next GET repopulates it."""
    _start(client, claim_folder)
    client.get("/receipts")
    client.post("/receipts", data={"rows::scan.pdf": ""})
    assert ClaimProject.load(claim_folder).items_for("scan.pdf") == []

    client.get("/receipts")  # ensure_default_item fires again
    assert len(ClaimProject.load(claim_folder).items_for("scan.pdf")) == 1


def test_open_ui_uses_the_omarchy_web_app_launcher_when_present(monkeypatch):
    from claims_processor import app as app_module

    calls = {}
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "/usr/bin/omarchy")
    monkeypatch.setattr(
        app_module.subprocess, "Popen", lambda cmd, **kw: calls.setdefault("cmd", cmd)
    )
    monkeypatch.setattr(
        app_module.webbrowser, "open", lambda url: calls.setdefault("browser", url)
    )

    app_module._open_ui("http://127.0.0.1:57311/")

    assert calls["cmd"] == ["omarchy", "launch", "webapp", "http://127.0.0.1:57311/"]
    assert "browser" not in calls  # the plain browser was not touched


def test_open_ui_falls_back_to_the_default_browser_without_omarchy(monkeypatch):
    from claims_processor import app as app_module

    opened = []
    monkeypatch.setattr(app_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    app_module._open_ui("http://127.0.0.1:57311/")

    assert opened == ["http://127.0.0.1:57311/"]
