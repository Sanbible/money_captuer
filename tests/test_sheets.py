"""Unit tests for sheets.py — ใช้ fake Spreadsheet/Worksheet เขียนเอง (ไม่มี network / credentials)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import gspread
import pytest

import sheets
from sheets import HEADER, MONTH_TAB_RE, ExpenseRecord, ExpenseSheet

REF_COL = HEADER.index("เลขอ้างอิง")
AMOUNT_COL = HEADER.index("จำนวนเงิน")


# --- fakes ---------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, code: int, message: str) -> None:
        self.status_code = code
        self.text = message

    def json(self) -> dict:
        return {"error": {"code": self.status_code, "message": self.text, "status": "INVALID_ARGUMENT"}}


def api_error(message: str, code: int = 400) -> gspread.exceptions.APIError:
    return gspread.exceptions.APIError(_FakeResponse(code, message))


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _trim_row(row: list) -> list:
    row = list(row)
    while row and (row[-1] is None or row[-1] == ""):
        row.pop()
    return row


def _trim_rows(rows: list[list]) -> list[list]:
    rows = [_trim_row(r) for r in rows]
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _formatted(cell: Any) -> Any:
    return cell if isinstance(cell, str) else str(cell)


class FakeWorksheet:
    def __init__(self, ss: "FakeSpreadsheet", title: str, sheet_id: int, rows: list[list] | None, cols: int) -> None:
        self._ss = ss
        self.title = title
        self.id = sheet_id
        self.col_count = cols
        self.rows: list[list] = [list(r) for r in (rows or [])]
        self.append_calls: list[dict] = []
        self.render_options: list[Any] = []
        self.deleted = False

    def _check(self) -> None:
        if self.deleted:
            raise api_error(f"Unable to parse range: '{self.title}'")

    # gspread-like API
    def append_rows(self, values, value_input_option="RAW", insert_data_option=None,
                    table_range=None, include_values_in_response=None):
        self._check()
        self._ss.calls["append"] += 1
        values = [list(v) for v in values]
        self.append_calls.append(
            {"values": values, "value_input_option": value_input_option, "table_range": table_range}
        )
        for v in values:
            # จำลอง USER_ENTERED: apostrophe นำหน้า = บังคับเป็นข้อความ และไม่ถูกเก็บเป็นค่า
            self.rows.append(
                [c[1:] if value_input_option == "USER_ENTERED" and isinstance(c, str) and c.startswith("'") else c
                 for c in v]
            )
        return {"updates": {}}

    def append_row(self, values, value_input_option="RAW", insert_data_option=None,
                   table_range=None, include_values_in_response=False):
        return self.append_rows([values], value_input_option=value_input_option,
                                insert_data_option=insert_data_option, table_range=table_range)

    def update(self, values=None, range_name=None, **kwargs):
        self._check()
        self._ss.calls["update"] += 1
        assert range_name in (None, "A1", "A1:J1"), range_name
        values = [list(v) for v in values]
        for i, v in enumerate(values):
            if i < len(self.rows):
                self.rows[i] = v
            else:
                self.rows.append(v)
        return {}

    def get_all_values(self, range_name=None, major_dimension=None, value_render_option=None, **kwargs):
        self._check()
        self._ss.calls["get_all_values"] += 1
        self.render_options.append(value_render_option)
        return [list(r) for r in self.rows]

    def get_values(self, *args, **kwargs):
        return self.get_all_values(*args, **kwargs)

    def col_values(self, col, value_render_option=None):
        self._check()
        self._ss.calls["col_values"] += 1
        return _trim_row([r[col - 1] if col - 1 < len(r) else "" for r in self.rows])


class FakeSpreadsheet:
    """จำลอง gspread.Spreadsheet เท่าที่ sheets.py ใช้ + นับจำนวนครั้งที่เรียก API"""

    def __init__(self, tabs: dict[str, list[list]] | None = None) -> None:
        self.calls: Counter = Counter()
        self._sheets: list[FakeWorksheet] = []
        self._next_id = 100
        self.add_worksheet_args: list[dict] = []
        self.batch_update_bodies: list[dict] = []
        self.batch_update_error: Exception | None = None
        self.batch_get_ranges: list[list[str]] = []
        for title, rows in (tabs or {}).items():
            self.add_tab(title, rows)

    # --- test helpers (ไม่นับเป็น API call) ---
    def add_tab(self, title: str, rows: list[list] | None = None, cols: int = 26,
                index: int | None = None) -> FakeWorksheet:
        ws = FakeWorksheet(self, title, self._next_id, rows, cols)
        self._next_id += 1
        if index is None or index >= len(self._sheets):
            self._sheets.append(ws)
        else:
            self._sheets.insert(index, ws)
        return ws

    def remove_tab(self, title: str) -> None:
        ws = self.tab(title)
        ws.deleted = True
        self._sheets.remove(ws)

    def tab(self, title: str) -> FakeWorksheet:
        for ws in self._sheets:
            if ws.title == title:
                return ws
        raise KeyError(title)

    def titles(self) -> list[str]:
        return [ws.title for ws in self._sheets]

    def total_calls(self) -> int:
        return sum(self.calls.values())

    # --- gspread-like API ---
    def worksheets(self, exclude_hidden: bool = False) -> list[FakeWorksheet]:
        self.calls["worksheets"] += 1
        return list(self._sheets)

    def worksheet(self, title: str) -> FakeWorksheet:
        self.calls["worksheet"] += 1
        for ws in self._sheets:
            if ws.title == title:
                return ws
        raise gspread.exceptions.WorksheetNotFound(title)

    def add_worksheet(self, title: str, rows: int, cols: int, index: int | None = None) -> FakeWorksheet:
        self.calls["add_worksheet"] += 1
        if title in self.titles():
            raise api_error(f'A sheet with the name "{title}" already exists.')
        self.add_worksheet_args.append({"title": title, "rows": rows, "cols": cols, "index": index})
        return self.add_tab(title, [], cols, index)

    def batch_update(self, body: dict) -> dict:
        self.calls["batch_update"] += 1
        if self.batch_update_error is not None:
            raise self.batch_update_error
        self.batch_update_bodies.append(body)
        return {"replies": []}

    def values_batch_get(self, ranges: list[str], params: dict | None = None) -> dict:
        self.calls["values_batch_get"] += 1
        self.batch_get_ranges.append(list(ranges))
        out = []
        for r in ranges:
            vals = self._get_range(r)
            vr = {"range": r, "majorDimension": "ROWS"}
            if vals:
                vr["values"] = vals
            out.append(vr)
        return {"spreadsheetId": "fake", "valueRanges": out}

    def _get_range(self, a1: str) -> list[list]:
        m = re.fullmatch(r"'((?:[^']|'')+)'(?:!(.+))?", a1)
        if not m:
            raise api_error(f"Unable to parse range: {a1}")
        title = m.group(1).replace("''", "'")
        sub = m.group(2)
        try:
            ws = self.tab(title)
        except KeyError:
            raise api_error(f"Unable to parse range: {a1}") from None
        rows = [[_formatted(c) for c in r] for r in ws.rows]
        if sub is None:
            return _trim_rows(rows)
        rm = re.fullmatch(r"(\d+):(\d+)", sub)
        if rm:
            return _trim_rows(rows[int(rm.group(1)) - 1:int(rm.group(2))])
        cm = re.fullmatch(r"([A-Z]+):([A-Z]+)", sub)
        if cm:
            c1, c2 = _col_index(cm.group(1)), _col_index(cm.group(2))
            if c2 >= ws.col_count:
                raise api_error(f"Range ({a1}) exceeds grid limits. Max columns: {ws.col_count}")
            return _trim_rows([r[c1:c2 + 1] for r in rows])
        raise AssertionError(f"fake doesn't support range {a1!r}")


# --- helpers ---------------------------------------------------------------------


def make_record(**overrides: Any) -> ExpenseRecord:
    base = dict(
        date="2026-10-26",
        time="14:05",
        amount=1234.5,
        receiver="ร้านกาแฟดี",
        category="เครื่องดื่ม/กาแฟ",
        note="ลาเต้เย็น",
        bank="กรุงไทย",
        trans_ref="A306e5fe310f649b6",
        recorded_at="2026-10-26 14:06:00",
        discord_link="https://discord.com/channels/1/2/3",
    )
    base.update(overrides)
    return ExpenseRecord(**base)


def data_row(ref: str = "", category: str = "อาหาร", amount: Any = 100.0, date: str = "2026-10-01") -> list:
    row = [""] * len(HEADER)
    row[HEADER.index("วันที่")] = date
    row[HEADER.index("จำนวนเงิน")] = amount
    row[HEADER.index("หมวด")] = category
    row[REF_COL] = ref
    return row


def last_sent_row(ws: FakeWorksheet) -> list:
    return ws.append_calls[-1]["values"][-1]


def new_sheet(tabs: dict[str, list[list]] | None = None, **kwargs: Any) -> tuple[ExpenseSheet, FakeSpreadsheet]:
    fake = FakeSpreadsheet(tabs)
    return ExpenseSheet.from_spreadsheet(fake, **kwargs), fake


# --- constants / constructor ---------------------------------------------------------


def test_header_and_month_regex():
    assert HEADER == ["วันที่", "เวลา", "จำนวนเงิน", "ผู้รับ", "หมวด", "โน้ต", "ธนาคาร",
                      "เลขอ้างอิง", "บันทึกเมื่อ", "ลิงก์ Discord"]
    assert MONTH_TAB_RE.match("2026-10")
    for bad in ("Sheet1", "2026-1", "2026-10x", "x2026-10", "2026/10", ""):
        assert not MONTH_TAB_RE.match(bad), bad


def test_constructor_opens_sheet_with_service_account(monkeypatch):
    fake = FakeSpreadsheet({"2026-10": [HEADER, data_row("R1")]})
    seen: dict[str, Any] = {}

    class FakeClient:
        def open_by_key(self, key):
            seen["key"] = key
            return fake

    def fake_service_account(filename, **kwargs):
        seen["filename"] = filename
        return FakeClient()

    monkeypatch.setattr(sheets.gspread, "service_account", fake_service_account)
    s = ExpenseSheet("SHEET123", r"C:\secret\credentials.json")
    assert seen == {"key": "SHEET123", "filename": r"C:\secret\credentials.json"}
    assert s.is_duplicate("R1") is True


def test_from_spreadsheet_makes_no_api_calls_on_construction():
    _, fake = new_sheet({"2026-10": [HEADER]})
    assert fake.total_calls() == 0


# --- append_expense ------------------------------------------------------------------


def test_append_creates_new_month_tab_with_header():
    s, fake = new_sheet({"Sheet1": []})
    tab = s.append_expense(make_record(date="2026-10-26"))
    assert tab == "2026-10"
    assert "2026-10" in fake.titles()
    args = fake.add_worksheet_args[-1]
    assert args["title"] == "2026-10"
    assert args["cols"] == len(HEADER)
    assert 100 <= args["rows"] <= 5000
    ws = fake.tab("2026-10")
    assert ws.rows[0] == HEADER
    assert len(ws.rows) == 2
    assert ws.rows[1][AMOUNT_COL] == 1234.5


def test_append_new_tab_formats_header_best_effort():
    s, fake = new_sheet({"Sheet1": []})
    s.append_expense(make_record())
    assert fake.batch_update_bodies, "ควรพยายามทำ header ตัวหนา + freeze"
    body_text = repr(fake.batch_update_bodies)
    assert "bold" in body_text
    assert "frozenRowCount" in body_text
    sheet_id = fake.tab("2026-10").id
    assert str(sheet_id) in body_text


def test_append_still_succeeds_when_formatting_fails():
    s, fake = new_sheet({"Sheet1": []})
    fake.batch_update_error = api_error("formatting not allowed", 403)
    assert s.append_expense(make_record()) == "2026-10"
    ws = fake.tab("2026-10")
    assert ws.rows[0] == HEADER
    assert len(ws.rows) == 2


def test_append_to_existing_tab_does_not_recreate_or_rewrite_header():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("OLD")]})
    assert s.append_expense(make_record(trans_ref="NEW1")) == "2026-10"
    assert fake.calls["add_worksheet"] == 0
    ws = fake.tab("2026-10")
    assert len(ws.rows) == 3
    assert ws.rows[0] == HEADER
    assert ws.rows[2][REF_COL] == "NEW1"
    # header ไม่ถูกส่งซ้ำ
    assert all(HEADER not in call["values"] for call in ws.append_calls)


def test_append_uses_user_entered_and_header_column_order():
    s, fake = new_sheet({"2026-10": [HEADER]})
    s.append_expense(make_record())
    ws = fake.tab("2026-10")
    call = ws.append_calls[-1]
    assert call["value_input_option"] == "USER_ENTERED"
    sent = last_sent_row(ws)
    assert len(sent) == len(HEADER)
    by_name = dict(zip(HEADER, sent))
    assert by_name["วันที่"] == "2026-10-26"
    assert by_name["เวลา"] == "14:05"
    assert by_name["จำนวนเงิน"] == 1234.5
    assert by_name["ผู้รับ"] == "ร้านกาแฟดี"
    assert by_name["หมวด"] == "เครื่องดื่ม/กาแฟ"
    assert by_name["โน้ต"] == "ลาเต้เย็น"
    assert by_name["ธนาคาร"] == "กรุงไทย"
    # เลขอ้างอิงถูกบังคับเป็นข้อความเสมอ (กันชีตแปลงเป็นตัวเลข/วันที่)
    assert by_name["เลขอ้างอิง"] == "'A306e5fe310f649b6"
    assert by_name["บันทึกเมื่อ"] == "2026-10-26 14:06:00"
    assert by_name["ลิงก์ Discord"] == "https://discord.com/channels/1/2/3"
    # ค่าที่ชีตเก็บจริง (apostrophe ไม่ถูกเก็บ)
    assert ws.rows[-1][REF_COL] == "A306e5fe310f649b6"


@pytest.mark.parametrize("amount", [1234.5, 100, 0.1 + 0.2])
def test_amount_written_as_number_not_string(amount):
    s, fake = new_sheet({"2026-10": [HEADER]})
    s.append_expense(make_record(amount=amount))
    sent = last_sent_row(fake.tab("2026-10"))[AMOUNT_COL]
    assert isinstance(sent, (int, float)) and not isinstance(sent, bool)
    assert sent == pytest.approx(round(amount, 2))


def test_empty_trans_ref_and_time_become_empty_cells():
    s, fake = new_sheet({"2026-10": [HEADER]})
    s.append_expense(make_record(trans_ref="", time=""))
    sent = last_sent_row(fake.tab("2026-10"))
    assert sent[REF_COL] == ""
    assert sent[HEADER.index("เวลา")] == ""


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("receiver", '=HYPERLINK("http://evil","x")', '\'=HYPERLINK("http://evil","x")'),
        ("receiver", "+66812345678", "'+66812345678"),
        ("note", "-ค่าส่ง", "'-ค่าส่ง"),
        ("note", "@mention", "'@mention"),
        ("category", "=1+1", "'=1+1"),
        ("category", "@cat", "'@cat"),
        ("note", "'quoted", "''quoted"),
        # ข้อความที่ชีตจะแปลงเป็นตัวเลข/วันที่เอง → บังคับเป็นข้อความ
        ("receiver", "7-11", "'7-11"),
        ("bank", "006", "'006"),
        # ข้อความปกติไม่แตะ
        ("receiver", "ร้าน 7-Eleven", "ร้าน 7-Eleven"),
        ("note", "กาแฟ 2 แก้ว", "กาแฟ 2 แก้ว"),
        ("category", "อาหาร", "อาหาร"),
    ],
)
def test_text_fields_escaped_against_formula_injection(field, value, expected):
    s, fake = new_sheet({"2026-10": [HEADER]})
    s.append_expense(make_record(**{field: value}))
    col = {"receiver": "ผู้รับ", "note": "โน้ต", "category": "หมวด", "bank": "ธนาคาร"}[field]
    sent = last_sent_row(fake.tab("2026-10"))
    assert sent[HEADER.index(col)] == expected


@pytest.mark.parametrize("bad_date", ["", "26/10/2026", "2026/10/26", "abc"])
def test_append_rejects_malformed_date(bad_date):
    s, fake = new_sheet({"2026-10": [HEADER]})
    with pytest.raises(ValueError):
        s.append_expense(make_record(date=bad_date))
    assert fake.calls["append"] == 0


def test_month_tabs_kept_sorted_newest_first():
    s, fake = new_sheet({"Sheet1": [], "2026-10": [HEADER], "2026-08": [HEADER]})
    s.append_expense(make_record(date="2026-09-15"))
    assert fake.titles() == ["Sheet1", "2026-10", "2026-09", "2026-08"]
    s.append_expense(make_record(date="2026-11-01"))
    assert fake.titles() == ["Sheet1", "2026-11", "2026-10", "2026-09", "2026-08"]
    s.append_expense(make_record(date="2025-12-31"))
    assert fake.titles() == ["Sheet1", "2026-11", "2026-10", "2026-09", "2026-08", "2025-12"]


def test_first_month_tab_goes_to_front():
    s, fake = new_sheet({"Sheet1": []})
    s.append_expense(make_record(date="2026-10-01"))
    assert fake.titles() == ["2026-10", "Sheet1"]


def test_append_finds_tab_created_by_another_process_after_cache_warm():
    s, fake = new_sheet({"2026-10": [HEADER]})
    s.is_duplicate("warm-the-cache")
    fake.add_tab("2026-11", [HEADER])  # อีก process สร้างแท็บนี้
    assert s.append_expense(make_record(date="2026-11-02")) == "2026-11"
    assert fake.calls["add_worksheet"] == 0
    assert len(fake.tab("2026-11").rows) == 2


def test_append_recreates_tab_deleted_after_cache_warm():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("OLD")], "2026-09": [HEADER]})
    s.is_duplicate("warm-the-cache")
    fake.remove_tab("2026-10")  # ผู้ใช้ลบแท็บทิ้งระหว่างที่บอทรันอยู่
    assert s.append_expense(make_record(date="2026-10-05")) == "2026-10"
    ws = fake.tab("2026-10")
    assert ws.rows[0] == HEADER
    assert len(ws.rows) == 2


def test_append_handles_tab_created_concurrently():
    """add_worksheet ชนกับอีก process ที่สร้างแท็บชื่อเดียวกันไปแล้ว → ใช้แท็บนั้นต่อ"""
    s, fake = new_sheet({"Sheet1": []})
    s.is_duplicate("warm-the-cache")
    original_refresh = fake.worksheets
    state = {"n": 0}

    def worksheets_hiding_new_tab(exclude_hidden=False):
        # ครั้งแรกหลัง cache warm: ยังไม่เห็นแท็บ (แต่จริง ๆ เพิ่งถูกสร้าง)
        state["n"] += 1
        result = original_refresh()
        if state["n"] == 1:
            fake.add_tab("2026-10", [HEADER])
            return [ws for ws in result if ws.title != "2026-10"]
        return result

    fake.worksheets = worksheets_hiding_new_tab
    assert s.append_expense(make_record()) == "2026-10"
    ws = fake.tab("2026-10")
    assert ws.rows[0] == HEADER
    assert len(ws.rows) == 2
    assert fake.titles().count("2026-10") == 1


# --- is_duplicate --------------------------------------------------------------------


def test_is_duplicate_empty_ref_makes_no_api_calls():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("")]})
    assert s.is_duplicate("") is False
    assert s.is_duplicate("   ") is False
    assert fake.total_calls() == 0


def multi_month_fake() -> dict[str, list[list]]:
    return {
        "Notes": [HEADER, data_row("NOT-A-MONTH-REF")],
        "2026-10": [HEADER, data_row("REF-OCT-1"), data_row(""), data_row("  REF-OCT-2  ")],
        "2026-09": [HEADER, data_row("REF-SEP-1")],
        "Summary 2026": [["เลขอ้างอิง"], ["SUMMARY-REF"]],
    }


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("REF-OCT-1", True),
        ("REF-SEP-1", True),
        ("REF-OCT-2", True),  # whitespace ในเซลล์ถูกตัด
        ("  REF-SEP-1 ", True),  # whitespace ใน input ถูกตัด
        ("NOT-A-MONTH-REF", False),  # อยู่ในแท็บที่ไม่ใช่ YYYY-MM
        ("SUMMARY-REF", False),
        ("REF-OCT", False),  # ต้องตรงทั้งค่า ไม่ใช่ substring
        ("ref-oct-1", False),
        ("เลขอ้างอิง", False),  # header ไม่นับ
        ("UNKNOWN", False),
    ],
)
def test_is_duplicate_scans_month_tabs_only(ref, expected):
    s, _ = new_sheet(multi_month_fake())
    assert s.is_duplicate(ref) is expected


def test_is_duplicate_uses_one_batch_get_over_month_tabs():
    s, fake = new_sheet(multi_month_fake())
    assert s.is_duplicate("REF-SEP-1") is True
    assert fake.calls["values_batch_get"] == 1
    assert fake.calls["get_all_values"] == 0
    assert fake.calls["col_values"] == 0
    ranges = fake.batch_get_ranges[0]
    assert all(r.startswith(("'2026-10'", "'2026-09'")) for r in ranges), ranges


def test_is_duplicate_after_append():
    s, fake = new_sheet({"Sheet1": []})
    assert s.is_duplicate("A306e5fe310f649b6") is False
    s.append_expense(make_record(trans_ref="A306e5fe310f649b6"))
    assert s.is_duplicate("A306e5fe310f649b6") is True


def test_is_duplicate_strips_stored_apostrophe():
    s, _ = new_sheet({"2026-10": [HEADER, data_row("'00123")]})
    assert s.is_duplicate("00123") is True


def test_is_duplicate_locates_ref_column_by_header_name():
    moved_header = ["คอลัมน์ที่ผู้ใช้แทรก"] + HEADER
    row = [""] + data_row("")
    row[moved_header.index("เลขอ้างอิง")] = "MOVED-REF"
    s, _ = new_sheet({"2026-10": [moved_header, row]})
    assert s.is_duplicate("MOVED-REF") is True


def test_is_duplicate_falls_back_to_default_column_without_header():
    s, _ = new_sheet({"2026-10": [data_row("NOHEADER-REF")]})
    assert s.is_duplicate("NOHEADER-REF") is True


def test_is_duplicate_no_month_tabs():
    s, fake = new_sheet({"Sheet1": [HEADER, data_row("X")]})
    assert s.is_duplicate("X") is False
    assert fake.calls["values_batch_get"] == 0


def test_is_duplicate_recovers_when_cached_tab_was_deleted():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("A")], "2026-09": [HEADER, data_row("B")]})
    assert s.is_duplicate("B") is True  # cache warm
    fake.remove_tab("2026-10")
    assert s.is_duplicate("B") is True
    assert s.is_duplicate("A") is False


def test_is_duplicate_handles_narrow_month_tab():
    s, fake = new_sheet({"2026-09": [HEADER, data_row("B")]})
    fake.add_tab("2026-10", [["วันที่", "จำนวนเงิน"], ["2026-10-01", 5]], cols=2)
    assert s.is_duplicate("B") is True
    assert s.is_duplicate("Z") is False


def test_tab_cache_reused_within_ttl():
    s, fake = new_sheet(multi_month_fake())
    s.is_duplicate("REF-OCT-1")
    s.is_duplicate("REF-SEP-1")
    s.month_summary("2026-10")
    assert fake.calls["worksheets"] == 1


def test_tab_cache_expires():
    s, fake = new_sheet({"2026-10": [HEADER]}, cache_ttl=0)
    assert s.is_duplicate("LATE") is False
    fake.add_tab("2026-11", [HEADER, data_row("LATE")])
    assert s.is_duplicate("LATE") is True


# --- month_summary -------------------------------------------------------------------


def test_month_summary_missing_tab_returns_none():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("", "อาหาร", 10)]})
    assert s.month_summary("2026-11") is None


@pytest.mark.parametrize("bad", ["", "Sheet1", "2026-1", "2026/10"])
def test_month_summary_invalid_month_returns_none(bad):
    s, fake = new_sheet({"Sheet1": [HEADER, data_row("", "อาหาร", 10)]})
    assert s.month_summary(bad) is None


def test_month_summary_sums_per_category_descending():
    rows = [
        HEADER,
        data_row("", "อาหาร", 120.0),
        data_row("", "เดินทาง", 500),
        data_row("", "อาหาร", "1,234.50"),
        data_row("", "ช้อปปิ้ง", "฿1,000.25"),
        data_row("", "ช้อปปิ้ง", "THB 12.00"),
        data_row("", "", 40),  # ไม่มีหมวด
        data_row("", "  ", "45.5"),  # หมวดว่าง (มีแต่ช่องว่าง)
        data_row("", "บันเทิง", "abc"),  # อ่านไม่ออก → ข้าม
        data_row("", "บันเทิง", ""),  # ว่าง → ข้าม
        data_row("", "สุขภาพ", "  99 บาท "),
        [""] * len(HEADER),  # แถวว่าง
    ]
    s, fake = new_sheet({"2026-10": rows})
    result = s.month_summary("2026-10")
    assert result == {
        "อาหาร": pytest.approx(1354.5),
        "ช้อปปิ้ง": pytest.approx(1012.25),
        "เดินทาง": pytest.approx(500.0),
        "สุขภาพ": pytest.approx(99.0),
        "ไม่ระบุ": pytest.approx(85.5),
    }
    assert list(result) == ["อาหาร", "ช้อปปิ้ง", "เดินทาง", "สุขภาพ", "ไม่ระบุ"]
    assert "บันเทิง" not in result


def test_month_summary_requests_unformatted_values():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("", "อาหาร", 10)]})
    s.month_summary("2026-10")
    assert fake.tab("2026-10").render_options[-1] == "UNFORMATTED_VALUE"


def test_month_summary_finds_columns_by_header_name():
    header = ["หมวด", "หมายเหตุ", "จำนวนเงิน"]
    rows = [header, ["อาหาร", "x", 50], ["เดินทาง", "y", "70.5"], ["อาหาร", "z", 25.25]]
    s, _ = new_sheet({"2026-10": rows})
    assert s.month_summary("2026-10") == {"อาหาร": pytest.approx(75.25), "เดินทาง": pytest.approx(70.5)}


def test_month_summary_without_header_uses_default_columns():
    s, _ = new_sheet({"2026-10": [data_row("", "อาหาร", 10), data_row("", "อาหาร", "5")]})
    assert s.month_summary("2026-10") == {"อาหาร": pytest.approx(15.0)}


def test_month_summary_header_only_is_empty_dict():
    s, _ = new_sheet({"2026-10": [HEADER]})
    assert s.month_summary("2026-10") == {}


def test_month_summary_short_rows_do_not_crash():
    s, _ = new_sheet({"2026-10": [HEADER, ["2026-10-01"], ["2026-10-02", "", 30, "", "อาหาร"]]})
    assert s.month_summary("2026-10") == {"อาหาร": pytest.approx(30.0)}


def test_month_summary_finds_tab_created_after_cache_warm():
    s, fake = new_sheet({"2026-10": [HEADER]})
    assert s.month_summary("2026-11") is None
    fake.add_tab("2026-11", [HEADER, data_row("", "อาหาร", 60)])
    assert s.month_summary("2026-11") == {"อาหาร": pytest.approx(60.0)}


def test_month_summary_tab_deleted_after_cache_warm_returns_none():
    s, fake = new_sheet({"2026-10": [HEADER, data_row("", "อาหาร", 60)], "2026-09": [HEADER]})
    assert s.month_summary("2026-10") == {"อาหาร": pytest.approx(60.0)}
    fake.remove_tab("2026-10")
    assert s.month_summary("2026-10") is None


def test_summary_after_append_roundtrip():
    s, fake = new_sheet({"Sheet1": []})
    s.append_expense(make_record(category="อาหาร", amount=50))
    s.append_expense(make_record(category="อาหาร", amount=25.5, trans_ref="R2"))
    s.append_expense(make_record(category="เดินทาง", amount=100, trans_ref=""))
    assert s.month_summary("2026-10") == {"อาหาร": pytest.approx(75.5), "เดินทาง": pytest.approx(100.0)}
