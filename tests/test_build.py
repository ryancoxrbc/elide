"""run_build writes only into claim_output/, never beside the source documents."""

from __future__ import annotations

from claims_processor.build import run_build
from claims_processor.models import ClaimItem, Source
from claims_processor.project import OUTPUT_DIR, ClaimProject, discover_pdfs


def _project(statement_pdf, receipt_pdf) -> ClaimProject:
    """A claim folder holding the shared fixtures, both already in tmp_path."""
    folder = statement_pdf.parent
    proj = ClaimProject(folder=str(folder), statement=statement_pdf.name)
    proj.sources.append(Source(path=receipt_pdf.name, page_count=1))
    proj.items.append(
        ClaimItem(source=receipt_pdf.name, pages=[1], amount="1322.98")
    )
    return proj


def test_a_build_writes_everything_into_the_output_subfolder(
    tmp_path, statement_pdf, receipt_pdf
):
    proj = _project(statement_pdf, receipt_pdf)
    sources_before = discover_pdfs(tmp_path)

    result = run_build(proj)
    assert result.ok, result.error

    out = tmp_path / OUTPUT_DIR
    assert out.is_dir()
    assert (out / "statement_redacted.pdf").exists()
    assert list(out.glob("Claim_Bundle_*.pdf"))
    assert (out / "redaction_report.txt").exists()

    # The claim folder itself gained only the state file - nothing a reopen
    # could misread as a source document.
    assert discover_pdfs(tmp_path) == sources_before
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        OUTPUT_DIR,
        "claim_project.json",
        "receipt.pdf",
        "statement.pdf",
    ]


def test_reopening_after_a_build_still_sees_only_the_originals(
    tmp_path, statement_pdf, receipt_pdf
):
    proj = _project(statement_pdf, receipt_pdf)
    assert run_build(proj).ok

    reopened = ClaimProject.load(tmp_path)
    assert discover_pdfs(reopened.folder) == ["receipt.pdf", "statement.pdf"]
