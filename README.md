# Claims Processor

Turns a folder of receipts plus a certified bank statement into **one PDF** you can submit,
with every unrelated transaction and every account balance permanently removed from the
statement.

The redaction is real: text is deleted from the PDF content stream, not covered with a black
rectangle that still has selectable text underneath. The pages stay vector PDF — selectable
text, working search, no flattening to images.

## Running it

```bash
~/Work/claims_processor/run.sh                     # start in the current directory
~/Work/claims_processor/run.sh /path/to/claim      # start somewhere specific
~/Work/claims_processor/run.sh --port 9000         # flags work without a folder
```

You do not need to know the path up front — the first screen is a folder browser. First run
builds `.venv` and installs dependencies. The browser opens at `http://127.0.0.1:57311/`.
Nothing is uploaded anywhere — the server is local and bound to loopback.

**Everything written goes into the claim folder you choose**, never into this project
directory:

| File | What it is |
|---|---|
| `claim_project.json` | Saved state — reopen the folder later and pick up where you left off |
| `<statement>_redacted.pdf` | The statement alone, redacted, for inspection |
| `Claim_Bundle_<folder>.pdf` | The deliverable |
| `redaction_report.txt` | What was removed, what was kept, and the verification result |

## The five steps

1. **Documents** — pick the claim folder by clicking through it: breadcrumbs, a folder list
   badged with how many source PDFs each holds and whether a claim is already in progress
   there, and chips for folders you have used before. **Browse…** opens your desktop's own
   folder dialog (`zenity`/`qarma`/`kdialog`/`yad`, whichever is installed). You can still type
   or paste a path. Then confirm which PDF is the certified statement — auto-detected by
   filename, falling back to scoring each document's text for statement markers — and mark the
   rest as receipts or ignore them.
2. **Amounts** — split each document into its receipts. A source PDF is shown as a strip of
   page thumbnails; click one (or its &#9974;) to open it full-size, with next/previous and a
   rotate button of its own — the thumbnail strip updates the moment you rotate, no save step
   either way. Below the strip, one row per receipt: a page range (from/to), the amount, a
   supplier label and a note. Amounts found in that range's own text are offered as chips, best
   guess first. **A source starts with one row spanning every page** — the common case, one PDF
   holding one receipt, needs no extra clicks. Click **+ Split off another receipt** to carve
   out more: a scanned page holding two till slips becomes two rows both pointing at page 1; an
   invoice printed across three pages becomes one row spanning all three. Ranges may overlap
   freely.
3. **Confirm** — for each amount, every statement line that moved exactly that sum is listed.
   You pick the right one. Nothing is auto-accepted, even when only one line matches, because
   statements routinely repeat amounts.
4. **Preview** — all statement pages rendered with the proposed redactions in red and your
   claimed lines outlined in green. Last look before anything is written.
5. **Build** — redacts, assembles, verifies, and reports.

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

61 tests, hermetic — they build a synthetic statement in-process and never touch real claim data.

## Dependencies

`pymupdf` (AGPL-3.0 — fine for private use, worth knowing if you ever redistribute this) and
`flask`. Optional: `tesseract` on `PATH` for the OCR check, and `zenity` (or `qarma`,
`kdialog`, `yad`) for the native folder dialog — without one the **Browse…** button is simply
hidden and the in-page browser does the job.
