"""Tests สำหรับ API ของเว็บ (web/app.py) — store / auth / ตัวอ่านสลิปเป็น fake ทั้งหมด"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import web.app
from config import Config
from slip_common import BKK_TZ
from store import NewExpense
from web.app import MAX_UPLOAD_BYTES, create_app
from web.auth import AuthError, AuthUser
from web.slips import SlipDraft

USER_A = AuthUser(id="00000000-0000-0000-0000-00000000000a", email="a@x.com", discord_id="111", providers=("discord",))
USER_B = AuthUser(id="00000000-0000-0000-0000-00000000000b", email="b@x.com", discord_id=None, providers=("google",))
TOKENS = {"tok-a": USER_A, "tok-b": USER_B}
A = {"Authorization": "Bearer tok-a"}
B = {"Authorization": "Bearer tok-b"}

CFG = Config(
    discord_token="", discord_channel_id=0, typhoon_api_key="typhoon-secret", typhoon_llm_model="m",
    supabase_url="https://proj.supabase.co", supabase_anon_key="sb_publishable_x",
    supabase_service_role_key="sb_secret_x", public_base_url="", categories=("อาหาร", "เดินทาง", "อื่นๆ"),
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


class FakeAuth:
    def get_user(self, token):
        if token not in TOKENS:
            raise AuthError("bad token")
        return TOKENS[token]


class FakeStore:
    """จำลอง ExpenseStore + กติกา unique เดียวกับ supabase/schema.sql"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.claims: list[tuple[str, str]] = []

    def _owned(self, row, user_id, discord_id):
        return (user_id and row["user_id"] == user_id) or (discord_id and row["discord_id"] == discord_id)

    def insert(self, exp: NewExpense):
        row = asdict(exp)
        row["trans_ref"] = exp.trans_ref or None
        row["time"] = exp.time or None
        if row["trans_ref"]:
            for r in self.rows:
                if r["trans_ref"] == row["trans_ref"] and (
                    (row["user_id"] and r["user_id"] == row["user_id"])
                    or (row["discord_id"] and r["discord_id"] == row["discord_id"])
                ):
                    return None
        row.update(id=str(uuid.uuid4()), created_at="2026-10-28T10:00:00+00:00")
        self.rows.append(row)
        return dict(row)

    def is_duplicate(self, trans_ref, *, user_id=None, discord_id=None):
        return any(r["trans_ref"] == trans_ref and self._owned(r, user_id, discord_id) for r in self.rows)

    def list_expenses(self, *, start, end, user_id=None, discord_id=None):
        rows = [r for r in self.rows if self._owned(r, user_id, discord_id) and start <= r["date"] < end]
        return sorted(rows, key=lambda r: (r["date"], r["time"] or ""), reverse=True)

    def delete(self, expense_id, *, user_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not (r["id"] == expense_id and r["user_id"] == user_id)]
        return len(self.rows) < before

    def claim_discord_expenses(self, user_id, discord_id):
        self.claims.append((user_id, discord_id))
        return 0


DRAFT = SlipDraft(amount=250.0, date="2026-10-20", time="12:30", receiver="ร้านกาแฟ", bank="กสิกรไทย",
                  trans_ref="REF-1", error=None)


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def draft():
    return {"value": DRAFT}


@pytest.fixture
def client(store, draft):
    def reader(data, mime, api_key, model):
        assert api_key == "typhoon-secret"
        return draft["value"]

    return TestClient(create_app(CFG, store=store, auth=FakeAuth(), reader=reader))


def add(client, headers=A, **overrides):
    body = {"amount": 100, "category": "อาหาร", "date": "2026-10-20", "time": "12:00", "receiver": "ร้าน",
            "note": "", "bank": "", "trans_ref": None}
    body.update(overrides)
    return client.post("/api/expenses", json=body, headers=headers)


# ---------- auth ----------

