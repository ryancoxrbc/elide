"""The local wizard: five screens, one claim folder, nothing leaves the machine."""

from __future__ import annotations

import secrets
import shutil
import subprocess
import webbrowser
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .build import run_build
from .filebrowser import (
    list_dir,
    native_picker,
    pick_folder_natively,
    recent_folders,
    remember_folder,
)
from .matching import find_candidates, suggest_amounts
from .models import ClaimItem, Match, Source, format_amount
from .project import ClaimProject, discover_pdfs, guess_statement
from .redact import build_plan
from .render import page_count, page_png
from .statement import parse_statement

app = Flask(__name__)
# Loopback-only and single-user; a fresh key each run is fine.
app.secret_key = secrets.token_hex(32)

# The active claim folder, set on the first screen.
STATE: dict[str, object] = {"project": None, "pages": None}


# Screens after the first need a chosen claim folder; send the user back if
# they deep-link or restart the server mid-run.
OPEN_ENDPOINTS = {"index", "static", "api_browse", "api_pick"}


@app.before_request
def _require_project():
    if request.endpoint in OPEN_ENDPOINTS or request.endpoint is None:
        return None
    if STATE.get("project") is None:
        return redirect(url_for("index"))
    return None


def project() -> ClaimProject:
    return STATE["project"]  # type: ignore[return-value]


def statement_pages(force: bool = False):
    """Parse the statement once and cache it for the session."""
    proj = project()
    if force or STATE.get("pages") is None:
        path = proj.statement_path
        STATE["pages"] = parse_statement(str(path)) if path and path.exists() else []
    return STATE["pages"]


# ------------------------------------------------------------------ step 1


@app.route("/", methods=["GET", "POST"])
def index():
    folder = request.form.get("folder") or request.args.get("folder") or app.config.get(
        "START_FOLDER", str(Path.cwd())
    )
    path = Path(folder).expanduser()

    error = ""
    if not path.is_dir():
        return render_template(
            "index.html",
            folder=str(path),
            pdfs=[],
            error=f"{path} is not a folder.",
            recents=recent_folders(),
            has_native_picker=native_picker() is not None,
        )

    proj = ClaimProject.load(path)
    names = discover_pdfs(path)

    if request.method == "POST" and request.form.get("action") == "save":
        proj.statement = request.form.get("statement", "")
        existing = {s.path: s for s in proj.sources}
        proj.sources = []
        for name in names:
            if name == proj.statement:
                continue
            role = request.form.get(f"role::{name}", "receipt")
            src = existing.get(name) or Source(path=name)
            src.include = role == "receipt"
            try:
                src.page_count = page_count(path / name)
            except Exception:
                src.page_count = src.page_count or 1
            proj.sources.append(src)

        # A document that vanished or was renamed leaves its claim items and
        # any matches on them dangling - drop them rather than carry references
        # to a source that no longer exists.
        valid_sources = {s.path for s in proj.sources}
        proj.items = [i for i in proj.items if i.source in valid_sources]
        valid_keys = {i.key for i in proj.items}
        proj.matches = {k: v for k, v in proj.matches.items() if k in valid_keys}

        proj.save()
        remember_folder(path)
        STATE["project"] = proj
        STATE["pages"] = None
        return redirect(url_for("receipts"))

    if not proj.statement:
        proj.statement = guess_statement(path, names)

    STATE["project"] = proj
    roles = {s.path: ("receipt" if s.include else "ignore") for s in proj.sources}
    info = []
    for name in names:
        try:
            count = page_count(path / name)
        except Exception:
            count = 0
        info.append({"name": name, "pages": count, "role": roles.get(name, "receipt")})
    return render_template(
        "index.html",
        folder=str(path),
        pdfs=info,
        statement=proj.statement,
        error=error,
        recents=recent_folders(),
        has_native_picker=native_picker() is not None,
    )


@app.route("/api/browse")
def api_browse():
    """Directory listing for the in-page folder browser."""
    return jsonify(list_dir(request.args.get("path") or str(Path.home())))


