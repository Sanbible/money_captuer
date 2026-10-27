"""Google Sheets: หา/สร้างแท็บเดือน, เพิ่มแถวรายจ่าย, เช็กสลิปซ้ำ, สรุปยอดรายเดือน

- 1 ไฟล์ชีต แท็บละเดือน ชื่อ ``YYYY-MM``
- ลำดับแท็บ: แท็บเดือนเรียง **ใหม่สุดอยู่ซ้ายสุด** (newest first) เดือนที่สร้างใหม่จะถูกแทรก
  ให้ลำดับยังเรียงอยู่ (best-effort — ถ้าผู้ใช้ย้ายแท็บเองก็แค่แทรกก่อนแท็บเดือนแรกที่เก่ากว่า)
  แท็บอื่นที่ไม่ใช่เดือน (เช่น Sheet1) ไม่ถูกย้าย; ถ้ายังไม่มีแท็บเดือนเลย แท็บเดือนแรกจะอยู่หน้าสุด
- ทุกเมธอดเป็น blocking — bot.py เรียกผ่าน ``asyncio.to_thread``

ทดสอบกับชีตจริง: ``python sheets.py`` (ต้องมี GOOGLE_SHEET_ID + credentials.json)
"""
from __future__ import annotations

import logging
import math
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from typing import Any, Callable

import gspread
from gspread.exceptions import APIError
from gspread.utils import ValueInputOption, ValueRenderOption, absolute_range_name

logger = logging.getLogger(__name__)

HEADER = ["วันที่", "เวลา", "จำนวนเงิน", "ผู้รับ", "หมวด", "โน้ต", "ธนาคาร", "เลขอ้างอิง", "บันทึกเมื่อ", "ลิงก์ Discord"]
MONTH_TAB_RE = re.compile(r"^\d{4}-\d{2}$")

REF_HEADER = "เลขอ้างอิง"
CATEGORY_HEADER = "หมวด"
AMOUNT_HEADER = "จำนวนเงิน"
UNCATEGORIZED = "ไม่ระบุ"

NEW_TAB_ROWS = 1000  # append จะขยายแถวให้เองถ้าเต็ม
DEFAULT_CACHE_TTL = 60.0  # วินาที — cache รายชื่อแท็บ (กัน process อื่นสร้างแท็บแล้วเราไม่เห็น)

_REF_COL = HEADER.index(REF_HEADER)
_REF_LETTER = chr(ord("A") + _REF_COL)  # "H"
_AMOUNT_COL = HEADER.index(AMOUNT_HEADER)

# ข้อความที่ขึ้นต้นด้วยตัวเหล่านี้ ชีตจะตีความเป็นสูตร (formula injection) → ใส่ ' นำหน้า
# ("'" รวมด้วย เพื่อให้ apostrophe ที่ผู้ใช้พิมพ์เองยังอยู่ ไม่ถูกชีตกินไป)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "'")
# ข้อความที่ USER_ENTERED จะแปลงเป็นตัวเลข/วันที่/เวลา/% /boolean เอง (เช่น "006", "7-11", "3/4")
_AUTO_PARSE_RE = re.compile(
    r"(?=.*\d)[\d\s.,:/%+\-()$฿€£¥]+|\d+(?:\.\d+)?[eE][+-]?\d+|true|false",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}(?::\d{2})?")
_AMOUNT_NOISE_RE = re.compile(r"(?i)thb|baht|บาท|฿|,|\s")
_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?|\.\d+")


@dataclass
class ExpenseRecord:
    date: str  # "YYYY-MM-DD" (bot ใส่วันที่วันนี้ถ้าสลิปไม่มีวันที่)
    time: str  # "HH:MM" หรือ ""
    amount: float
    receiver: str
    category: str
    note: str
    bank: str  # ชื่อธนาคาร เช่น "กรุงไทย" (หรือรหัสดิบ / "")
    trans_ref: str  # "" ถ้าไม่มี QR
    recorded_at: str  # "YYYY-MM-DD HH:MM:SS" เวลาท้องถิ่น
    discord_link: str  # jump URL ของข้อความสลิป


