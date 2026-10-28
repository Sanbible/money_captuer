"""Unit tests สำหรับ pure helpers ใน bot.py (ไม่ต่อ Discord / Sheets / Typhoon)"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import bot
from slip_reader import SlipData


# ---------- parse_amount_input ----------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,234.50", 1234.5),
        ("120", 120.0),
        (" 99.5 ", 99.5),
        ("0.75", 0.75),
        ("12,345,678.9", 12345678.9),
        ("฿1,000", 1000.0),
        ("1000 บาท", 1000.0),
        ("฿ 250.00 บาท", 250.0),
    ],
)
def test_parse_amount_input_accepts_valid(text, expected):
    assert bot.parse_amount_input(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "0", "0.00", "-5", "abc", "1e5", "nan", "inf", "1,23", "1234,567.00",
     "12.345", "1.2.3", "12a", "1 000", ",100", "100,"],
)
def test_parse_amount_input_rejects_invalid(text):
    assert bot.parse_amount_input(text) is None


# ---------- format_baht / format_amount_plain ----------

@pytest.mark.parametrize(
    "amount, expected",
    [(None, "?"), (1234.5, "฿1,234.50"), (0.5, "฿0.50"), (1_000_000, "฿1,000,000.00"), (99.999, "฿100.00")],
)
def test_format_baht(amount, expected):
    assert bot.format_baht(amount) == expected


@pytest.mark.parametrize("amount, expected", [(None, ""), (1234.5, "1234.50"), (20, "20.00")])
def test_format_amount_plain(amount, expected):
    assert bot.format_amount_plain(amount) == expected


def test_format_amount_plain_roundtrips_through_parser():
    assert bot.parse_amount_input(bot.format_amount_plain(1234.5)) == pytest.approx(1234.5)


# ---------- is_image_attachment / guess_mime ----------

@pytest.mark.parametrize(
    "filename, content_type, expected",
    [
        ("slip.png", "image/png", True),
        ("download", "image/jpeg", True),
        ("IMG_0001.JPG", None, True),
        ("a.jpeg", None, True),
        ("a.png", "image/png; charset=binary", True),
        ("photo", "IMAGE/JPG", True),
        ("doc.pdf", "application/pdf", False),
        ("anim.gif", "image/gif", False),
        ("pic.webp", "image/webp", False),
        ("noext", None, False),
        ("", None, False),
    ],
)
def test_is_image_attachment(filename, content_type, expected):
    assert bot.is_image_attachment(filename, content_type) is expected


@pytest.mark.parametrize(
    "filename, content_type, expected",
    [
        ("a.jpg", None, "image/jpeg"),
        ("a.JPEG", None, "image/jpeg"),
        ("a.png", None, "image/png"),
        ("x", "image/jpeg", "image/jpeg"),
        ("x.jpg", "image/png", "image/png"),  # content_type ชนะนามสกุล
        ("x", "image/jpg", "image/jpeg"),
        ("x", None, "image/png"),  # ค่าเริ่มต้น
    ],
)
def test_guess_mime(filename, content_type, expected):
    assert bot.guess_mime(filename, content_type) == expected


# ---------- parse_summary_command ----------

TODAY = date(2026, 10, 26)


@pytest.mark.parametrize(
    "content, expected",
    [
        ("!สรุป", "2026-10"),
        ("!สรุป 2026-09", "2026-09"),
        ("  !สรุป   2025-1 ", "2025-01"),
        ("!สรุป 2024-12", "2024-12"),
    ],
)
def test_parse_summary_command_valid(content, expected):
    assert bot.parse_summary_command(content, TODAY) == expected


def test_parse_summary_command_accepts_datetime_today():
    assert bot.parse_summary_command("!สรุป", datetime(2027, 1, 2, 3, 4)) == "2027-01"


@pytest.mark.parametrize("content", ["", "hello", "สรุป", "!สรุปผล", "!summary", "ดู !สรุป"])
def test_parse_summary_command_not_a_command(content):
    assert bot.parse_summary_command(content, TODAY) is None


@pytest.mark.parametrize(
    "content",
    ["!สรุป 2026-13", "!สรุป 2026-00", "!สรุป abc", "!สรุป 26-10", "!สรุป 2026-10 extra", "!สรุป 2026/10"],
)
def test_parse_summary_command_bad_format_raises(content):
    with pytest.raises(ValueError) as exc:
        bot.parse_summary_command(content, TODAY)
    assert "YYYY-MM" in str(exc.value)


# ---------- timezone ----------

def test_bkk_now_is_utc_plus_7():
    now = bot.bkk_now()
    assert now.utcoffset() == timedelta(hours=7)
    utc_now = datetime.now(timezone.utc)
    assert abs((now - utc_now).total_seconds()) < 5


# ---------- normalize_slip_date / normalize_slip_time / format_slip_datetime ----------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-26", "2026-10-26"),
        ("2569-10-26", "2026-10-26"),  # ปี พ.ศ. หลุดมา -> แปลงเป็น ค.ศ.
        (" 2026-01-05 ", "2026-01-05"),
        (None, None),
        ("", None),
        ("26/10/2026", None),
        ("2026-02-30", None),
        ("null", None),
    ],
)
def test_normalize_slip_date(value, expected):
    assert bot.normalize_slip_date(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [("14:05", "14:05"), ("9:05", "09:05"), ("14:05:33", "14:05"), ("25:00", None),
     ("12:60", None), (None, None), ("", None), ("abc", None)],
)
def test_normalize_slip_time(value, expected):
    assert bot.normalize_slip_time(value) == expected


@pytest.mark.parametrize(
    "d, t, expected",
    [
        ("2026-10-26", "14:05", "2026-10-26 14:05"),
        ("2026-10-26", None, "2026-10-26"),
        (None, None, "ไม่ทราบวันที่"),
        (None, "14:05", "ไม่ทราบวันที่ 14:05"),
    ],
)
def test_format_slip_datetime(d, t, expected):
    assert bot.format_slip_datetime(d, t) == expected


# ---------- SlipState.from_results ----------

def test_slip_state_from_results_with_qr_and_ocr():
    qr = {"api_id": "000001", "bank_code": "006", "trans_ref": "A306e5fe310f649b6", "country": "TH"}
    slip = SlipData(amount=50.0, date="2569-10-26", time="9:05", receiver="  ร้านกาแฟ  ")
    state = bot.SlipState.from_results(author_id=42, slip_url="https://discord.com/x", qr=qr, slip=slip)
    assert state.author_id == 42
    assert state.slip_url == "https://discord.com/x"
    assert state.amount == 50.0
    assert state.date == "2026-10-26"
    assert state.time == "09:05"
    assert state.receiver == "ร้านกาแฟ"
    assert state.bank_code == "006"
    assert state.trans_ref == "A306e5fe310f649b6"
    assert state.note == ""


def test_slip_state_from_results_without_qr_and_failed_ocr():
    state = bot.SlipState.from_results(author_id=1, slip_url="u", qr=None, slip=SlipData(error="ocr failed"))
    assert state.amount is None
    assert state.date is None
    assert state.time is None
    assert state.receiver is None
    assert state.bank_code is None
    assert state.trans_ref is None


def test_slip_state_from_results_ignores_non_positive_amount():
    state = bot.SlipState.from_results(author_id=1, slip_url="u", qr=None, slip=SlipData(amount=0.0))
    assert state.amount is None


# ---------- build_record ----------

NOW = datetime(2026, 10, 26, 18, 30, 5, tzinfo=timezone(timedelta(hours=7)))


def _state(**overrides) -> bot.SlipState:
    base = dict(
        author_id=1, slip_url="https://discord.com/channels/1/2/3", amount=1234.5, date="2026-09-30",
        time="23:59", receiver="ร้าน A", note="ข้าวเที่ยง", bank_code="006", trans_ref="REF123",
    )
    base.update(overrides)
    return bot.SlipState(**base)


def test_build_record_uses_slip_values():
    rec = bot.build_record(_state(), "อาหาร", NOW)
    assert rec.date == "2026-09-30"
    assert rec.time == "23:59"
    assert rec.amount == 1234.5
    assert rec.receiver == "ร้าน A"
    assert rec.category == "อาหาร"
    assert rec.note == "ข้าวเที่ยง"
    assert rec.bank == "กรุงไทย"
    assert rec.trans_ref == "REF123"
    assert rec.recorded_at == "2026-10-26 18:30:05"
    assert rec.discord_link == "https://discord.com/channels/1/2/3"


def test_build_record_defaults_when_slip_has_no_date_or_qr():
    rec = bot.build_record(
        _state(date=None, time=None, receiver=None, note="", bank_code=None, trans_ref=None), "อื่นๆ", NOW
    )
    assert rec.date == "2026-10-26"
    assert rec.time == ""
    assert rec.receiver == ""
    assert rec.bank == ""
    assert rec.trans_ref == ""


def test_build_record_requires_amount():
    with pytest.raises(ValueError):
        bot.build_record(_state(amount=None), "อาหาร", NOW)


# ---------- format_summary ----------

@pytest.mark.parametrize("totals", [None, {}])
def test_format_summary_empty(totals):
    assert bot.format_summary("2026-10", totals) == "ยังไม่มีข้อมูลเดือน 2026-10"


def test_format_summary_lists_categories_and_total():
    text = bot.format_summary("2026-10", {"อาหาร": 120.0, "เดินทาง": 1500.5})
    lines = text.splitlines()
    assert "เดินทาง" in lines[0] and "฿1,500.50" in lines[0]  # มากสุดขึ้นก่อน
    assert "อาหาร" in lines[1] and "฿120.00" in lines[1]
    assert "รวมทั้งเดือน" in lines[-1] and "฿1,620.50" in lines[-1]