@app.route("/api/pick", methods=["POST"])
def api_pick():
    """Open the desktop's own folder chooser and report what was picked."""
    start = (request.json or {}).get("path") if request.is_json else None
    chosen, error = pick_folder_natively(start)
    return jsonify({"path": chosen, "error": error})


# ------------------------------------------------------------------ step 2


@app.route("/receipts", methods=["GET", "POST"])
def receipts():
    """One PDF can be several receipts, or one receipt can span several pages.

    Each included source gets its own set of claim items - page ranges with an
    amount - edited as a variable number of rows. The form submits, per source,
    a ``rows::<path>`` list of row ids and then ``<field>::<path>::<row id>``
    for each row, so a row added or removed in the browser needs no extra round
    trip: it is only reconciled against ``proj.items`` on submit.
    """
    proj = project()

    if request.method == "POST":
        for src in proj.included_sources():
            row_ids = [r for r in (request.form.get(f"rows::{src.path}") or "").split(",") if r]
            new_items = []
            for rid in row_ids:
                def field(name: str, default: str = "") -> str:
                    return (request.form.get(f"{name}::{src.path}::{rid}") or default).strip()

                bound = max(src.page_count, 1)
                first = _clamp_int(field("first", "1"), 1, bound, default=1)
                last = _clamp_int(field("last", str(first)), first, bound, default=first)

                existing = proj.item(rid)
                item = existing if (existing and existing.source == src.path) else ClaimItem(source=src.path)
                item.first_page, item.last_page = first, last
                item.amount = field("amount")
                item.label = field("label")
                item.note = field("note")
                new_items.append(item)

            proj.items = [i for i in proj.items if i.source != src.path] + new_items

        valid_keys = {i.key for i in proj.items}
        proj.matches = {k: v for k, v in proj.matches.items() if k in valid_keys}
        proj.save()
        return redirect(url_for("match"))

    groups = []
    for src in proj.included_sources():
        proj.ensure_default_item(src)
        rows = [
            {"item": it, "suggestions": suggest_amounts(str(proj.abs_path(src.path)), pages=it.pages)}
            for it in proj.items_for(src.path)
        ]
        groups.append(
            {
                "source": src,
                "pages": list(range(1, max(src.page_count, 1) + 1)),
                "rows": rows,
                "no_text": not any(r["suggestions"] for r in rows),
                "default_label": Path(src.path).stem.replace("_", " "),
            }
        )
    proj.save()  # persist any default items ensure_default_item just created
    return render_template("receipts.html", project=proj, groups=groups)


def _clamp_int(text: str, low: int, high: int, *, default: int) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


@app.route("/rotate/<path:name>/<int:page>", methods=["POST"])
def rotate_page(name: str, page: int):
    """Rotate one page 90 anticlockwise and report the new angle immediately.

    Used by the thumbnail's rotate button: the browser swaps the image in
    place from this response rather than reloading the page.
    """
    proj = project()
    src = proj.source(name)
    if src is None or not 1 <= page <= max(src.page_count, 1):
        abort(404)
    degrees = src.rotate_anticlockwise(page)
    proj.save()
    return jsonify({"rotation": degrees})


@app.route("/doc/<path:name>/page/<int:number>.png")
def doc_page(name: str, number: int):
    proj = project()
    target = proj.abs_path(name)
    if not target.exists():
        abort(404)
    src = proj.source(name)
    rotation = src.rotation_of(number) if src else 0
    dpi = int(request.args.get("dpi", 110))
    return Response(page_png(target, number, dpi=dpi, rotation=rotation), mimetype="image/png")


# ------------------------------------------------------------------ step 3


