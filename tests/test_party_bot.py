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


# ---------- Task 5: status / แก้ / ลบ ----------

from party_math import balances  # noqa: E402


def row(eid: str, payer: str, amount: int, shares: dict[str, int], created_by: str | None = None, note: str = "",
        created_at: str = "2026-10-29T10:00:00+00:00") -> dict:
    return {"id": eid, "party_id": PARTY_ID, "status": "confirmed", "payer_id": payer, "amount_satang": amount,
            "created_by": created_by or payer, "note": note, "participants": list(shares), "created_at": created_at,
            "party_expense_shares": [{"discord_id": k, "share_satang": v} for k, v in shares.items()]}


SPEC_ROWS = [
    row("e1", "1", 90000, {"1": 30000, "2": 30000, "3": 30000}, note="ที่พัก"),
    row("e2", "2", 30000, {"1": 10000, "2": 10000, "3": 10000}, note="ข้าว"),
]


def embed_text(embed) -> str:
    return f"{embed.title} {embed.description} " + " ".join(f"{f.name} {f.value}" for f in embed.fields)


def test_expenses_from_rows_uses_stored_shares():
    rows = [row("e1", "1", 10000, {"1": 3334, "2": 3333, "3": 3333})]
    exps = party_bot.expenses_from_rows(rows)
    assert exps[0].payer == "1" and exps[0].amount == 10000 and set(exps[0].participants) == {"1", "2", "3"}
    b = balances(["1", "2", "3"], exps)
    assert {k: v.owed for k, v in b.items()} == {"1": 3334, "2": 3333, "3": 3333}


def test_status_embed_totals_and_net():
    text = embed_text(party_bot.status_embed(PARTY, SPEC_ROWS, ["1", "2", "3"]))
    assert "1,200.00" in text
    assert "+500.00" in text and "-100.00" in text and "-400.00" in text
    assert "ที่พัก" in text and "ข้าว" in text


def test_status_embed_empty():
    text = embed_text(party_bot.status_embed(PARTY, [], ["1"]))
    assert "ยังไม่มีรายการ" in text


def test_status_embed_limits_25_rows():
    rows = [row(f"e{i}", "1", 100 + i, {"1": 100 + i}, note=f"รายการ{i}") for i in range(30)]
    embed = party_bot.status_embed(PARTY, rows, ["1"])
    assert "รายการ29" in embed.description and "รายการ4 " not in embed.description + " "
    assert len(embed.description) <= 4096
    assert all(len(f.value) <= 1024 for f in embed.fields)


def test_editable_rows_creator_or_host():
    rows = [row("e1", "1", 100, {"1": 100}, created_by="1"), row("e2", "2", 100, {"2": 100}, created_by="2")]
    assert [r["id"] for r in party_bot.editable_rows(rows, "1", host_id="9")] == ["e1"]
    assert [r["id"] for r in party_bot.editable_rows(rows, "9", host_id="9")] == ["e2", "e1"]  # ใหม่สุดก่อน
    assert party_bot.editable_rows(rows, "5", host_id="9") == []


def test_editable_rows_max_25():
    rows = [row(f"e{i}", "1", 100, {"1": 100}) for i in range(30)]
    assert len(party_bot.editable_rows(rows, "1", host_id="9")) == 25


# ---------- Task 6: ปิดปาร์ตี้ / สรุป / โอนแล้ว ----------

import asyncio  # noqa: E402

TRANSFERS = [
    {"id": "33333333-3333-3333-3333-333333333331", "seq": 1, "from_id": "3", "to_id": "1", "amount_satang": 40000,
     "paid": False},
    {"id": "33333333-3333-3333-3333-333333333332", "seq": 2, "from_id": "2", "to_id": "1", "amount_satang": 10000,
     "paid": False},
]
CLOSED = {**PARTY, "status": "closed"}


def test_final_summary_spec_example():
    text = embed_text(party_bot.final_summary_embed(CLOSED, SPEC_ROWS, ["1", "2", "3"], TRANSFERS))
    assert "1,200.00" in text
    assert "<@3> → <@1> : 400.00 บาท" in text and "<@2> → <@1> : 100.00 บาท" in text
    assert "+500.00" in text and "-400.00" in text


def test_final_summary_no_transfers():
    text = embed_text(party_bot.final_summary_embed({**PARTY, "status": "settled"}, [], ["1", "2"], []))
    assert "ไม่มีใครต้องโอน" in text


def test_final_summary_marks_paid_and_settled():
    paid = [{**t, "paid": True} for t in TRANSFERS]
    embed = party_bot.final_summary_embed({**PARTY, "status": "settled"}, SPEC_ROWS, ["1", "2", "3"], paid)
    assert "เคลียร์ครบแล้ว" in embed.title
    assert embed_text(embed).count("✅") >= 2


def test_transfer_view_one_button_per_transfer():
    async def build():
        return party_bot.transfer_view(TRANSFERS)

    view = asyncio.run(build())
    ids = [item.custom_id for item in view.children]
    assert ids == [f"party:paid:{t['id']}" for t in TRANSFERS]


def test_toggle_status_text_covers_all_statuses():
    assert set(party_bot.TOGGLE_STATUS_TEXT) == {"paid", "unpaid", "settled", "forbidden", "locked", "not_found"}


# ---------- final review: ชื่อสมาชิกต้องดึงพร้อมกัน (กันเกิน 3 วินาทีของ Discord) ----------

import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def test_member_names_fetches_concurrently():
    class SlowGuild:
        id = 999

        def get_member(self, _id):
            return None

        async def fetch_member(self, member_id):
            await asyncio.sleep(0.2)
            return SimpleNamespace(display_name=f"name{member_id}")

    party_bot._name_cache.clear()
    ids = [str(i) for i in range(1, 11)]
    start = time.perf_counter()
    names = asyncio.run(party_bot.member_names(SlowGuild(), ids))
    elapsed = time.perf_counter() - start
    assert names == {i: f"name{i}" for i in ids}
    assert elapsed < 1.0, f"ดึงชื่อทีละคนใช้ {elapsed:.2f}s"
