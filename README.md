# Elide

Turns a folder of receipts plus a certified bank statement into **one PDF** you can submit,
with every unrelated transaction and every account balance permanently removed from the
statement.

The redaction is real: text is deleted from the PDF content stream, not covered with a black
rectangle that still has selectable text underneath. The pages stay vector PDF — selectable
text, working search, no flattening to images.

## A starting point, not a finished product

This was built against one bank's certified-statement layout: South African, `R` currency,
comma decimals, a running **Balance** column, an FSP-registered footer. The statement parsing
in `elide/statement.py`, the statement-detection scoring in `project.py`, and the
amount spellings in `models.py` all encode assumptions about that format. A statement from a
different bank — or a different kind of source document — will likely need those adjusted, and
the redaction policy in `redact.py` tuned to what *your* claim needs to hide and keep.

That adjustment is the intended workflow. The codebase is deliberately small, typed and
hermetically tested so you can hand it to your own LLM/agent, describe your statement, and have
it adapt the parser and redaction rules to your case. Treat this repo as a verified
foundation — real content-stream redaction, geometry guards, an independent post-build
check — rather than something to run unchanged.

## Running it

```bash
~/Work/elide/run.sh                     # start in the current directory
~/Work/elide/run.sh /path/to/claim      # start somewhere specific
~/Work/elide/run.sh --port 9000         # flags work without a folder
```

You do not need to know the path up front — the first screen is a folder browser. First run
builds `.venv` and installs dependencies. The browser opens at `http://127.0.0.1:57311/`.
Nothing is uploaded anywhere — the server is local and bound to loopback.

On an [Omarchy](https://omarchy.org/) system it opens in an app-mode browser window (via
`omarchy launch webapp`) — no tab strip, no address bar, and nothing added to your app menu.
Everywhere else it uses your default browser. `--no-browser` skips opening anything.

**Closing the window stops the server**, so the terminal you started it from comes back to you
rather than being left holding a port. The last step has a **Close** button that does both at once
— it shuts the wizard and ends the run — and closing the window yourself does the same thing a few
seconds later.

Moving between steps is not closing it, and neither is leaving the tab in the background; a second
tab on the same claim keeps the run going until both are shut. Started with `--no-browser` and
never opened, it stays up as it always did, and Ctrl-C still works throughout.

**Everything written goes into the claim folder you choose**, never into this project
directory. Only the saved-state file sits at the top level; every generated file goes into a
`claim_output/` subfolder, so reopening the claim folder never mistakes a previous run's
output for a source document:

| Path | What it is |
|---|---|
| `claim_project.json` | Saved state — reopen the folder later and pick up where you left off |
| `claim_output/<statement>_redacted.pdf` | The statement alone, redacted, for inspection |
| `claim_output/Claim_Bundle_<folder>.pdf` | The deliverable |
| `claim_output/redaction_report.txt` | What was removed, what was kept, and the verification result |

## The six steps

Structure and detail are separate steps: you settle which pages make up which receipt first,
and only then does the app ask what each one cost — one card per receipt rather than one row
per PDF.

1. **Documents** — pick the claim folder by clicking through it: breadcrumbs, a folder list
   badged with how many source PDFs each holds and whether a claim is already in progress
   there, and chips for folders you have used before. **Browse…** opens your desktop's own
   folder dialog (`zenity`/`qarma`/`kdialog`/`yad`, whichever is installed). You can still type
   or paste a path. Then confirm which PDF is the certified statement — auto-detected by
   filename, falling back to scoring each document's text for statement markers — and mark the
   rest as receipts or ignore them.
