"""A synthetic bank statement, so tests never depend on real claim data."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

ROWS = [
    ("2026-06-03", "Interest Earned at 0.10%", "", "0.36", "3,226.91"),
    ("2026-06-08", "PayShap NORTHWIND TRADING", "", "967.50", "4,194.41"),
    ("2026-06-08", "PayShap CEDARVIEW BODY CORP - INV 20734", "3,967.50", "", "226.91"),
    # Rivermarch marks an amount owing with a TRAILING minus, not a leading one.
    ("2026-07-02", "LUMENNET300142785 PAYLINK", "809.00", "", "582.09-"),
    ("2026-08-26", "NETPAY*Harborlight", "1,322.98", "", "1,905.07-"),
    ("2026-08-28", "QUAYSIDE MARKET", "228.00", "", "2,133.07-"),
    ("2026-08-30", "LUMENNET300489061 PAYLINK", "809.00", "", "2,942.07-"),
]

# Right edges of the three amount columns, matching the real statement layout.
X_DATE, X_DESC = 61.0, 118.0
X_DEBIT_R, X_CREDIT_R, X_BALANCE_R = 412.0, 475.0, 538.0
Y_HEADER, ROW_PITCH, FONT_SIZE = 100.0, 16.5, 9.0


def _right(page, text, right_x, y):
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=FONT_SIZE)
    page.insert_text((right_x - width, y), text, fontname="helv", fontsize=FONT_SIZE)


@pytest.fixture
def statement_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    page.insert_text((X_DATE, 60), "Account holder: Doctor Cornelius", fontname="helv", fontsize=9)
    page.insert_text(
        (X_DATE, 74), "Account type: Transaction Account", fontname="helv", fontsize=9
    )

    page.insert_text((X_DATE, Y_HEADER), "Date", fontname="helv", fontsize=9)
    page.insert_text((X_DESC, Y_HEADER), "Description", fontname="helv", fontsize=9)
    _right(page, "Debit", X_DEBIT_R - 18, Y_HEADER)
    _right(page, "Credit", X_CREDIT_R - 16, Y_HEADER)
    _right(page, "Balance", X_BALANCE_R - 11, Y_HEADER)

    for index, (date, desc, debit, credit, balance) in enumerate(ROWS):
        y = Y_HEADER + ROW_PITCH * (index + 1)
        page.insert_text((X_DATE, y), date, fontname="helv", fontsize=FONT_SIZE)
        page.insert_text((X_DESC, y), desc, fontname="helv", fontsize=FONT_SIZE)
        for value, right_x in ((debit, X_DEBIT_R), (credit, X_CREDIT_R), (balance, X_BALANCE_R)):
            if value:
                _right(page, f"R {value}", right_x, y)

    page.insert_text(
        (X_DATE, 800), "Rivermarch Bank Limited. FSP number 40100.", fontname="helv", fontsize=7
    )

    out = tmp_path / "statement.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def receipt_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Invoice 4469", fontname="helv", fontsize=14)
    page.insert_text((72, 130), "Subtotal   R1,150.42", fontname="helv", fontsize=10)
    page.insert_text((72, 150), "TOTAL      R1,322.98", fontname="helv", fontsize=10)
    out = tmp_path / "receipt.pdf"
    doc.save(out)
    doc.close()
    return out
