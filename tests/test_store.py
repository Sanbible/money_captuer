"""Unit tests สำหรับ store.py — ใช้ httpx.MockTransport (ไม่มี network / Supabase จริง)"""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from store import ExpenseStore, NewExpense

URL = "https://proj.supabase.co"
SECRET = "sb_secret_abc"


class Recorder:
    """จำ request ทุกตัว แล้วตอบด้วย handler ที่กำหนด"""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda req: httpx.Response(200, json=[]))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def make_store(rec: Recorder, key: str = SECRET) -> ExpenseStore:
    return ExpenseStore(URL, key, client=httpx.Client(transport=httpx.MockTransport(rec)))


def expense(**overrides: Any) -> NewExpense:
    base = dict(
        date="2026-10-26", time="14:05", amount=120.5, receiver="ร้านข้าว", category="อาหาร", note="",
        bank="กรุงไทย", trans_ref="'REF001 ", source="web", user_id="user-a", discord_id=None, discord_link="",
    )
    base.update(overrides)
    return NewExpense(**base)


def params(req: httpx.Request) -> list[tuple[str, str]]:
    return list(req.url.params.multi_items())


# ---------- headers ----------

def test_secret_key_sent_only_as_apikey():
    rec = Recorder()
    make_store(rec).list_expenses(start="2026-10-01", end="2026-11-01", user_id="u")
    assert rec.last.headers["apikey"] == SECRET
    assert "authorization" not in rec.last.headers
    assert str(rec.last.url).startswith(f"{URL}/rest/v1/expenses")


def test_legacy_jwt_key_also_sent_as_bearer():
    rec = Recorder()
    make_store(rec, key="eyJhbGciOi.legacy.jwt").list_expenses(start="2026-10-01", end="2026-11-01", user_id="u")
    assert rec.last.headers["apikey"] == "eyJhbGciOi.legacy.jwt"
    assert rec.last.headers["authorization"] == "Bearer eyJhbGciOi.legacy.jwt"


# ---------- insert ----------

def test_insert_returns_row_and_normalizes_ref():
    row = {"id": "e1", "amount": 120.5}
    rec = Recorder(lambda req: httpx.Response(201, json=[row]))
    assert make_store(rec).insert(expense()) == row
    req = rec.last
    assert req.method == "POST" and req.url.path == "/rest/v1/expenses"
    assert req.headers["prefer"] == "return=representation"
    body = json.loads(req.content)
    assert body["trans_ref"] == "REF001"
    assert body["user_id"] == "user-a" and body["source"] == "web" and body["amount"] == 120.5


def test_insert_empty_optional_fields_become_null():
    rec = Recorder(lambda req: httpx.Response(201, json=[{}]))
    make_store(rec).insert(expense(trans_ref="", time="", discord_link=""))
    body = json.loads(rec.last.content)
    assert body["trans_ref"] is None and body["time"] is None and body["discord_link"] is None


def test_insert_duplicate_returns_none():
    rec = Recorder(lambda req: httpx.Response(409, json={"code": "23505", "message": "duplicate key"}))
    assert make_store(rec).insert(expense()) is None


def test_insert_other_error_raises():
    rec = Recorder(lambda req: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        make_store(rec).insert(expense())


# ---------- is_duplicate ----------

def test_is_duplicate_with_user_and_discord_uses_or_filter():
    rec = Recorder(lambda req: httpx.Response(200, json=[{"id": "e1"}]))
    assert make_store(rec).is_duplicate("REF001", user_id="user-a", discord_id="111") is True
    p = params(rec.last)
    assert ("trans_ref", "eq.REF001") in p
    assert ("or", '(user_id.eq.user-a,discord_id.eq."111")') in p


def test_is_duplicate_discord_only():
    rec = Recorder()
    assert make_store(rec).is_duplicate("REF001", discord_id="111") is False
    assert ("discord_id", "eq.111") in params(rec.last)


@pytest.mark.parametrize("ref", ["", "   ", "'", None])
def test_is_duplicate_empty_ref_makes_no_request(ref):
    rec = Recorder()
    assert make_store(rec).is_duplicate(ref, user_id="user-a") is False
    assert rec.requests == []


# ---------- owner required ----------

def test_list_without_owner_raises():
    with pytest.raises(ValueError):
        make_store(Recorder()).list_expenses(start="2026-10-01", end="2026-11-01")


def test_is_duplicate_without_owner_raises():
    with pytest.raises(ValueError):
        make_store(Recorder()).is_duplicate("REF001")


# ---------- list ----------

def test_list_expenses_query():
    rows = [{"id": "e1"}]
    rec = Recorder(lambda req: httpx.Response(200, json=rows))
    assert make_store(rec).list_expenses(start="2026-10-01", end="2026-11-01", user_id="user-a") == rows
    p = params(rec.last)
    assert ("date", "gte.2026-10-01") in p and ("date", "lt.2026-11-01") in p
    assert ("user_id", "eq.user-a") in p
    assert dict(p)["order"].startswith("date.desc,time.desc")


# ---------- delete ----------

@pytest.mark.parametrize("response, expected", [([], False), ([{"id": "e1"}], True)])
def test_delete_scoped_to_user(response, expected):
    rec = Recorder(lambda req: httpx.Response(200, json=response))
    assert make_store(rec).delete("e1", user_id="user-a") is expected
    req = rec.last
    assert req.method == "DELETE"
    assert ("id", "eq.e1") in params(req) and ("user_id", "eq.user-a") in params(req)
    assert req.headers["prefer"] == "return=representation"


# ---------- claim ----------

def test_claim_calls_rpc():
    rec = Recorder(lambda req: httpx.Response(200, json=3))
    assert make_store(rec).claim_discord_expenses("user-a", "111") == 3
    assert rec.last.url.path == "/rest/v1/rpc/claim_discord_expenses"
    assert json.loads(rec.last.content) == {"p_user": "user-a", "p_discord": "111"}