class ExpenseSheet:
    """ห่อ gspread Spreadsheet สำหรับบันทึกรายจ่าย

    ``ExpenseSheet(sheet_id, credentials_file)`` เปิดชีตจริงด้วย service account
    สำหรับเทสต์: ``ExpenseSheet.from_spreadsheet(fake)`` หรือ ``ExpenseSheet("", "", spreadsheet=fake)``
    (object ที่ส่งเข้ามาต้องมี ``worksheets()``, ``add_worksheet()``, ``values_batch_get()``, ``batch_update()``)
    """

    def __init__(
        self,
        sheet_id: str,
        credentials_file: str,
        *,
        spreadsheet: Any | None = None,
        cache_ttl: float = DEFAULT_CACHE_TTL,
    ) -> None:
        if spreadsheet is None:
            client = gspread.service_account(filename=credentials_file)
            spreadsheet = client.open_by_key(sheet_id)
        self._ss = spreadsheet
        self._cache_ttl = cache_ttl
        self._clock: Callable[[], float] = time.monotonic
        self._lock = threading.RLock()
        self._cache: list[Any] | None = None  # worksheets เรียงตามตำแหน่งแท็บ
        self._cache_time = 0.0

    @classmethod
    def from_spreadsheet(cls, spreadsheet: Any, *, cache_ttl: float = DEFAULT_CACHE_TTL) -> ExpenseSheet:
        """สร้างจาก Spreadsheet ที่เปิดไว้แล้ว (หรือ fake ในเทสต์) — ไม่เรียก API ตอนสร้าง"""
        return cls("", "", spreadsheet=spreadsheet, cache_ttl=cache_ttl)

    # ------------------------------------------------------------------ public API

    def is_duplicate(self, trans_ref: str) -> bool:
        """มีเลขอ้างอิงนี้ในคอลัมน์ "เลขอ้างอิง" ของแท็บ YYYY-MM ใดแล้วหรือยัง ("" → False ไม่เรียก API)"""
        ref = _normalize_ref(trans_ref)
        if not ref:
            return False
        try:
            found = self._ref_in_tabs(ref, self._month_sheets())
        except APIError as exc:
            # cache อาจมีแท็บที่ถูกลบไปแล้ว หรือแท็บแคบกว่าคอลัมน์ H → โหลดรายชื่อใหม่แล้วอ่านทั้งแท็บ
            logger.warning("เช็กซ้ำแบบเร็วไม่สำเร็จ (%s) — โหลดรายชื่อแท็บใหม่แล้วลองอีกครั้ง", exc)
            titles = [ws.title for ws in self._month_sheets(force=True)]
            found = self._ref_in_full_tabs(ref, titles)
        if found:
            logger.info("พบเลขอ้างอิงซ้ำ ref=%s", ref)
        return found

    def append_expense(self, rec: ExpenseRecord) -> str:
        """เพิ่ม 1 แถวลงแท็บ ``rec.date[:7]`` (สร้างแท็บ + header ถ้ายังไม่มี) แล้วคืนชื่อแท็บ"""
        tab = _month_of(rec.date)
        row = _build_row(rec)
        ws, created = self._get_or_create(tab)
        try:
            self._append(ws, row, with_header=created)
        except APIError:
            # worksheet ใน cache อาจถูกลบไปแล้ว — ถ้าแท็บยังอยู่จริงแปลว่าเป็น error อื่น ให้โยนต่อ
            if created or self._find_fresh(tab) is not None:
                raise
            logger.warning("แท็บ %s หายไประหว่างบันทึก — สร้างใหม่แล้วลองอีกครั้ง", tab)
            ws, created = self._get_or_create(tab)
            self._append(ws, row, with_header=created)
        logger.info("บันทึกแล้ว แท็บ=%s amount=%.2f ref=%s", tab, row[_AMOUNT_COL], _normalize_ref(rec.trans_ref) or "-")
        return tab

    def month_summary(self, month: str) -> dict[str, float] | None:
        """{หมวด: ยอดรวม} เรียงจากมากไปน้อย; None ถ้าไม่มีแท็บเดือนนั้น ({} ถ้ามีแต่ header)"""
        month = (month or "").strip()
        if not MONTH_TAB_RE.fullmatch(month):
            return None
        ws = self._find(month)
        if ws is None:
            return None
        try:
            rows = ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
        except APIError:
            ws = self._find_fresh(month)
            if ws is None:  # แท็บถูกลบไปแล้ว
                return None
            rows = ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
        return _summarize_rows(rows)

    # ------------------------------------------------------------------ worksheet cache

    def _load(self, force: bool = False) -> tuple[list[Any], bool]:
        """คืน (รายการ worksheet, โหลดใหม่จาก API หรือไม่)"""
        with self._lock:
            expired = self._cache is None or (self._clock() - self._cache_time) >= self._cache_ttl
            fresh = force or expired
            if fresh:
                self._cache = list(self._ss.worksheets())
                self._cache_time = self._clock()
            return list(self._cache or []), fresh

    def _lookup(self, title: str) -> tuple[Any | None, list[Any]]:
        """หาแท็บจาก cache; ไม่เจอ → โหลดรายชื่อแท็บใหม่อีกรอบ (อาจมี process อื่นสร้างไว้แล้ว)"""
        sheets, fresh = self._load()
        ws = _by_title(sheets, title)
        if ws is None and not fresh:
            sheets, _ = self._load(force=True)
            ws = _by_title(sheets, title)
        return ws, sheets

    def _find(self, title: str) -> Any | None:
        return self._lookup(title)[0]

    def _find_fresh(self, title: str) -> Any | None:
        return _by_title(self._load(force=True)[0], title)

    def _month_sheets(self, force: bool = False) -> list[Any]:
        return [ws for ws in self._load(force)[0] if MONTH_TAB_RE.fullmatch(ws.title)]

    def _get_or_create(self, tab: str) -> tuple[Any, bool]:
        """คืน (worksheet, เพิ่งสร้างใหม่หรือไม่)"""
        with self._lock:
            ws, sheets = self._lookup(tab)
            if ws is not None:
                return ws, False
            index = _month_insert_index(tab, [w.title for w in sheets])
            try:
                ws = self._ss.add_worksheet(title=tab, rows=NEW_TAB_ROWS, cols=len(HEADER), index=index)
            except APIError:
                # อาจชนกับอีก process ที่เพิ่งสร้างแท็บชื่อเดียวกัน
                ws = self._find_fresh(tab)
                if ws is None:
                    raise
                return ws, False
            logger.info("สร้างแท็บใหม่ %s (ตำแหน่ง %d)", tab, index)
            if self._cache is not None:
                self._cache.insert(min(index, len(self._cache)), ws)
        self._format_new_tab(ws)
        return ws, True

    def _format_new_tab(self, ws: Any) -> None:
        """header ตัวหนา + freeze แถวแรก + รูปแบบตัวเลขคอลัมน์จำนวนเงิน (best-effort, 1 API call)"""
        sheet_id = ws.id
        body = {
            "requests": [
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": len(HEADER)},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 1,
                                  "startColumnIndex": _AMOUNT_COL, "endColumnIndex": _AMOUNT_COL + 1},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                },
            ]
        }
        try:
            self._ss.batch_update(body)
        except Exception as exc:  # noqa: BLE001 — จัดรูปแบบไม่ได้ไม่ควรทำให้บันทึกล้ม
            logger.warning("จัดรูปแบบ header แท็บ %s ไม่สำเร็จ: %s", getattr(ws, "title", "?"), exc)

    @staticmethod
    def _append(ws: Any, row: list[Any], *, with_header: bool) -> None:
        if with_header:
            # แท็บใหม่: เขียน header + แถวแรกใน request เดียว (ไม่มีทางได้แท็บที่ไม่มี header)
            ws.append_rows([list(HEADER), row], value_input_option=ValueInputOption.user_entered, table_range="A1")
        else:
            ws.append_row(row, value_input_option=ValueInputOption.user_entered, table_range="A1")

    # ------------------------------------------------------------------ duplicate scan

    def _batch_get(self, ranges: list[str]) -> list[list[list[Any]]]:
        resp = self._ss.values_batch_get(ranges) or {}
        out = [vr.get("values", []) for vr in resp.get("valueRanges", [])]
        out += [[] for _ in range(len(ranges) - len(out))]
        return out

    def _ref_in_tabs(self, ref: str, sheets: list[Any]) -> bool:
        """รอบแรก: ขอ header + คอลัมน์ H ของทุกแท็บเดือนใน values_batch_get ครั้งเดียว
        แท็บที่ "เลขอ้างอิง" ไม่ได้อยู่คอลัมน์ H (หรือแท็บแคบ) → อ่านทั้งแท็บอีก 1 request"""
        quick: list[str] = []
        full: list[str] = []
        for ws in sheets:
            cols = _col_count(ws)
            (full if cols is not None and cols <= _REF_COL else quick).append(ws.title)

        if quick:
            ranges: list[str] = []
            for title in quick:
                ranges.append(absolute_range_name(title, "1:1"))
                ranges.append(absolute_range_name(title, f"{_REF_LETTER}:{_REF_LETTER}"))
            results = self._batch_get(ranges)
            for i, title in enumerate(quick):
                header_rows = results[2 * i]
                header = header_rows[0] if header_rows else []
                if _find_col(header, REF_HEADER) != _REF_COL:
                    full.append(title)
                    continue
                col_rows = results[2 * i + 1]
                if _has_col(header, REF_HEADER):
                    col_rows = col_rows[1:]  # ข้ามเซลล์ header
                if _column_has(col_rows, 0, ref):
                    return True

        return self._ref_in_full_tabs(ref, full)

    def _ref_in_full_tabs(self, ref: str, titles: list[str]) -> bool:
        if not titles:
            return False
        for rows in self._batch_get([absolute_range_name(t) for t in titles]):
            header = rows[0] if rows else []
            data = rows[1:] if _has_col(header, REF_HEADER) else rows
            if _column_has(data, _find_col(header, REF_HEADER), ref):
                return True
        return False


