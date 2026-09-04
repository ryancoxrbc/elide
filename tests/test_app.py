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


def test_a_fresh_source_gets_one_default_item_holding_all_its_pages(client, claim_folder):
    _start(client, claim_folder)
    r = client.get("/split")
    assert r.status_code == 200

    proj = ClaimProject.load(claim_folder)
    items = proj.items_for("scan.pdf")
    assert len(items) == 1
    assert items[0].pages == [1, 2]


def test_cutting_a_source_into_two_receipts_persists_two_items(client, claim_folder):
    _start(client, claim_folder)
    client.get("/split")  # create the default item

    r = client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "1",
            "pages::scan.pdf::new1": "2",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"] == "/amounts"

    proj = ClaimProject.load(claim_folder)
    items = proj.items_for("scan.pdf")
    assert [i.pages for i in items] == [[1], [2]]
    assert items[0].key != items[1].key


def test_two_receipts_may_be_given_the_same_page(client, claim_folder):
    """Two till slips on one scanned sheet: the page is picked for both."""
    _start(client, claim_folder)
    client.get("/split")

    client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "1",
            "pages::scan.pdf::new1": "1,2",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [i.pages for i in items] == [[1], [1, 2]]


def test_a_receipt_may_be_given_pages_that_do_not_run_on(client, claim_folder):
    _start(client, claim_folder)
    client.get("/split")
    client.post("/split", data={"rows::scan.pdf": "new0",
                                "pages::scan.pdf::new0": "2,1"})

    item = ClaimProject.load(claim_folder).items_for("scan.pdf")[0]
    assert item.pages == [1, 2]  # ascending and deduped, whatever order they were clicked in


def test_receipts_are_stored_in_page_order(client, claim_folder):
    _start(client, claim_folder)
    client.get("/split")

    client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "2",
            "pages::scan.pdf::new1": "1",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [i.pages for i in items] == [[1], [2]]


def test_a_page_that_is_not_in_the_document_is_dropped(client, claim_folder):
    """Nothing to clamp to: the list names thumbnails, and these name none."""
    _start(client, claim_folder)
    client.get("/split")
    client.post("/split", data={"rows::scan.pdf": "new0",
                                "pages::scan.pdf::new0": "0,1,2,999,x"})

    item = ClaimProject.load(claim_folder).items_for("scan.pdf")[0]
    assert item.pages == [1, 2]


def test_a_receipt_left_with_no_pages_is_removed(client, claim_folder):
    _start(client, claim_folder)
    client.get("/split")
    client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "1,2",
            "pages::scan.pdf::new1": "",
        },
    )

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [i.pages for i in items] == [[1, 2]]


def test_re_cutting_a_receipt_keeps_its_id_and_its_amount(client, claim_folder):
    """The id is what a confirmed match hangs off, so it must survive an edit."""
    _start(client, claim_folder)
    client.get("/split")
    client.post("/split", data={"rows::scan.pdf": "new0",
                                "pages::scan.pdf::new0": "1"})

    only = ClaimProject.load(claim_folder).items_for("scan.pdf")[0]
    client.post("/amounts", data={f"amount::{only.key}": "100.00"})
    client.post("/split", data={"rows::scan.pdf": only.key,
                                f"pages::scan.pdf::{only.key}": "1,2"})

    again = ClaimProject.load(claim_folder).items_for("scan.pdf")[0]
    assert again.key == only.key
    assert again.pages == [1, 2]
    assert again.amount == "100.00"


def test_each_receipt_is_offered_only_the_amounts_on_its_own_pages(client, claim_folder):
    """Receipt A's R100 is on page 1 and receipt B's R200 on page 2."""
    _start(client, claim_folder)
    client.get("/split")
    client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "1",
            "pages::scan.pdf::new1": "2",
        },
    )

    page = client.get("/amounts").get_data(as_text=True)
    first, second = page.split("Receipt 2")
    assert "R 100.00" in first and "R 200.00" not in first
    assert "R 200.00" in second and "R 100.00" not in second


def test_the_amounts_step_stores_what_was_typed_against_each_receipt(client, claim_folder):
    _start(client, claim_folder)
    client.get("/split")
    client.post(
        "/split",
        data={
            "rows::scan.pdf": "new0,new1",
            "pages::scan.pdf::new0": "1",
            "pages::scan.pdf::new1": "2",
        },
    )
    ids = [i.id for i in ClaimProject.load(claim_folder).items_for("scan.pdf")]

    r = client.post(
        "/amounts",
        data={
            f"amount::{ids[0]}": "100.00", f"label::{ids[0]}": "Receipt A",
            f"note::{ids[0]}": "first slip",
            f"amount::{ids[1]}": "200.00", f"label::{ids[1]}": "Receipt B",
            f"note::{ids[1]}": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"] == "/match"

    items = ClaimProject.load(claim_folder).items_for("scan.pdf")
    assert [(i.amount, i.label, i.note) for i in items] == [
        ("100.00", "Receipt A", "first slip"),
        ("200.00", "Receipt B", ""),
    ]
    # The pages are untouched by this step.
    assert [i.pages for i in items] == [[1], [2]]


def test_ignoring_a_page_takes_it_in_and_out_of_the_claim(client, claim_folder):
    _start(client, claim_folder)

    assert client.post("/ignore/scan.pdf/2").get_json() == {"ignored": True}
    assert ClaimProject.load(claim_folder).source("scan.pdf").ignored == [2]

    assert client.post("/ignore/scan.pdf/2").get_json() == {"ignored": False}
    assert ClaimProject.load(claim_folder).source("scan.pdf").ignored == []


def test_ignoring_a_page_outside_the_document_is_rejected(client, claim_folder):
    _start(client, claim_folder)
    assert client.post("/ignore/scan.pdf/99").status_code == 404
    assert client.post("/ignore/does-not-exist.pdf/1").status_code == 404


def test_an_ignored_page_offers_none_of_its_amounts(client, claim_folder):
    """Page 2 holds receipt B's R200; ignoring it takes the figure with it."""
    _start(client, claim_folder)
    client.get("/split")  # one receipt spanning both pages
    client.post("/ignore/scan.pdf/2")

    page = client.get("/amounts").get_data(as_text=True)
    assert "R 100.00" in page
    assert "R 200.00" not in page


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
    client.get("/split")
    client.post("/split", data={"rows::scan.pdf": ""})
    assert ClaimProject.load(claim_folder).items_for("scan.pdf") == []

    client.get("/split")  # ensure_default_item fires again
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