@pytest.mark.parametrize(
    "method, path",
    [("get", "/api/expenses"), ("post", "/api/expenses"), ("get", "/api/summary"),
     ("post", "/api/slips/read"), ("delete", f"/api/expenses/{uuid.uuid4()}")],
)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "tok-a"}])
def test_requires_token(client, method, path, headers):
    resp = getattr(client, method)(path, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "กรุณาเข้าสู่ระบบ"


def test_config_is_public_and_has_no_secrets(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == {"supabase_url": "https://proj.supabase.co", "supabase_anon_key": "sb_publishable_x",
                           "categories": ["อาหาร", "เดินทาง", "อื่นๆ"]}
    assert "sb_secret_x" not in resp.text and "typhoon-secret" not in resp.text


def test_claim_called_for_discord_user_only(client, store):
    client.get("/api/expenses?month=2026-10", headers=A)
    client.get("/api/expenses?month=2026-10", headers=B)
    assert store.claims == [(USER_A.id, "111")]


# ---------- read slip ----------

def test_read_slip_returns_draft_and_duplicate_flag(client):
    resp = client.post("/api/slips/read", files={"file": ("s.png", PNG, "image/png")}, headers=A)
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount"] == 250.0 and body["trans_ref"] == "REF-1" and body["duplicate"] is False
    add(client, trans_ref="REF-1")
    resp = client.post("/api/slips/read", files={"file": ("s.jpg", PNG, "image/jpeg")}, headers=A)
    assert resp.json()["duplicate"] is True


def test_read_slip_duplicate_is_per_user(client):
    add(client, headers=B, trans_ref="REF-1")
    resp = client.post("/api/slips/read", files={"file": ("s.png", PNG, "image/png")}, headers=A)
    assert resp.json()["duplicate"] is False


def test_read_slip_rejects_non_image(client):
    resp = client.post("/api/slips/read", files={"file": ("a.pdf", b"%PDF", "application/pdf")}, headers=A)
    assert resp.status_code == 415


def test_read_slip_rejects_large_file(client):
    big = b"0" * (MAX_UPLOAD_BYTES + 1)
    resp = client.post("/api/slips/read", files={"file": ("s.png", big, "image/png")}, headers=A)
    assert resp.status_code == 413


def test_read_slip_without_file_422(client):
    assert client.post("/api/slips/read", headers=A).status_code == 422


def test_read_slip_unreadable_amount_returns_null_amount(client, draft):
    draft["value"] = SlipDraft(amount=None, date=None, time=None, receiver=None, bank="", trans_ref=None,
                               error="OCR ล้มเหลว")
    resp = client.post("/api/slips/read", files={"file": ("s.png", PNG, "image/png")}, headers=A)
    assert resp.status_code == 200
    assert resp.json()["amount"] is None and resp.json()["duplicate"] is False


# ---------- create / list ----------

def test_create_expense_and_list_month(client, store):
    resp = add(client, amount=99.999, trans_ref="R1")
    assert resp.status_code == 201
    created = resp.json()
    assert created["amount"] == 100.0 and created["source"] == "web"
    assert store.rows[0]["user_id"] == USER_A.id and store.rows[0]["discord_id"] == "111"
    listed = client.get("/api/expenses?month=2026-10", headers=A).json()
    assert [r["id"] for r in listed] == [created["id"]]
    assert set(listed[0]) == {"id", "date", "time", "amount", "receiver", "category", "note", "bank",
                              "trans_ref", "source", "created_at"}
    assert client.get("/api/expenses?month=2026-09", headers=A).json() == []


def test_create_expense_defaults_date_to_today_bkk(client, monkeypatch, store):
    monkeypatch.setattr(web.app, "bkk_now", lambda: datetime(2026, 10, 28, 23, 30, tzinfo=BKK_TZ))
    assert add(client, date=None, time=None).status_code == 201
    assert store.rows[0]["date"] == "2026-10-28" and store.rows[0]["time"] is None


def test_create_expense_converts_buddhist_year(client, store):
    assert add(client, date="2569-10-20").status_code == 201
    assert store.rows[0]["date"] == "2026-10-20"


def test_list_defaults_to_current_month(client, monkeypatch):
    monkeypatch.setattr(web.app, "bkk_now", lambda: datetime(2026, 10, 28, tzinfo=BKK_TZ))
    add(client)
    assert len(client.get("/api/expenses", headers=A).json()) == 1


@pytest.mark.parametrize(
    "overrides",
    [{"category": "ไม่มีหมวดนี้"}, {"amount": 0}, {"amount": -5}, {"amount": 10_000_001}, {"date": "26/10/2026"},
     {"date": "2026-02-30"}, {"time": "25:00"}, {"note": "x" * 501}],
)
def test_create_expense_invalid_422(client, store, overrides):
    assert add(client, **overrides).status_code == 422
    assert store.rows == []


def test_create_duplicate_409(client):
    assert add(client, trans_ref="R1").status_code == 201
    resp = add(client, trans_ref="R1")
    assert resp.status_code == 409 and resp.json()["detail"] == "สลิปนี้บันทึกไปแล้ว"


def test_same_ref_different_users_both_ok(client):
    assert add(client, headers=A, trans_ref="R1").status_code == 201
    assert add(client, headers=B, trans_ref="R1").status_code == 201


def test_list_only_own_expenses(client):
    add(client, headers=A, receiver="ของ A")
    add(client, headers=B, receiver="ของ B")
    assert [r["receiver"] for r in client.get("/api/expenses?month=2026-10", headers=A).json()] == ["ของ A"]
    assert [r["receiver"] for r in client.get("/api/expenses?month=2026-10", headers=B).json()] == ["ของ B"]


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "abc", "2026-10-01"])
def test_bad_month_422(client, month):
    assert client.get(f"/api/expenses?month={month}", headers=A).status_code == 422
    assert client.get(f"/api/summary?month={month}", headers=A).status_code == 422