# ---------------------------------------------------------------------- helpers


def _by_title(sheets: list[Any], title: str) -> Any | None:
    for ws in sheets:
        if ws.title == title:
            return ws
    return None


def _col_count(ws: Any) -> int | None:
    try:
        n = ws.col_count
    except Exception:  # noqa: BLE001
        return None
    return n if isinstance(n, int) else None


def _month_insert_index(new_title: str, titles: list[str]) -> int:
    """ตำแหน่งแทรกแท็บเดือนใหม่ให้แท็บเดือนเรียงใหม่สุดก่อน (newest first)"""
    months = [(i, t) for i, t in enumerate(titles) if MONTH_TAB_RE.fullmatch(t)]
    if not months:
        return 0
    for i, title in months:
        if title < new_title:  # YYYY-MM เทียบแบบ string ได้
            return i
    return months[-1][0] + 1


def _month_of(date_str: Any) -> str:
    s = date_str.strip() if isinstance(date_str, str) else ""
    if not _DATE_RE.fullmatch(s):
        raise ValueError(f"วันที่ต้องเป็นรูปแบบ YYYY-MM-DD (ได้ {date_str!r})")
    try:
        _date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"วันที่ไม่ถูกต้อง: {date_str!r}") from None
    return s[:7]


def _as_text(value: Any) -> str:
    """บังคับให้เป็นข้อความในชีต: กัน formula injection และกันชีตแปลง "006"/"7-11" เป็นตัวเลข/วันที่"""
    s = "" if value is None else str(value).strip()
    if not s:
        return ""
    if s.startswith(_FORMULA_PREFIXES) or _AUTO_PARSE_RE.fullmatch(s):
        return "'" + s
    return s


