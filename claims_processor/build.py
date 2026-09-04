"""The build step: redact, assemble, verify, report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .bundle import BundleEntry, build_bundle
from .models import format_amount, page_label, pages_of
from .project import ClaimProject
from .redact import RedactionError, apply_plan, build_plan, check_plan
from .statement import parse_statement
from .verify import VerificationResult, verify


@dataclass
class BuildResult:
    ok: bool
    report: str
    bundle: Path | None = None
    verification: VerificationResult | None = None
    error: str = ""


def run_build(project: ClaimProject, *, run_ocr: bool = False) -> BuildResult:
    """Produce the bundle, refusing to emit anything that fails verification."""
    statement = project.statement_path
    if statement is None or not statement.exists():
        return BuildResult(False, "", error="No certified statement selected.")

    project.ensure_output_dir()
    pages = parse_statement(str(statement))
    kept = project.kept_rows()

    plan = build_plan(
        pages,
        kept,
        redact_balance_column=project.redact_balance_column,
        redact_summary_balances=project.redact_summary_balances,
    )
    try:
        check_plan(pages, plan, kept)
    except RedactionError as exc:
        return BuildResult(False, "", error=str(exc))

    apply_plan(str(statement), project.redacted_path, plan)

    statement_pages = None
    if not project.keep_empty_pages:
        statement_pages = sorted(plan.pages_with_content) or [1]

    entries = [
        BundleEntry(item=i, source=project.source(i.source), match=project.matches.get(i.key))
        for i in project.included_items()
        if project.source(i.source) is not None
    ]
    summary = build_bundle(
        project.bundle_path,
        redacted_statement=project.redacted_path,
        entries=entries,
        claim_folder=project.folder,
        statement_pages=statement_pages,
        include_index=project.include_index_page,
    )

    # Verify twice: the redacted statement on its own, then the statement pages
    # as they actually landed in the bundle - proving the merge did not carry
    # anything back in. Both scans are limited to statement pages, since the
    # receipts legitimately repeat dates and amounts found on removed rows.
    result = verify(
        project.redacted_path,
        removed=plan.removed_text,
        kept=plan.kept_text,
        run_ocr=run_ocr,
    )
    first = summary["index_pages"] + 1
    in_bundle = verify(
        project.bundle_path,
        removed=plan.removed_text,
        kept=plan.kept_text,
        run_ocr=False,
        pages=range(first, first + summary["statement_pages"]),
    )
    if not in_bundle.passed:
        result.passed = False
        result.leaks.extend(f"{t} (in the assembled bundle)" for t in in_bundle.leaks)
        result.missing.extend(f"{t} (in the assembled bundle)" for t in in_bundle.missing)

    report = _write_report(project, plan, summary, result, run_ocr)
    project.build_report = report
    project.save()

    if not result.passed:
        return BuildResult(
            False,
            report,
            bundle=project.bundle_path,
            verification=result,
            error="Verification failed - the bundle leaks information and must not be sent.",
        )
    return BuildResult(True, report, bundle=project.bundle_path, verification=result)


def _write_report(project, plan, summary, result, run_ocr: bool) -> str:
    lines = [
        "CLAIM BUNDLE - REDACTION REPORT",
        "=" * 60,
        f"Generated       : {datetime.now().isoformat(timespec='seconds')}",
        f"Claim folder    : {project.folder}",
        f"Statement       : {project.statement}",
        f"Bundle          : {project.bundle_path.name}",
        "",
        "CLAIMED ITEMS",
        "-" * 60,
    ]

    total = 0
    for item in project.included_items():
        match = project.matches.get(item.key)
        total += item.value or 0
        amount = format_amount(item.value) if item.value is not None else "(no amount)"
        label = item.label or Path(item.source).stem
        lines.append(f"{amount:>14}  {label}")
        source = project.source(item.source)
        where = page_label(pages_of(item, source)) if source else item.page_label
        lines.append(f"{'':>14}  file: {item.source} ({where})")
        if match and match.confirmed and not match.not_found:
            lines.append(
                f"{'':>14}  matched: p.{match.page} {match.date} "
                f"{match.description} [{match.account}]"
            )
        elif match and match.not_found:
            lines.append(f"{'':>14}  matched: NOT FOUND on statement")
        else:
            lines.append(f"{'':>14}  matched: none")
        lines.append("")

    lines += [
        f"{'TOTAL':>14}  {format_amount(total)}",
        "",
        "REDACTION",
        "-" * 60,
        f"Redaction boxes applied      : {len(plan.boxes)}",
        f"Statement lines removed      : "
        f"{sum(1 for b in plan.boxes if b.reason == 'unrelated transaction')}",
        f"Balances removed             : "
        f"{sum(1 for b in plan.boxes if 'balance' in b.reason)}",
        f"Statement pages in bundle    : {summary['statement_pages']}",
        f"Receipt pages in bundle      : {summary['receipt_pages']}",
        "",
        "Policy: unrelated transaction rows removed in full (date, description and",
        "amounts); the running balance removed on every row including claimed ones;",
        "page-1 closing balances removed. Account holder, account numbers, statement",
        "period, column headings and the bank's footer are preserved.",
        "",
        "Text is deleted from the PDF content stream, not covered over. The pages",
        "remain vector PDF - selectable text, not flattened images.",
        "",
        "VERIFICATION",
        "-" * 60,
        result.as_report(),
    ]
    if not run_ocr:
        lines.append("\n  - OCR check not run (enable it for a render-level check)")

    text = "\n".join(lines)
    project.report_path.write_text(text, encoding="utf-8")
    return text
