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
U1 = "aaaaaaaa-0000-4000-8000-000000000001"
U2 = "aaaaaaaa-0000-4000-8000-000000000002"


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


# ---------- code / headers ----------

def test_new_code_alphabet_and_length():
    rng = random.Random(1)
    for _ in range(200):
        code = new_code(rng)
        assert len(code) == CODE_LENGTH and set(code) <= set(CODE_ALPHABET)
    assert not set("01OIL") & set(CODE_ALPHABET)


def test_headers_secret_key_only_apikey():
    rec = Recorder()
    make(rec).members(PARTY)
    assert rec.last.headers["apikey"] == "sb_secret_x" and "authorization" not in rec.last.headers
    assert rec.last.url.path == "/rest/v1/party_members"


# ---------- create ----------

PARTY_ROW = {"id": PARTY, "code": "ABC234", "name": "ทริป", "status": "open", "host_id": U1}


def test_create_party_calls_rpc_then_reads_party():
    rec = Recorder(httpx.Response(200, json=PARTY), httpx.Response(200, json=[PARTY_ROW]))
    assert make(rec).create_party(name="ทริป", host_id=U1, host_name="สมชาย") == PARTY_ROW
    create, read = rec.requests
    assert create.url.path == "/rest/v1/rpc/party_create"
    b = body(create)
    assert b["p_name"] == "ทริป" and b["p_host"] == U1 and b["p_host_name"] == "สมชาย"
    assert len(b["p_code"]) == CODE_LENGTH and set(b["p_code"]) <= set(CODE_ALPHABET)
    assert ("id", f"eq.{PARTY}") in params(read)


def test_create_party_retries_on_code_conflict():
    rec = Recorder(conflict(), httpx.Response(200, json=PARTY), httpx.Response(200, json=[PARTY_ROW]))
    assert make(rec).create_party(name="x", host_id=U1, host_name="") == PARTY_ROW
    assert [r.url.path for r in rec.requests[:2]] == ["/rest/v1/rpc/party_create"] * 2


def test_create_party_gives_up_after_5_conflicts():
    rec = Recorder(*[conflict() for _ in range(5)])
    with pytest.raises(RuntimeError):
        make(rec).create_party(name="x", host_id=U1, host_name="")
    assert len(rec.requests) == 5


# ---------- lookups ----------

def test_party_by_code_uppercases():
    rec = Recorder(httpx.Response(200, json=[PARTY_ROW]))
    assert make(rec).party_by_code(" abc234 ") == PARTY_ROW
    assert ("code", "eq.ABC234") in params(rec.last)


def test_parties_of_user():
    rec = Recorder(httpx.Response(200, json=[{"party_id": PARTY, "parties": PARTY_ROW}]))
    assert make(rec).parties_of(U1) == [PARTY_ROW]
    p = params(rec.last)
    assert ("user_id", f"eq.{U1}") in p and dict(p)["select"] == "party_id,parties(*)"


def test_members_in_join_order():
    rows = [{"user_id": U1, "display_name": "A"}, {"user_id": U2, "display_name": "B"}]
    rec = Recorder(httpx.Response(200, json=rows))
    assert make(rec).members(PARTY) == rows
    assert ("order", "joined_at.asc") in params(rec.last) and ("party_id", f"eq.{PARTY}") in params(rec.last)


def test_expenses_embed_shares():
    rec = Recorder(httpx.Response(200, json=[]))
    make(rec).expenses(PARTY)
    p = params(rec.last)
    assert dict(p)["select"] == "*,party_expense_shares(user_id,share_satang)"
    assert ("party_id", f"eq.{PARTY}") in p and ("order", "created_at.asc") in p


def test_transfers_in_seq_order():
    rec = Recorder(httpx.Response(200, json=[]))
    make(rec).transfers(PARTY)
    assert ("order", "seq.asc") in params(rec.last)


# ---------- rpc ----------

@pytest.mark.parametrize(
    "call, rpc, expected_body",
    [
        (lambda s: s.join(PARTY, U2, "บี"), "party_join", {"p_party": PARTY, "p_user": U2, "p_name": "บี"}),
        (lambda s: s.remove_member(PARTY, U2, U1), "party_remove_member",
         {"p_party": PARTY, "p_member": U2, "p_actor": U1}),
        (lambda s: s.delete_expense(EXPENSE, U1), "party_delete_expense", {"p_expense": EXPENSE, "p_actor": U1}),
        (lambda s: s.close(PARTY, U1), "party_close", {"p_party": PARTY, "p_actor": U1}),
        (lambda s: s.toggle_paid(EXPENSE, U1), "party_toggle_paid", {"p_transfer": EXPENSE, "p_actor": U1}),
    ],
)
def test_rpc_calls_return_status(call, rpc, expected_body):
    rec = Recorder(httpx.Response(200, json="full"))
    assert call(make(rec)) == "full"
    assert rec.last.url.path == f"/rest/v1/rpc/{rpc}"
    assert body(rec.last) == expected_body


def test_add_expense_returns_status_and_id():
    rec = Recorder(httpx.Response(200, json={"status": "ok", "id": EXPENSE}))
    shares = {U1: 5000, U2: 5000}
    result = make(rec).add_expense(PARTY, U1, amount_satang=10000, payer_id=U1, note="หมูกระทะ", source="slip",
                                   trans_ref="R1", image_sha256="H1", shares=shares)
    assert result == ("ok", EXPENSE)
    assert rec.last.url.path == "/rest/v1/rpc/party_add_expense"
    assert body(rec.last) == {"p_party": PARTY, "p_actor": U1, "p_amount": 10000, "p_payer": U1, "p_note": "หมูกระทะ",
                              "p_source": "slip", "p_trans_ref": "R1", "p_sha": "H1", "p_shares": shares}


def test_add_expense_error_status_has_no_id():
    rec = Recorder(httpx.Response(200, json={"status": "duplicate"}))
    assert make(rec).add_expense(PARTY, U1, amount_satang=1, payer_id=U1, note="", source="cash", trans_ref=None,
                                 image_sha256=None, shares={U1: 1}) == ("duplicate", None)


def test_update_expense_body():
    rec = Recorder(httpx.Response(200, json="ok"))
    assert make(rec).update_expense(EXPENSE, U1, amount_satang=300, payer_id=U2, note="", shares={U1: 300}) == "ok"
    assert body(rec.last) == {"p_expense": EXPENSE, "p_actor": U1, "p_amount": 300, "p_payer": U2, "p_note": "",
                              "p_shares": {U1: 300}}


def test_create_transfers_sends_list():
    rows = [{"id": "t1", "seq": 1}]
    rec = Recorder(httpx.Response(200, json=rows))
    assert make(rec).create_transfers(PARTY, [Transfer(U2, U1, 40000)]) == rows
    assert body(rec.last) == {"p_party": PARTY, "p_transfers": [{"from_id": U2, "to_id": U1, "amount": 40000}]}


def test_rpc_http_error_raises():
    rec = Recorder(httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        make(rec).join(PARTY, U2, "")
