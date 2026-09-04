"""Persistence, including migrating a claim_project.json from before receipts
could span pages or share a document.
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from claims_processor.models import ClaimItem, Match, Source
from claims_processor.project import ClaimProject


@pytest.fixture
def claim_folder(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    doc.save(tmp_path / "receipt.pdf")
    doc.close()
    return tmp_path


def test_fresh_project_has_no_sources_or_items(claim_folder):
    proj = ClaimProject.load(claim_folder)
    assert proj.sources == []
    assert proj.items == []
    assert not (claim_folder / "claim_project.json").exists()  # nothing written until save()


def test_save_and_load_round_trips_sources_items_and_matches(claim_folder):
    proj = ClaimProject.load(claim_folder)
    proj.statement = "CertifiedStatements.pdf"
    src = Source(path="receipt.pdf", page_count=2, rotations={"1": 270})
    proj.sources.append(src)
    item = ClaimItem(source=src.path, first_page=1, last_page=2, label="A", amount="10.00")
    proj.items.append(item)
    proj.matches[item.key] = Match(
        item_key=item.key,
        page=1,
        row_index=0,
        column="debit",
        date="2026-01-01",
        description="X",
        account="Y",
        confirmed=True,
    )
    proj.save()

    reloaded = ClaimProject.load(claim_folder)
    assert reloaded.statement == "CertifiedStatements.pdf"
    assert len(reloaded.sources) == 1
    assert reloaded.sources[0].rotation_of(1) == 270
    assert len(reloaded.items) == 1
    assert reloaded.items[0].key == item.key
    assert reloaded.items[0].label == "A"
    assert item.key in reloaded.matches
    assert reloaded.matches[item.key].description == "X"


def test_ensure_default_item_spans_every_page(claim_folder):
    proj = ClaimProject.load(claim_folder)
    src = Source(path="receipt.pdf", page_count=2)
    proj.sources.append(src)
    proj.ensure_default_item(src)
    assert len(proj.items_for(src.path)) == 1
    only = proj.items_for(src.path)[0]
    assert (only.first_page, only.last_page) == (1, 2)

    # Calling it again must not add a second item once one already exists.
    proj.ensure_default_item(src)
    assert len(proj.items_for(src.path)) == 1


def test_two_items_of_one_source_get_distinct_keys(claim_folder):
    """Identity is the item's own id, not its source and range.

    It has to be: a range moves whenever a neighbouring receipt does, and the
    match confirmed against an item must move with it.
    """
    proj = ClaimProject.load(claim_folder)
    a = ClaimItem(source="receipt.pdf", first_page=1, last_page=1, amount="1.00")
    b = ClaimItem(source="receipt.pdf", first_page=2, last_page=2, amount="2.00")
    proj.items += [a, b]
    proj.matches[a.key] = Match(item_key=a.key, page=1, row_index=0, column="debit",
                                 date="", description="", account="", confirmed=True)
    proj.matches[b.key] = Match(item_key=b.key, page=1, row_index=1, column="debit",
                                 date="", description="", account="", confirmed=True)
    proj.save()

    reloaded = ClaimProject.load(claim_folder)
    assert len(reloaded.items) == 2
    assert len(reloaded.matches) == 2
    assert {i.key for i in reloaded.items} == set(reloaded.matches)


def test_loading_separates_receipts_that_claim_the_same_page(claim_folder):
    """A file written before a page belonged to one receipt only.

    Left alone, the overlap would put page 1 into the bundle twice - so the
    ranges are laid out again on load, and the matches ride along on the item
    ids rather than being lost.
    """
    proj = ClaimProject.load(claim_folder)
    proj.sources.append(Source(path="receipt.pdf", page_count=2))
    a = ClaimItem(source="receipt.pdf", first_page=1, last_page=2, amount="1.00")
    b = ClaimItem(source="receipt.pdf", first_page=1, last_page=2, amount="2.00")
    proj.items += [a, b]
    proj.matches[b.key] = Match(item_key=b.key, page=1, row_index=1, column="debit",
                                date="", description="", account="", confirmed=True)
    proj.save()

    reloaded = ClaimProject.load(claim_folder)
    items = reloaded.items_for("receipt.pdf")
    assert [(i.first_page, i.last_page) for i in items] == [(1, 1), (2, 2)]
    assert [i.amount for i in items] == ["1.00", "2.00"]
    assert set(reloaded.matches) == {b.key}


def test_loading_keeps_a_shared_page_that_was_set_by_hand(claim_folder):
    """The rule is a default, not a cage - a pinned overlap survives a reload."""
    proj = ClaimProject.load(claim_folder)
    proj.sources.append(Source(path="receipt.pdf", page_count=2))
    proj.items += [
        ClaimItem(source="receipt.pdf", first_page=1, last_page=1, amount="1.00", pinned=True),
        ClaimItem(source="receipt.pdf", first_page=1, last_page=1, amount="2.00", pinned=True),
    ]
    proj.save()

    items = ClaimProject.load(claim_folder).items_for("receipt.pdf")
    assert [(i.first_page, i.last_page) for i in items] == [(1, 1), (1, 1)]
    assert all(i.pinned for i in items)


def test_a_project_file_from_before_pinning_loads_unpinned(claim_folder):
    proj = ClaimProject.load(claim_folder)
    proj.sources.append(Source(path="receipt.pdf", page_count=2))
    proj.items.append(ClaimItem(source="receipt.pdf", first_page=1, last_page=2))
    proj.save()

    written = json.loads((claim_folder / "claim_project.json").read_text())
    for item in written["items"]:
        del item["pinned"]
    (claim_folder / "claim_project.json").write_text(json.dumps(written))

    only = ClaimProject.load(claim_folder).items_for("receipt.pdf")[0]
    assert only.pinned is False


def test_loading_leaves_a_source_with_one_receipt_alone(claim_folder):
    proj = ClaimProject.load(claim_folder)
    proj.sources.append(Source(path="receipt.pdf", page_count=2))
    proj.items.append(ClaimItem(source="receipt.pdf", first_page=1, last_page=2))
    proj.save()

    only = ClaimProject.load(claim_folder).items_for("receipt.pdf")[0]
    assert (only.first_page, only.last_page) == (1, 2)


def test_a_legacy_project_file_migrates_cleanly(claim_folder):
    """The exact shape claim_project.json had before this change."""
    legacy = {
        "statement": "CertifiedStatements.pdf",
        "statement_rotation": 0,
        "receipts": [
            {
                "path": "receipt.pdf",
                "label": "Harborlight",
                "amount": "1,322.98",
                "note": "",
                "rotation": 90,
                "include": True,
            }
        ],
        "matches": {
            "receipt.pdf": {
                "receipt_path": "receipt.pdf",
                "page": 10,
                "row_index": 15,
                "column": "debit",
                "date": "2026-08-26",
                "description": "NETPAY*Harborlight",
                "account": "Credit Card Account",
                "confirmed": True,
                "not_found": False,
            }
        },
        "keep_empty_pages": True,
        "redact_balance_column": True,
        "redact_summary_balances": True,
        "include_index_page": True,
        "build_report": "",
        "output_name": "",
    }
    (claim_folder / "claim_project.json").write_text(json.dumps(legacy), encoding="utf-8")

    proj = ClaimProject.load(claim_folder)
    assert proj.statement == "CertifiedStatements.pdf"
    assert len(proj.sources) == 1
    src = proj.sources[0]
    assert src.path == "receipt.pdf"
    assert src.page_count == 2  # read from the real PDF, not guessed
    assert src.rotation_of(1) == 90 and src.rotation_of(2) == 90

    assert len(proj.items) == 1
    item = proj.items[0]
    assert (item.first_page, item.last_page) == (1, 2)
    assert item.label == "Harborlight"
    assert item.amount == "1,322.98"

    assert item.key in proj.matches
    migrated = proj.matches[item.key]
    assert migrated.item_key == item.key
    assert migrated.description == "NETPAY*Harborlight"
    assert migrated.page == 10 and migrated.confirmed is True

    # And it saves back out in the new shape, not the old one.
    proj.save()
    saved = json.loads((claim_folder / "claim_project.json").read_text())
    assert "sources" in saved and "receipts" not in saved


def test_migrating_a_missing_pdf_falls_back_to_one_page(claim_folder):
    """A receipt referenced in the JSON that no longer exists on disk."""
    legacy = {
        "statement": "",
        "receipts": [{"path": "gone.pdf", "label": "", "amount": "5.00", "note": "",
                       "rotation": 0, "include": True}],
        "matches": {},
    }
    (claim_folder / "claim_project.json").write_text(json.dumps(legacy), encoding="utf-8")

    proj = ClaimProject.load(claim_folder)
    assert proj.sources[0].page_count == 1
    assert proj.items[0].last_page == 1


def test_generated_paths_all_sit_in_the_output_subfolder(claim_folder):
    proj = ClaimProject.load(claim_folder)
    proj.statement = "CertifiedStatements.pdf"

    out = claim_folder / "claim_output"
    assert proj.output_root == out
    for path in (proj.redacted_path, proj.bundle_path, proj.report_path):
        assert path.parent == out

    assert not out.exists()  # nothing created just by asking for a path
    assert proj.ensure_output_dir() == out
    assert out.is_dir()
