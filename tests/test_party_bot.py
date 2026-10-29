"""Unit tests สำหรับส่วนที่ไม่ต้องต่อ Discord ใน party_bot.py"""
from __future__ import annotations

import re
import uuid

import pytest

import party_bot

PARTY_ID = "11111111-1111-1111-1111-111111111111"


# ---------- custom_id ----------

def test_custom_ids_round_trip():
    assert party_bot.DYNAMIC_ITEMS, "ต้องลงทะเบียนปุ่ม/เมนูอย่างน้อยหนึ่งตัว"
    prefixes = set()
    for cls in party_bot.DYNAMIC_ITEMS:
        target = str(uuid.uuid4())
        item = cls(target)
        match = re.fullmatch(cls.__discord_ui_compiled_template__, item.custom_id)
        assert match, f"{cls.__name__}: {item.custom_id}"
        assert match.group("target") == target
        assert len(item.custom_id) <= 100
        assert item.custom_id.startswith("party:")
        prefixes.add(item.custom_id.rsplit(":", 1)[0])
    assert len(prefixes) == len(party_bot.DYNAMIC_ITEMS), "custom_id ของแต่ละคลาสต้องไม่ชนกัน"


# ---------- status text ----------

def test_join_status_text_covers_all_statuses():
    assert set(party_bot.JOIN_STATUS_TEXT) == {"ok", "full", "already", "closed", "not_found"}


def test_remove_status_text_covers_all_statuses():
    assert set(party_bot.REMOVE_STATUS_TEXT) == {"ok", "involved", "not_member", "forbidden", "host", "closed",
                                                 "not_found"}


# ---------- invite ----------

PARTY = {"id": PARTY_ID, "code": "ABC234", "name": "ทริปเขาใหญ่", "host_id": "9", "status": "open"}


def test_invite_embed_shows_code_and_count():
    embed = party_bot.invite_embed(PARTY, 3)
    text = f"{embed.title} {embed.description} " + " ".join(f"{f.name} {f.value}" for f in embed.fields)
    assert "ทริปเขาใหญ่" in text and "ABC234" in text and "3/10" in text and "<@9>" in text


def test_invite_embed_closed_party():
    embed = party_bot.invite_embed({**PARTY, "status": "closed"}, 3)
    text = f"{embed.description} " + " ".join(f"{f.name} {f.value}" for f in embed.fields)
    assert "ปิดแล้ว" in text


# ---------- Task 4: draft / ฟอร์มยืนยัน ----------

from datetime import datetime, timedelta, timezone  # noqa: E402

DRAFT = {
    "id": "22222222-2222-2222-2222-222222222222", "party_id": PARTY_ID, "status": "draft", "amount_satang": 10000,
    "payer_id": "5", "created_by": "5", "participants": ["5", "7", "9"], "note": "", "source": "slip",
    "created_at": "2026-10-29T10:00:00+00:00",
}


def test_sanitize_draft_drops_non_members():
    out = party_bot.sanitize_draft({**DRAFT, "participants": ["5", "7", "8"]}, ["5", "7", "9"])
    assert out["participants"] == ["5", "7"]


def test_sanitize_draft_payer_falls_back_to_creator():
    out = party_bot.sanitize_draft({**DRAFT, "payer_id": "8"}, ["5", "7", "9"])
    assert out["payer_id"] == "5"


def test_sanitize_draft_empty_participants_means_everyone():
    out = party_bot.sanitize_draft({**DRAFT, "participants": ["8"]}, ["5", "7", "9"])
    assert out["participants"] == ["5", "7", "9"]


def test_form_state_requires_amount():
    fs = party_bot.form_state({**DRAFT, "amount_satang": None}, duplicate=False, expired=False)
    assert fs.can_confirm is False and fs.shares == {}
    assert any("ยอด" in w for w in fs.warnings)


def test_form_state_duplicate_blocks():
    fs = party_bot.form_state(DRAFT, duplicate=True, expired=False)
    assert fs.can_confirm is False and any("ซ้ำ" in w or "บันทึกในปาร์ตี้แล้ว" in w for w in fs.warnings)


def test_form_state_expired_blocks():
    fs = party_bot.form_state(DRAFT, duplicate=False, expired=True)
    assert fs.can_confirm is False and any("หมดอายุ" in w for w in fs.warnings)


def test_form_state_shares_sum_to_amount():
    fs = party_bot.form_state(DRAFT, duplicate=False, expired=False)
    assert fs.can_confirm is True and fs.warnings == []
    assert fs.shares == {"5": 3334, "7": 3333, "9": 3333}


def test_is_expired():
    created = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
    row = {**DRAFT, "created_at": created.isoformat()}
    assert party_bot.is_expired(row, created + timedelta(hours=23)) is False
    assert party_bot.is_expired(row, created + timedelta(hours=25)) is True
    assert party_bot.is_expired({**row, "status": "confirmed"}, created + timedelta(days=9)) is False


@pytest.mark.parametrize("value, expected", [(104.0, 10400), (33.33, 3333), (0.1, 10), (None, None), (0, None),
                                             (-5, None), (float("nan"), None), (float("inf"), None)])
def test_amount_to_satang(value, expected):
    assert party_bot.amount_to_satang(value) == expected


def test_save_status_text_covers_all_statuses():
    assert set(party_bot.SAVE_STATUS_TEXT) == {"ok", "closed", "forbidden", "invalid", "duplicate", "not_found"}


def test_expense_summary_embed_lists_shares():
    embed = party_bot.expense_summary_embed({**DRAFT, "note": "หมูกระทะ"}, {"5": 3334, "7": 3333, "9": 3333})
    text = f"{embed.title} {embed.description} " + " ".join(f"{f.name} {f.value}" for f in embed.fields)
    assert "100.00" in text and "หมูกระทะ" in text and "<@5>" in text
    assert "33.34" in text and "33.33" in text