@app.route("/match", methods=["GET", "POST"])
def match():
    proj = project()
    pages = statement_pages()

    if request.method == "POST":
        for item in proj.claimed_items():
            choice = request.form.get(f"pick::{item.key}")
            if not choice:
                proj.matches.pop(item.key, None)
                continue
            if choice == "none":
                proj.matches[item.key] = Match(
                    item_key=item.key,
                    page=0,
                    row_index=-1,
                    column="",
                    date="",
                    description="",
                    account="",
                    confirmed=True,
                    not_found=True,
                )
                continue
            page_no, row_index = (int(part) for part in choice.split(":"))
            row = pages[page_no - 1].rows[row_index]
            column = row.matches_amount(item.value) or "debit"
            proj.matches[item.key] = Match(
                item_key=item.key,
                page=page_no,
                row_index=row_index,
                column=column,
                date=row.date_text,
                description=row.description_text,
                account=row.account,
                confirmed=True,
            )
        proj.save()
        return redirect(url_for("preview"))

    groups = []
    for item in proj.included_items():
        value = item.value
        candidates = find_candidates(pages, value) if value is not None else []
        chosen = proj.matches.get(item.key)
        groups.append(
            {
                "item": item,
                "amount": format_amount(value) if value is not None else "",
                "candidates": candidates,
                "chosen": chosen,
                "label": item.label or Path(item.source).stem,
            }
        )
    return render_template("match.html", project=proj, groups=groups)


# ------------------------------------------------------------------ step 4


@app.route("/preview", methods=["GET", "POST"])
def preview():
    proj = project()
    pages = statement_pages()

    if request.method == "POST":
        proj.keep_empty_pages = request.form.get("keep_empty") == "on"
        proj.include_index_page = request.form.get("include_index") == "on"
        proj.redact_balance_column = request.form.get("redact_balance") == "on"
        proj.redact_summary_balances = request.form.get("redact_summary") == "on"
        proj.output_name = (request.form.get("output_name") or "").strip()
        proj.save()
        return redirect(url_for("build"))

    kept = proj.kept_rows()
    plan = build_plan(
        pages,
        kept,
        redact_balance_column=proj.redact_balance_column,
        redact_summary_balances=proj.redact_summary_balances,
    )

    views = []
    for spage in pages:
        boxes = plan.for_page(spage.number)
        keeps = [
            spage.rows[i].rect
            for (p, i) in kept
            if p == spage.number and i < len(spage.rows)
        ]
        views.append(
            {
                "number": spage.number,
                "width": spage.width,
                "height": spage.height,
                "account": spage.account,
                "boxes": [b.rect for b in boxes],
                "keeps": keeps,
                "kept_count": len(keeps),
                "removed_count": sum(
                    1 for b in boxes if b.reason == "unrelated transaction"
                ),
            }
        )

    default_name = proj.output_name or f"Claim_Bundle_{Path(proj.folder).name}.pdf"
    return render_template(
        "preview.html", project=proj, views=views, default_name=default_name
    )


@app.route("/statement/page/<int:number>.png")
def statement_page_image(number: int):
    proj = project()
    path = proj.statement_path
    if path is None or not path.exists():
        abort(404)
    dpi = int(request.args.get("dpi", 100))
    return Response(page_png(path, number, dpi=dpi), mimetype="image/png")


# ------------------------------------------------------------------ step 5


@app.route("/build", methods=["GET", "POST"])
def build():
    proj = project()
    result = None
    if request.method == "POST":
        result = run_build(proj, run_ocr=request.form.get("ocr") == "on")
    return render_template(
        "build.html",
        project=proj,
        result=result,
        report=proj.build_report,
        bundle_name=proj.bundle_path.name,
        bundle_exists=proj.bundle_path.exists(),
    )


@app.route("/download")
def download():
    proj = project()
    if not proj.bundle_path.exists():
        abort(404)
    return send_file(proj.bundle_path, as_attachment=False)


def _open_ui(url: str) -> None:
    """Open the wizard in a browser.

    On Omarchy, ``omarchy launch webapp`` opens it as a web app - an app-mode
    browser window with no tab strip or address bar - without adding anything
    to the app menu. Everywhere else, the default browser in the normal way.
    """
    if shutil.which("omarchy"):
        try:
            subprocess.Popen(
                ["omarchy", "launch", "webapp", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass  # fall through to the normal browser
    webbrowser.open(url)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Claims Processor")
    parser.add_argument("folder", nargs="?", default=str(Path.cwd()))
    parser.add_argument("--port", type=int, default=57311)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    app.config["START_FOLDER"] = str(Path(args.folder).expanduser().resolve())
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  Claims Processor\n  claim folder : {app.config['START_FOLDER']}\n  open         : {url}\n")
    if not args.no_browser:
        _open_ui(url)
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