# ---------- delete ----------

def test_delete_own_expense_204(client, store):
    expense_id = add(client).json()["id"]
    resp = client.delete(f"/api/expenses/{expense_id}", headers=A)
    assert resp.status_code == 204 and resp.content == b""
    assert store.rows == []


def test_delete_other_users_expense_is_404(client, store):
    expense_id = add(client, headers=B).json()["id"]
    assert client.delete(f"/api/expenses/{expense_id}", headers=A).status_code == 404
    assert len(store.rows) == 1


def test_delete_bad_id_422(client):
    assert client.delete("/api/expenses/not-a-uuid", headers=A).status_code == 422


# ---------- summary ----------

def test_summary_totals_and_six_months(client):
    add(client, amount=100, category="อาหาร", date="2026-10-01")
    add(client, amount=300, category="เดินทาง", date="2026-10-31")
    add(client, amount=50.5, category="อาหาร", date="2026-10-15")
    add(client, amount=70, category="อาหาร", date="2026-08-10")
    add(client, amount=999, category="อาหาร", date="2026-04-30")  # เก่ากว่า 6 เดือน
    add(client, amount=999, category="อาหาร", date="2026-11-01")  # เดือนถัดไป
    add(client, headers=B, amount=5000, date="2026-10-02")  # ของคนอื่น
    body = client.get("/api/summary?month=2026-10", headers=A).json()
    assert body["month"] == "2026-10"
    assert body["total"] == 450.5
    assert body["by_category"] == [{"category": "เดินทาง", "total": 300.0}, {"category": "อาหาร", "total": 150.5}]
    assert body["monthly"] == [
        {"month": "2026-05", "total": 0}, {"month": "2026-06", "total": 0}, {"month": "2026-07", "total": 0},
        {"month": "2026-08", "total": 70.0}, {"month": "2026-09", "total": 0}, {"month": "2026-10", "total": 450.5},
    ]


def test_summary_crosses_year_boundary(client):
    add(client, amount=10, date="2025-12-05")
    body = client.get("/api/summary?month=2026-02", headers=A).json()
    assert [m["month"] for m in body["monthly"]] == ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
    assert body["monthly"][3]["total"] == 10.0
    assert body["total"] == 0 and body["by_category"] == []


# ---------- pages ----------

@pytest.mark.parametrize("path", ["/", "/dashboard", "/settings"])
def test_pages_served(client, path):
    resp = client.get(path)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")


def test_health(client):
    assert client.get("/health").json() == {"ok": True}
