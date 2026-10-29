"""Unit tests สำหรับ party_store.py — httpx.MockTransport แทน Supabase"""
from __future__ import annotations

import json
import random

import httpx
import pytest

from party_math import Transfer
from party_store import CODE_ALPHABET, CODE_LENGTH, PartyStore, new_code

URL = "https://proj.supabase.co"
PARTY = "11111111-1111-1111-1111-111111111111"
EXPENSE = "22222222-2222-2222-2222-222222222222"


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0) if self._responses else httpx.Response(200, json=[])

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def make(rec: Recorder, key: str = "sb_secret_x") -> PartyStore:
    return PartyStore(URL, key, client=httpx.Client(transport=httpx.MockTransport(rec)))


def params(req: httpx.Request) -> list[tuple[str, str]]:
    return list(req.url.params.multi_items())


def body(req: httpx.Request):
    return json.loads(req.content)


def conflict() -> httpx.Response:
    return httpx.Response(409, json={"code": "23505", "message": "duplicate key"})


# ---------- code ----------

def test_new_code_alphabet_and_length():
    rng = random.Random(1)
    for _ in range(200):
        code = new_code(rng)
        assert len(code) == CODE_LENGTH and set(code) <= set(CODE_ALPHABET)
    assert not set("01OIL") & set(CODE_ALPHABET)


# ---------- headers ----------

def test_headers_secret_key_only_apikey():
    rec = Recorder()
    make(rec).members(PARTY)
    assert rec.last.headers["apikey"] == "sb_secret_x" and "authorization" not in rec.last.headers
    assert rec.last.url.path == "/rest/v1/party_members"


# ---------- create ----------

PARTY_ROW = {"id": PARTY, "code": "ABC234", "status": "open"}


def test_create_party_inserts_party_and_host():
    rec = Recorder(httpx.Response(201, json=[PARTY_ROW]), httpx.Response(201, json=[]))
    row = make(rec).create_party(name="ทริปเขาใหญ่", host_id="9", guild_id="1", channel_id="2", thread_id="3")
    assert row == PARTY_ROW
    party_req, member_req = rec.requests
    assert party_req.url.path == "/rest/v1/parties" and body(party_req)["host_id"] == "9"
    assert set(body(party_req)["code"]) <= set(CODE_ALPHABET)
    assert member_req.url.path == "/rest/v1/party_members"
    assert body(member_req) == {"party_id": PARTY, "discord_id": "9"}


def test_create_party_retries_on_code_conflict():
    rec = Recorder(conflict(), httpx.Response(201, json=[PARTY_ROW]), httpx.Response(201, json=[]))
    assert make(rec).create_party(name="x", host_id="9", guild_id="1", channel_id="2", thread_id="3") == PARTY_ROW
    codes = [body(r)["code"] for r in rec.requests[:2]]
    assert len(rec.requests) == 3


def test_create_party_gives_up_after_5_conflicts():
    rec = Recorder(*[conflict() for _ in range(5)])
    with pytest.raises(RuntimeError):
        make(rec).create_party(name="x", host_id="9", guild_id="1", channel_id="2", thread_id="3")
    assert len(rec.requests) == 5


# ---------- lookups ----------

def test_party_by_code_uppercases():
    rec = Recorder(httpx.Response(200, json=[PARTY_ROW]))
    assert make(rec).party_by_code(" abc234 ") == PARTY_ROW
    assert ("code", "eq.ABC234") in params(rec.last)


def test_party_by_thread_missing_returns_none():
    rec = Recorder(httpx.Response(200, json=[]))
    assert make(rec).party_by_thread("3") is None
    assert ("thread_id", "eq.3") in params(rec.last)


def test_members_in_join_order():
    rec = Recorder(httpx.Response(200, json=[{"discord_id": "9"}, {"discord_id": "5"}]))
    assert make(rec).members(PARTY) == ["9", "5"]
    assert ("order", "joined_at.asc") in params(rec.last)


# ---------- rpc ----------

@pytest.mark.parametrize(
    "call, rpc, expected_body",
    [
        (lambda s: s.join(PARTY, "5"), "party_join", {"p_party": PARTY, "p_member": "5"}),
        (lambda s: s.remove_member(PARTY, "5", "9"), "party_remove_member",
         {"p_party": PARTY, "p_member": "5", "p_actor": "9"}),
        (lambda s: s.delete_expense(EXPENSE, "9"), "party_delete_expense", {"p_expense": EXPENSE, "p_actor": "9"}),
        (lambda s: s.close(PARTY, "9"), "party_close", {"p_party": PARTY, "p_actor": "9"}),
        (lambda s: s.toggle_paid(EXPENSE, "9"), "party_toggle_paid", {"p_transfer": EXPENSE, "p_actor": "9"}),
    ],
)
def test_rpc_calls_return_status(call, rpc, expected_body):
    rec = Recorder(httpx.Response(200, json="full"))
    assert call(make(rec)) == "full"
    assert rec.last.url.path == f"/rest/v1/rpc/{rpc}"
    assert body(rec.last) == expected_body


