"""ทดสอบ regex สำรอง fallback_amount() — ใช้เมื่อ LLM ล้มเหลว (ข้อความเข้ามาเป็นผล OCR Markdown/HTML)"""
from __future__ import annotations

import pytest

from slip_reader import fallback_amount


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # --- รูปแบบพื้นฐานจากสเปก ---
        ("จำนวนเงิน 1,234.50 บาท", 1234.5),
        ("1,234.50 บาท", 1234.5),
        ("฿ 99.00", 99.0),
        ("฿99.00", 99.0),
        ("จำนวนเงิน 250.00", 250.0),
        ("THB 1,000.00", 1000.0),
        ("THB1,000.00", 1000.0),
        ("1,000.00 THB", 1000.0),
        ("500 บาท", 500.0),                    # ไม่มีทศนิยม
        ("จำนวนเงิน: 1500", 1500.0),           # จำนวนเต็มที่มี keyword
        ("ยอดชำระ 12,345,678.90 บาท", 12345678.9),
        ("-350.00 บาท", 350.0),                # บางแอปแสดงยอดติดลบ
        ("จำนวน ๑,๒๓๔.๕๐ บาท", 1234.5),       # เลขไทย
    ],
)
def test_basic_amount_formats(text: str, expected: float) -> None:
    assert fallback_amount(text) == pytest.approx(expected)


def test_returns_float() -> None:
    result = fallback_amount("500 บาท")
    assert isinstance(result, float)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "โอนเงินสำเร็จ",
        "ค่าธรรมเนียม 0.00 บาท",                 # มีแต่ค่าธรรมเนียม → ไม่ใช่ยอดโอน
        "ค่าธรรมเนียม 25.00 บาท",
        "Fee 10.00 THB",
        "ยอดเงินคงเหลือ 15,000.00 บาท",           # ยอดคงเหลือไม่ใช่ยอดโอน
        "26 ต.ค. 69 14:30 น.\nรหัสอ้างอิง 015299143012ABC\nxxx-x-x1234-x",  # วันที่/เวลา/เลขบัญชี
        "เวลา 14.30 น.",                          # เวลาแบบจุดไม่ใช่ยอดเงิน
        "วันที่ 26/10/2569",
        "0.00 บาท",                               # ยอด 0 ไม่ใช่การโอนจริง
        "เลขที่รายการ 2023102614301234567",
    ],
)
def test_no_amount(text: str) -> None:
    assert fallback_amount(text) is None


def test_fee_line_before_amount_does_not_win() -> None:
    text = "ค่าธรรมเนียม 0.00 บาท\nจำนวน 350.00 บาท"
    assert fallback_amount(text) == pytest.approx(350.0)


def test_nonzero_fee_after_amount_does_not_win() -> None:
    text = "จำนวนเงิน 1,500.00 บาท\nค่าธรรมเนียม 25.00 บาท"
    assert fallback_amount(text) == pytest.approx(1500.0)


def test_nonzero_fee_before_unlabelled_amount_does_not_win() -> None:
    text = "ค่าธรรมเนียม 10.00 บาท\n200.00 บาท"
    assert fallback_amount(text) == pytest.approx(200.0)


def test_fee_and_amount_on_same_line() -> None:
    text = "26 ต.ค. 69, 14.30 น. จำนวน 500.00 บาท ค่าธรรมเนียม 0.00 บาท"
    assert fallback_amount(text) == pytest.approx(500.0)


def test_fee_amount_label_is_still_a_fee() -> None:
    text = "Fee Amount 15.00\nTransfer Amount 300.00"
    assert fallback_amount(text) == pytest.approx(300.0)


def test_balance_is_ignored() -> None:
    text = "ยอดเงินคงเหลือ 15,000.00 บาท\nจำนวนเงิน 120.00 บาท"
    assert fallback_amount(text) == pytest.approx(120.0)
    text_en = "Available balance THB 9,999.00\nAmount THB 1,000.00"
    assert fallback_amount(text_en) == pytest.approx(1000.0)


def test_keyword_amount_beats_earlier_bare_number() -> None:
    text = "เลขที่อ้างอิง 2023102612345678\n99.00\nจำนวนเงิน 45.00 บาท"
    assert fallback_amount(text) == pytest.approx(45.0)


def test_currency_amount_beats_item_count_with_keyword() -> None:
    # "จำนวน 1 รายการ" เป็นจำนวนรายการ ไม่ใช่ยอดเงิน
    text = "จำนวน 1 รายการ\n500.00 บาท"
    assert fallback_amount(text) == pytest.approx(500.0)


def test_label_on_previous_line() -> None:
    text = "Amount\n1,234.00 THB\nFee\n15.00 THB"
    assert fallback_amount(text) == pytest.approx(1234.0)


def test_markdown_table_cells() -> None:
    text = (
        "| รายการ | ค่า |\n"
        "|---|---|\n"
        "| ค่าธรรมเนียม | 0.00 บาท |\n"
        "| จำนวนเงิน | 2,500.00 บาท |\n"
    )
    assert fallback_amount(text) == pytest.approx(2500.0)


def test_markdown_table_header_label() -> None:
    text = (
        "| รายการ | จำนวนเงิน (บาท) |\n"
        "| --- | --- |\n"
        "| โอนเงิน | 780.00 |\n"
    )
    assert fallback_amount(text) == pytest.approx(780.0)


def test_html_table_from_typhoon_ocr_v15() -> None:
    # Typhoon OCR v1.5 แสดงตารางเป็น HTML <table>
    text = (
        "<table><tr><td>ค่าธรรมเนียม:</td><td>5.00&nbsp;บาท</td></tr>"
        "<tr><td>จำนวน:</td><td>780.00 บาท</td></tr></table>"
    )
    assert fallback_amount(text) == pytest.approx(780.0)


def test_figure_description_is_ignored_when_slip_text_has_amount() -> None:
    text = (
        "<figure>\nภาพโลโก้ธนาคาร มีข้อความ 10.00 บาท\n</figure>\n"
        "โอนเงินสำเร็จ\n150.00 บาท"
    )
    assert fallback_amount(text) == pytest.approx(150.0)


def test_figure_used_as_last_resort() -> None:
    text = "<figure>\nภาพสลิปโอนเงิน จำนวนเงิน 320.00 บาท\n</figure>"
    assert fallback_amount(text) == pytest.approx(320.0)


def test_realistic_kplus_style_slip() -> None:
    text = """โอนเงินสำเร็จ
26 ต.ค. 69 14:30 น.

นาย ทดสอบ ใจดี
ธ.กสิกรไทย
xxx-x-x1234-x

ร้าน กาแฟดี
ธ.กรุงไทย
xxx-x-x5678-x

เลขที่รายการ:
015299143012ABC1234

จำนวน:
85.00 บาท

ค่าธรรมเนียม:
0.00 บาท
"""
    assert fallback_amount(text) == pytest.approx(85.0)
