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


def test_two_rows_claiming_the_whole_document_are_split_between_them(client, claim_folder):
    """The rule holds on the server, not only in the browser.

    A submit with scripting off - or a hand-posted form - can still name the
    same pages twice; laying the ranges out again is what keeps a page from
    entering the bundle under two different receipts.
    """
    _start(client, claim_folder)
    client.get("/receipts")

    client.post(
        "/receipts",
        data={
            "rows::scan.pdf": "new0,new1",
            "first::scan.pdf::new0": "1", "last::scan.pdf::new0": "2",
            "amount::scan.pdf::new0": "100.00", "label::scan.pdf::new0": "Receipt A",
            "note::scan.pdf::new0": "",
            "first::scan.pdf::new1": "1", "last::scan.pdf::new1": "2",
            "amount::scan.pdf::new1": "200.00", "label::scan.pdf::new1": "Receipt B",
            "note::scan.pdf::new1": "",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [(i.first_page, i.last_page, i.label) for i in items] == [
        (1, 1, "Receipt A"),
        (2, 2, "Receipt B"),
    ]


def test_rows_are_stored_in_page_order(client, claim_folder):
    _start(client, claim_folder)
    client.get("/receipts")

    client.post(
        "/receipts",
        data={
            "rows::scan.pdf": "new0,new1",
            "first::scan.pdf::new0": "2", "last::scan.pdf::new0": "2",
            "amount::scan.pdf::new0": "200.00", "label::scan.pdf::new0": "Second",
            "note::scan.pdf::new0": "",
            "first::scan.pdf::new1": "1", "last::scan.pdf::new1": "1",
            "amount::scan.pdf::new1": "100.00", "label::scan.pdf::new1": "First",
            "note::scan.pdf::new1": "",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [i.label for i in items] == ["First", "Second"]


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


def test_a_row_set_by_hand_keeps_a_page_it_shares(client, claim_folder):
    """Two till slips on one scanned sheet: both rows pinned to page 1."""
    _start(client, claim_folder)
    client.get("/receipts")

    client.post(
        "/receipts",
        data={
            "rows::scan.pdf": "new0,new1",
            "first::scan.pdf::new0": "1", "last::scan.pdf::new0": "1",
            "pin::scan.pdf::new0": "on",
            "amount::scan.pdf::new0": "100.00", "label::scan.pdf::new0": "Slip A",
            "note::scan.pdf::new0": "",
            "first::scan.pdf::new1": "1", "last::scan.pdf::new1": "1",
            "pin::scan.pdf::new1": "on",
            "amount::scan.pdf::new1": "200.00", "label::scan.pdf::new1": "Slip B",
            "note::scan.pdf::new1": "",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [(i.first_page, i.last_page, i.label) for i in items] == [
        (1, 1, "Slip A"),
        (1, 1, "Slip B"),
    ]
    assert all(i.pinned for i in items)


def test_an_unticked_box_puts_the_row_back_under_the_layout(client, claim_folder):
    _start(client, claim_folder)
    client.get("/receipts")

    shared = {
        "rows::scan.pdf": "new0,new1",
        "first::scan.pdf::new0": "1", "last::scan.pdf::new0": "1",
        "amount::scan.pdf::new0": "100.00", "label::scan.pdf::new0": "Slip A",
        "note::scan.pdf::new0": "",
        "first::scan.pdf::new1": "1", "last::scan.pdf::new1": "1",
        "amount::scan.pdf::new1": "200.00", "label::scan.pdf::new1": "Slip B",
        "note::scan.pdf::new1": "",
    }
    client.post("/receipts", data={**shared, "pin::scan.pdf::new0": "on",
                                   "pin::scan.pdf::new1": "on"})
    ids = [i.id for i in ClaimProject.load(claim_folder).items_for("scan.pdf")]

    # Re-submit the same rows with neither box ticked.
    again = dict(shared)
    again["rows::scan.pdf"] = ",".join(ids)
    for old_key, new_key in zip(("new0", "new1"), ids):
        for name in ("first", "last", "amount", "label", "note"):
            again[f"{name}::scan.pdf::{new_key}"] = again.pop(f"{name}::scan.pdf::{old_key}")
    client.post("/receipts", data=again)

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [(i.first_page, i.last_page) for i in items] == [(1, 1), (2, 2)]
    assert not any(i.pinned for i in items)


def test_amount_chips_are_scoped_to_the_page_range_asked_for(client, claim_folder):
    """Splitting a receipt off moves the figures with the pages.

    Receipt A's R100 is on page 1 and receipt B's R200 on page 2, so a range
    must only ever offer what its own pages say.
    """
    _start(client, claim_folder)

    assert client.get("/api/amounts/scan.pdf?first=1&last=1").get_json() == {
        "amounts": ["100.00"]
    }
    assert client.get("/api/amounts/scan.pdf?first=2&last=2").get_json() == {
        "amounts": ["200.00"]
    }
    both = client.get("/api/amounts/scan.pdf?first=1&last=2").get_json()["amounts"]
    assert sorted(both) == ["100.00", "200.00"]


def test_amount_chips_for_an_unknown_document_are_rejected(client, claim_folder):
    _start(client, claim_folder)
    assert client.get("/api/amounts/does-not-exist.pdf?first=1&last=1").status_code == 404


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
