"""Tests สำหรับ web/slips.read_draft — QR + OCR ส่งต่อค่าถูกต้อง (ไม่เรียก API จริง)"""
from __future__ import annotations

import pytest

import slip_qr
import slip_reader
from slip_reader import SlipData
from web import slips


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict:
    record: dict = {}

    def fake_read_slip(data, api_key, model, mime, **kwargs):
        record.update(kwargs)
        return SlipData(amount=150.0, receiver=" นาย เอ ")

    monkeypatch.setattr(slip_reader, "read_slip", fake_read_slip)
    monkeypatch.setattr(slip_qr, "read_slip_qr", lambda data: {"bank_code": "004", "trans_ref": "REF1"})
    return record


def test_read_draft_hedges_ocr_and_reads_qr(seen) -> None:
    draft = slips.read_draft(b"img", "image/png", "k", "m")
    assert seen == {"copies": slips.OCR_COPIES, "expect": None}
    assert slips.OCR_COPIES == 2
    assert draft.amount == 150.0 and draft.trans_ref == "REF1" and draft.receiver == "นาย เอ"


def test_read_draft_passes_expected_amount(seen) -> None:
    slips.read_draft(b"img", "image/png", "k", "m", expect=333.33)
    assert seen["expect"] == 333.33
