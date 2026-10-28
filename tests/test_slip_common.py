"""Unit tests สำหรับ helper กลางที่บอทและเว็บใช้ร่วมกัน (slip_common.py)"""
from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta

import pytest

import slip_common


@pytest.mark.parametrize(
    "value, expected",
    [("'ABC123 ", "ABC123"), (" 0157ABC ", "0157ABC"), ("' ", ""), ("", ""), (None, "")],
)
def test_normalize_ref(value, expected):
    assert slip_common.normalize_ref(value) == expected


@pytest.mark.parametrize("value", ["2026-10", "1999-01", "2026-12"])
def test_month_re_accepts(value):
    assert slip_common.MONTH_RE.fullmatch(value)


@pytest.mark.parametrize("value", ["2026-13", "2026-00", "2026-1", "26-10", "2026-10-01", ""])
def test_month_re_rejects(value):
    assert slip_common.MONTH_RE.fullmatch(value) is None


def test_load_bkk_tz_falls_back_without_tzdata(monkeypatch):
    def boom(key):
        raise zoneinfo.ZoneInfoNotFoundError(key)

    monkeypatch.setattr(slip_common, "ZoneInfo", boom)
    tz = slip_common.load_bkk_tz()
    assert datetime(2026, 1, 1, tzinfo=tz).utcoffset() == timedelta(hours=7)