2. **Receipts** — separate each PDF into the receipts inside it, by pointing at the pages. A
   source is shown as a strip of page thumbnails, each with its own **↻** rotate, **⚲**
   open-full-size and **✕** ignore buttons; rotating and ignoring take effect at once, no save
   step. **A document starts as one receipt covering every page** — the common case, one PDF
   holding one receipt, needs no clicks at all.

   **Multiple receipts** is the switch under the strip. Turn it on and the document empties: every
   page greys out, because a page belongs to no receipt until you say so. The bar reads
   *Selecting receipt 1* — click the pages that make it up, or press and drag along the strip to
   take a run of them in one go. Clicking a page you already took puts it back. **Done** settles
   that receipt, **+ Add receipt 2** starts the next, and so on until the document is used up.
   **Reset** puts the whole document back to a single receipt.

   **Every page wears a bubble for the receipt it belongs to**, so the split is visible on the
   pages themselves rather than in a column of numbers. **A page can wear two** — that is two till
   slips scanned onto one sheet, and it reaches the bundle once under each receipt. Nothing
   rearranges itself behind you: a receipt holds exactly the pages you picked, and two receipts
   sharing a page or a receipt skipping one in the middle are simply what you clicked.

   **A page you never pick stays grey and marked *Ignored*** — a cover sheet, a duplicate scan,
   the blank reverse of an invoice. It stays in your source document and never reaches the bundle.
   **✕ says the same thing outright** and works whether the document is one receipt or several: it
   takes the page out of the claim even when a receipt still names it.
3. **Amounts** — one card per receipt, with the receipt itself beside the fields: page through
   its own pages with **‹ ›**, rotate it, or click it to read it full-size. Then the amount, a
   supplier label and a note. Amounts found in that receipt's own pages are offered as chips,
   best guess first — the pages are settled by now, so a chip can only ever come off a page
   that receipt actually claims.
4. **Confirm** — for each amount, every statement line that moved exactly that sum is listed.
   You pick the right one. Nothing is auto-accepted, even when only one line matches, because
   statements routinely repeat amounts.
5. **Preview** — all statement pages rendered with the proposed redactions in red and your
   claimed lines outlined in green. Last look before anything is written.
6. **Build** — redacts, assembles, verifies, and reports. Once the bundle exists, **Close** ends
   the whole thing: it shuts the window and stops the server, leaving your finished claim in the
   folder and your terminal back where it was.

## What gets removed

Under the default (strict) policy:

- Unrelated transaction rows are wiped **whole** — date, description and amounts.
- The running **Balance** column is wiped on every row, including your claimed ones.
- The closing balances on the summary page are wiped.

Kept: account holder, account numbers, statement period, column headings, the bank's footer
and its certification stamp — everything that makes the document identifiable as a genuine
certified statement. All pages are retained by default, including fully-blacked ones, so the
statement is visibly unbroken.

Each of these is a checkbox on the preview screen if you want it different.

## How it refuses to get it wrong

Two geometry guards run **before** anything is written, and abort the build if either trips:

- **Bleed** — no redaction bar may touch text on a line you are keeping.
- **Coverage** — every word on a removed row must be inside a bar.

Then the finished file is checked independently, against the output rather than the plan:

- No removed string survives in the extracted text of the statement pages.
- Every kept string *does* survive, which catches over-redaction.
- The raw page content streams carry no literal leftovers.
- No embedded files, scripts or annotations.
- Optional OCR pass renders each page at 200 dpi and reads it back with `tesseract`, catching
  anything still legible — including text baked into an image, which glyph removal cannot touch.

A build that fails verification is reported as failed. It is never quietly handed over.

## Notes

- The leak checks are scoped to the statement's own pages. Receipts legitimately show dates and
  round amounts that also appeared on removed rows; scanning the whole bundle for those would
  flag a correct build as leaking.
- Amount matching is exact `Decimal` comparison, never fuzzy — an approximate match on a claim
  is worse than no match. It understands `R 1 149,80`, `R1,150.00`, `R 228.00` and the trailing
  minus Rivermarch uses for amounts owing.
- Column positions are read from each page's own header row, so statements whose accounts use
  different layouts parse correctly.
- The statement is inserted into the bundle **verbatim** and never re-rendered. Receipts are
  scaled onto A4 portrait so the bundle reads as one document.

## Tests

```bash
.venv/bin/pytest -q
```

100 tests, hermetic — they build a synthetic statement in-process and never touch real claim data.

## Dependencies

`pymupdf` and `flask`. Optional: `tesseract` on `PATH` for the OCR check, and `zenity` (or
`qarma`, `kdialog`, `yad`) for the native folder dialog — without one the **Browse…** button is
simply hidden and the in-page browser does the job.

`pymupdf` is AGPL-3.0. That licence covers PyMuPDF itself, not the code in this repo (see
below). Fine to run and modify privately; if you distribute or host a combined work, mind
PyMuPDF's terms for that component.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, adapt it however you like.