def _typed_or_text(value: Any, pattern: re.Pattern[str]) -> str:
    """ค่าที่ตรงรูปแบบ (เวลา/วันเวลา) ปล่อยให้ชีตแปลงเป็นชนิดจริง ที่เหลือบังคับเป็นข้อความ"""
    s = "" if value is None else str(value).strip()
    return s if pattern.fullmatch(s) else _as_text(s)


def _normalize_ref(value: Any) -> str:
    s = "" if value is None else str(value).strip()
    if s.startswith("'"):
        s = s[1:].strip()
    return s


def _build_row(rec: ExpenseRecord) -> list[Any]:
    try:
        amount = float(rec.amount)
    except (TypeError, ValueError):
        raise ValueError(f"จำนวนเงินไม่ถูกต้อง: {rec.amount!r}") from None
    if not math.isfinite(amount):
        raise ValueError(f"จำนวนเงินไม่ถูกต้อง: {rec.amount!r}")
    ref = _normalize_ref(rec.trans_ref)
    cells = {
        "วันที่": rec.date.strip(),  # ตรวจรูปแบบแล้วใน _month_of → ชีตแปลงเป็นวันที่
        "เวลา": _typed_or_text(rec.time, _TIME_RE),
        "จำนวนเงิน": round(amount, 2),  # ส่งเป็นตัวเลข ไม่ใช่ "1,234.00"
        "ผู้รับ": _as_text(rec.receiver),
        "หมวด": _as_text(rec.category),
        "โน้ต": _as_text(rec.note),
        "ธนาคาร": _as_text(rec.bank),
        "เลขอ้างอิง": f"'{ref}" if ref else "",  # บังคับเป็นข้อความเสมอ (ใช้กันซ้ำ ห้ามถูกแปลงเป็นตัวเลข)
        "บันทึกเมื่อ": _typed_or_text(rec.recorded_at, _DATETIME_RE),
        "ลิงก์ Discord": _as_text(rec.discord_link),
    }
    return [cells[h] for h in HEADER]


