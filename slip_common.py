"""helper กลางที่บอท Discord (bot.py) และเว็บ (web/) ใช้ร่วมกัน — pure functions ไม่ต่อ network"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BKK_UTC_OFFSET = timedelta(hours=7)

MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")  # "YYYY-MM"


def load_bkk_tz() -> tzinfo:
    """เขตเวลาไทย — Windows มักไม่มี tzdata จึง fallback เป็น UTC+7 คงที่ (ไทยไม่มี DST)"""
    try:
        return ZoneInfo("Asia/Bangkok")
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(BKK_UTC_OFFSET, "+07")


BKK_TZ = load_bkk_tz()


def bkk_now() -> datetime:
    """เวลาปัจจุบันตามเวลาไทย (timezone-aware)"""
    return datetime.now(BKK_TZ)


_AMOUNT_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?")


def parse_amount_input(text: str) -> float | None:
    """แปลงยอดที่ผู้ใช้พิมพ์ เช่น "1,234.50", "฿120", "99 บาท" -> float

    คืน None ถ้ารูปแบบไม่ถูก หรือไม่ใช่ตัวเลขบวก (ทศนิยมไม่เกิน 2 ตำแหน่ง, คอมมาต้องคั่นหลักพันถูกต้อง)
    """
    s = (text or "").strip().removeprefix("฿").removesuffix("บาท").strip()
    if not _AMOUNT_RE.fullmatch(s):
        return None
    value = float(s.replace(",", ""))
    return round(value, 2) if value > 0 else None


_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?")


def normalize_slip_date(value: str | None) -> str | None:
    """ตรวจวันที่จาก LLM -> "YYYY-MM-DD" (ค.ศ.) ; ถ้าปีหลุดมาเป็น พ.ศ. ให้ลบ 543 ; ไม่ถูกต้อง -> None"""
    if not isinstance(value, str):
        return None
    match = _DATE_RE.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day = (int(g) for g in match.groups())
    if year >= 2400:  # ปี พ.ศ.
        year -= 543
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_slip_time(value: str | None) -> str | None:
    """ตรวจเวลาจาก LLM -> "HH:MM" ; ไม่ถูกต้อง -> None"""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.fullmatch(value.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def normalize_ref(value: Any) -> str:
    """เลขอ้างอิงธุรกรรม: ตัดช่องว่างและ ' นำหน้า (ค่าที่เคยบังคับเป็นข้อความใน Google Sheets)"""
    s = "" if value is None else str(value).strip()
    if s.startswith("'"):
        s = s[1:].strip()
    return s