def test_save_expense_sends_shares_json():
    rec = Recorder(httpx.Response(200, json="ok"))
    status = make(rec).save_expense(EXPENSE, "9", amount_satang=10000, payer_id="1", note="หมูกระทะ",
                                    shares={"1": 3334, "2": 3333, "3": 3333})
    assert status == "ok"
    assert body(rec.last) == {"p_expense": EXPENSE, "p_actor": "9", "p_amount": 10000, "p_payer": "1",
                              "p_note": "หมูกระทะ", "p_shares": {"1": 3334, "2": 3333, "3": 3333}}


def test_create_transfers_sends_list():
    rows = [{"id": "t1", "seq": 1}]
    rec = Recorder(httpx.Response(200, json=rows))
    assert make(rec).create_transfers(PARTY, [Transfer("3", "1", 40000)]) == rows
    assert rec.last.url.path == "/rest/v1/rpc/party_create_transfers"
    assert body(rec.last) == {"p_party": PARTY, "p_transfers": [{"from_id": "3", "to_id": "1", "amount": 40000}]}


def test_rpc_http_error_raises():
    rec = Recorder(httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        make(rec).join(PARTY, "5")


# ---------- expenses ----------

def test_create_draft_body():
    rec = Recorder(httpx.Response(201, json=[{"id": EXPENSE}]))
    make(rec).create_draft(PARTY, created_by="5", payer_id="5", participants=["5", "9"], amount_satang=None,
                           note="", source="slip", trans_ref="R1", image_sha256="H1", slip_message_url="u")
    b = body(rec.last)
    assert b["status"] == "draft" and b["amount_satang"] is None and b["participants"] == ["5", "9"]
    assert b["trans_ref"] == "R1" and b["image_sha256"] == "H1" and b["party_id"] == PARTY


def test_update_draft_only_drafts():
    rec = Recorder(httpx.Response(200, json=[]))
    assert make(rec).update_draft(EXPENSE, payer_id="9") is None
    p = params(rec.last)
    assert ("id", f"eq.{EXPENSE}") in p and ("status", "eq.draft") in p
    assert rec.last.method == "PATCH" and body(rec.last)["payer_id"] == "9"


def test_get_expense_embeds_shares():
    row = {"id": EXPENSE, "party_expense_shares": [{"discord_id": "1", "share_satang": 5}]}
    rec = Recorder(httpx.Response(200, json=[row]))
    assert make(rec).get_expense(EXPENSE) == row
    assert dict(params(rec.last))["select"] == "*,party_expense_shares(discord_id,share_satang)"


def test_confirmed_expenses_query():
    rec = Recorder(httpx.Response(200, json=[]))
    make(rec).confirmed_expenses(PARTY)
    p = params(rec.last)
    assert ("party_id", f"eq.{PARTY}") in p and ("status", "eq.confirmed") in p
    assert ("order", "created_at.asc") in p


def test_is_duplicate_slip_checks_confirmed_ref_or_hash():
    rec = Recorder(httpx.Response(200, json=[{"id": "x"}]))
    assert make(rec).is_duplicate_slip(PARTY, trans_ref="R1", image_sha256="H1", exclude_expense_id=EXPENSE) is True
    p = params(rec.last)
    assert ("status", "eq.confirmed") in p and ("party_id", f"eq.{PARTY}") in p
    assert ("or", '(trans_ref.eq."R1",image_sha256.eq."H1")') in p
    assert ("id", f"neq.{EXPENSE}") in p


def test_is_duplicate_slip_hash_only():
    rec = Recorder(httpx.Response(200, json=[]))
    assert make(rec).is_duplicate_slip(PARTY, trans_ref=None, image_sha256="H1") is False
    assert ("image_sha256", "eq.H1") in params(rec.last)


def test_is_duplicate_slip_nothing_to_compare():
    rec = Recorder()
    assert make(rec).is_duplicate_slip(PARTY, trans_ref=None, image_sha256=None) is False
    assert rec.requests == []