def _clean_header(header: list[Any]) -> list[str]:
    return ["" if c is None else str(c).strip() for c in header]


def _has_col(header: list[Any], name: str) -> bool:
    return name in _clean_header(header)


def _find_col(header: list[Any], name: str) -> int:
    """index ของคอลัมน์ตามชื่อใน header; ไม่เจอ → ตำแหน่งตาม HEADER มาตรฐาน"""
    cleaned = _clean_header(header)
    return cleaned.index(name) if name in cleaned else HEADER.index(name)


def _column_has(rows: list[list[Any]], idx: int, ref: str) -> bool:
    return any(idx < len(row) and _normalize_ref(row[idx]) == ref for row in rows)


def _parse_amount(value: Any) -> float | None:
    """แปลงค่าในเซลล์จำนวนเงินเป็น float; อ่านไม่ออก → None"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    s = str(value).strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):  # รูปแบบบัญชี (1,234.00)
        negative, s = True, s[1:-1]
    s = _AMOUNT_NOISE_RE.sub("", s)
    if s and s[0] in "+-":
        negative ^= s[0] == "-"
        s = s[1:]
    if not _AMOUNT_RE.fullmatch(s):
        return None
    amount = float(s)
    return -amount if negative else amount


def _summarize_rows(rows: list[list[Any]]) -> dict[str, float]:
    header = rows[0] if rows else []
    has_header = _has_col(header, CATEGORY_HEADER) or _has_col(header, AMOUNT_HEADER)
    cat_i = _find_col(header, CATEGORY_HEADER)
    amt_i = _find_col(header, AMOUNT_HEADER)

    totals: dict[str, float] = {}
    for row in rows[1:] if has_header else rows:
        amount = _parse_amount(row[amt_i]) if amt_i < len(row) else None
        if amount is None:
            continue
        raw_cat = row[cat_i] if cat_i < len(row) else ""
        category = ("" if raw_cat is None else str(raw_cat).strip()) or UNCATEGORIZED
        totals[category] = totals.get(category, 0.0) + amount

    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return {cat: round(total, 2) for cat, total in ordered}


# ---------------------------------------------------------------------- smoke test


def _smoke_test() -> int:
    """เพิ่มแถวทดสอบลงแท็บ 2000-01 ของชีตจริง แล้วเช็กซ้ำ + สรุป"""
    from config import ConfigError, load_config

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    try:
        cfg = load_config(required=["GOOGLE_SHEET_ID", "GOOGLE_CREDENTIALS_FILE"])
    except ConfigError as exc:
        print(f"ตั้งค่าไม่ครบ: {exc}")
        return 1

    sheet = ExpenseSheet(cfg.google_sheet_id, cfg.google_credentials_file)
    now = datetime.now()
    ref = "SMOKETEST" + now.strftime("%Y%m%d%H%M%S")
    rec = ExpenseRecord(
        date="2000-01-15",
        time="12:34",
        amount=1234.5,
        receiver="ร้านทดสอบ (python sheets.py)",
        category=cfg.categories[0],
        note="แถวทดสอบ ลบได้",
        bank="กรุงไทย",
        trans_ref=ref,
        recorded_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        discord_link="",
    )

    print(f"is_duplicate ก่อนบันทึก = {sheet.is_duplicate(ref)}  (ควรเป็น False)")
    tab = sheet.append_expense(rec)
    print(f"เพิ่มแถวทดสอบลงแท็บ: {tab}")
    dup = sheet.is_duplicate(ref)
    print(f"is_duplicate({ref!r}) = {dup}  (ควรเป็น True)")
    print(f"month_summary('2000-01') = {sheet.month_summary('2000-01')}")
    print('เสร็จแล้ว — ลบแท็บ "2000-01" ในชีตทิ้งได้เลย (เป็นแท็บทดสอบ)')
    return 0 if dup else 1


if __name__ == "__main__":
    sys.exit(_smoke_test())
