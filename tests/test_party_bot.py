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
