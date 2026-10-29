"""Tests สำหรับ API ปาร์ตี้ (web/parties.py) — FakePartyStore จำลองกติกาเดียวกับ supabase/party_schema.sql"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from config import Config
from party_math import split
from web.app import create_app
from web.auth import AuthError, AuthUser
from web.slips import SlipDraft

A = AuthUser(id="aaaaaaaa-0000-4000-8000-00000000000a", email="a@x.com", discord_id="1", providers=("discord",), name="เอ")
B = AuthUser(id="aaaaaaaa-0000-4000-8000-00000000000b", email="b@x.com", discord_id=None, providers=("google",), name="บี")
C = AuthUser(id="aaaaaaaa-0000-4000-8000-00000000000c", email="c@x.com", discord_id=None, providers=("email",), name="ซี")
X = AuthUser(id="aaaaaaaa-0000-4000-8000-0000000000ff", email="x@x.com", discord_id=None, providers=("email",), name="นอก")
USERS = {"tok-a": A, "tok-b": B, "tok-c": C, "tok-x": X}
H = {u.name: {"Authorization": f"Bearer {tok}"} for tok, u in USERS.items()}
HA, HB, HC, HX = H["เอ"], H["บี"], H["ซี"], H["นอก"]

CFG = Config(
    typhoon_api_key="k", typhoon_llm_model="m", supabase_url="https://proj.supabase.co",
    supabase_anon_key="sb_publishable_x", supabase_service_role_key="sb_secret_x", public_base_url="",
    categories=("อาหาร",),
)


class FakeAuth:
    def get_user(self, token):
        if token not in USERS:
            raise AuthError("bad")
        return USERS[token]


class FakeExpenseStore:
    def claim_discord_expenses(self, user_id, discord_id):
        return 0

    def is_duplicate(self, *a, **k):
        return False


class FakePartyStore:
    def __init__(self) -> None:
        self.parties: dict[str, dict] = {}
        self.members_: dict[str, list[dict]] = {}
        self.expenses_: dict[str, dict] = {}
        self.transfers_: dict[str, list[dict]] = {}

    # parties
    def create_party(self, *, name, host_id, host_name):
        pid = str(uuid.uuid4())
        self.parties[pid] = {"id": pid, "code": f"C{len(self.parties):05d}"[:6].replace("0", "Z"), "name": name,
                             "host_id": host_id, "status": "open", "created_at": "2026-10-29T10:00:00+00:00"}
        self.members_[pid] = [{"user_id": host_id, "display_name": host_name}]
        return dict(self.parties[pid])

    def get_party(self, pid):
        return dict(self.parties[pid]) if pid in self.parties else None

    def party_by_code(self, code):
        return next((dict(p) for p in self.parties.values() if p["code"] == code.strip().upper()), None)

    def parties_of(self, user_id):
        return [dict(self.parties[pid]) for pid, ms in self.members_.items() if any(m["user_id"] == user_id for m in ms)]

    def members(self, pid):
        return [dict(m) for m in self.members_.get(pid, [])]

    def _ids(self, pid):
        return [m["user_id"] for m in self.members_.get(pid, [])]

    def join(self, pid, user_id, name):
        p = self.parties.get(pid)
        if p is None:
            return "not_found"
        if p["status"] != "open":
            return "closed"
        if user_id in self._ids(pid):
            return "already"
        if len(self._ids(pid)) >= 10:
            return "full"
        self.members_[pid].append({"user_id": user_id, "display_name": name})
        return "ok"

    def remove_member(self, pid, member, actor):
        p = self.parties.get(pid)
        if p is None:
            return "not_found"
        if p["status"] != "open":
            return "closed"
        if actor != member and actor != p["host_id"]:
            return "forbidden"
        if member == p["host_id"]:
            return "host"
        if member not in self._ids(pid):
            return "not_member"
        for e in self.expenses_.values():
            if e["party_id"] == pid and (e["payer_id"] == member or member in e["shares"]):
                return "involved"
        self.members_[pid] = [m for m in self.members_[pid] if m["user_id"] != member]
        return "ok"

    def close(self, pid, actor):
        p = self.parties.get(pid)
        if p is None:
            return "not_found"
        if actor != p["host_id"]:
            return "forbidden"
        if p["status"] != "open":
            return "already_closed"
        p["status"] = "closed"
        return "ok"

    # expenses
    def _valid(self, pid, amount, payer, shares):
        ids = self._ids(pid)
        return amount > 0 and payer in ids and shares and all(k in ids for k in shares) and sum(shares.values()) == amount

    def add_expense(self, pid, actor, *, amount_satang, payer_id, note, source, trans_ref, image_sha256, shares):
        p = self.parties.get(pid)
        if p is None:
            return "not_found", None
        if p["status"] != "open":
            return "closed", None
        if actor not in self._ids(pid):
            return "not_member", None
        if not self._valid(pid, amount_satang, payer_id, shares):
            return "invalid", None
        for e in self.expenses_.values():
            if e["party_id"] == pid and ((trans_ref and e["trans_ref"] == trans_ref)
                                         or (image_sha256 and e["image_sha256"] == image_sha256)):
                return "duplicate", None
        eid = str(uuid.uuid4())
        self.expenses_[eid] = {"id": eid, "party_id": pid, "amount_satang": amount_satang, "payer_id": payer_id,
                               "created_by": actor, "note": note, "source": source, "trans_ref": trans_ref,
                               "image_sha256": image_sha256, "shares": dict(shares),
                               "created_at": f"2026-10-29T10:{len(self.expenses_):02d}:00+00:00"}
        return "ok", eid

    def update_expense(self, eid, actor, *, amount_satang, payer_id, note, shares):
        e = self.expenses_.get(eid)
        if e is None:
            return "not_found"
        p = self.parties[e["party_id"]]
        if p["status"] != "open":
            return "closed"
        if actor not in (e["created_by"], p["host_id"]):
            return "forbidden"
        if not self._valid(p["id"], amount_satang, payer_id, shares):
            return "invalid"
        e.update(amount_satang=amount_satang, payer_id=payer_id, note=note, shares=dict(shares))
        return "ok"

    def delete_expense(self, eid, actor):
        e = self.expenses_.get(eid)
        if e is None:
            return "not_found"
        p = self.parties[e["party_id"]]
        if p["status"] != "open":
            return "closed"
        if actor not in (e["created_by"], p["host_id"]):
            return "forbidden"
        del self.expenses_[eid]
        return "ok"

    def expenses(self, pid):
        rows = []
        for e in sorted(self.expenses_.values(), key=lambda e: e["created_at"]):
            if e["party_id"] == pid:
                row = {k: v for k, v in e.items() if k != "shares"}
                row["party_expense_shares"] = [{"user_id": k, "share_satang": v} for k, v in e["shares"].items()]
                rows.append(row)
        return rows

    # transfers
    def transfers(self, pid):
        return [dict(t) for t in self.transfers_.get(pid, [])]

    def create_transfers(self, pid, transfers):
        p = self.parties[pid]
        if p["status"] == "closed" and not self.transfers_.get(pid):
            self.transfers_[pid] = [
                {"id": str(uuid.uuid4()), "seq": i, "from_id": t.from_id, "to_id": t.to_id,
                 "amount_satang": t.amount, "paid": False} for i, t in enumerate(transfers, 1)
            ]
            if not transfers:
                p["status"] = "settled"
        return self.transfers(pid)

    def toggle_paid(self, tid, actor):
        for pid, ts in self.transfers_.items():
            for t in ts:
                if t["id"] == tid:
                    if self.parties[pid]["status"] != "closed":
                        return "locked"
                    if actor not in (t["from_id"], t["to_id"]):
                        return "forbidden"
                    t["paid"] = not t["paid"]
                    if t["paid"] and all(x["paid"] for x in ts):
                        self.parties[pid]["status"] = "settled"
                        return "settled"
                    return "paid" if t["paid"] else "unpaid"
        return "not_found"


@pytest.fixture
def ps():
    return FakePartyStore()


@pytest.fixture
def client(ps):
    def reader(data, mime, key, model):
        return SlipDraft(amount=104.0, date=None, time=None, receiver=None, bank="", trans_ref="REF9", error=None)

    app = create_app(CFG, store=FakeExpenseStore(), auth=FakeAuth(), reader=reader, party_store=ps)
    return TestClient(app)


def create(client, name="ทริปเขาใหญ่", headers=HA) -> dict:
    resp = client.post("/api/parties", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def join(client, party, headers):
    return client.post("/api/parties/join", json={"code": party["code"]}, headers=headers)


def add(client, party, headers=HA, **overrides):
    body = {"amount": "100", "note": "", "payer_id": A.id, "participants": [A.id], "source": "cash"}
    body.update(overrides)
    return client.post(f"/api/parties/{party['id']}/expenses", json=body, headers=headers)


@pytest.fixture
def trio(client):
    party = create(client)
    assert join(client, party, HB).status_code == 200
    assert join(client, party, HC).status_code == 200
    return party


# ---------- create / list / join ----------

def test_create_and_list(client):
    party = create(client)
    assert party["name"] == "ทริปเขาใหญ่" and len(party["code"]) == 6
    listed = client.get("/api/parties", headers=HA).json()
    assert [p["id"] for p in listed] == [party["id"]]
    assert listed[0]["member_count"] == 1 and listed[0]["my_net_satang"] == 0
    assert client.get("/api/parties", headers=HB).json() == []


@pytest.mark.parametrize("name", ["", " ", "x" * 101])
def test_create_bad_name_422(client, name):
    assert client.post("/api/parties", json={"name": name}, headers=HA).status_code == 422


def test_join_by_code(client, ps):
    party = create(client)
    resp = join(client, party, HB)
    assert resp.status_code == 200 and resp.json()["id"] == party["id"]
    assert join(client, party, HB).status_code == 200  # เข้าซ้ำ (เช่นกดลิงก์เชิญอีกครั้ง) → ไปหน้าปาร์ตี้ได้เลย
    assert [m["display_name"] for m in ps.members(party["id"])] == ["เอ", "บี"]
    resp = client.post("/api/parties/join", json={"code": "NOPE99"}, headers=HB)
    assert resp.status_code == 404 and "ไม่พบ" in resp.json()["detail"]


def test_join_full_and_closed_409(client, ps):
    party = create(client)
    for i in range(9):
        ps.join(party["id"], f"aaaaaaaa-0000-4000-8000-0000000001{i:02d}", "x")
    resp = join(client, party, HB)
    assert resp.status_code == 409 and "เต็ม" in resp.json()["detail"]
    other = create(client, "อีกปาร์ตี้")
    ps.parties[other["id"]]["status"] = "closed"
    assert join(client, other, HB).status_code == 409


def test_requires_login(client):
    assert client.get("/api/parties").status_code == 401


# ---------- privacy ----------

def test_non_member_gets_404_everywhere(client, trio, ps):
    pid = trio["id"]
    add(client, trio)
    eid = next(iter(ps.expenses_))
    calls = [
        ("get", f"/api/parties/{pid}", None),
        ("post", f"/api/parties/{pid}/expenses", {"amount": "1", "payer_id": X.id, "participants": [X.id]}),
        ("put", f"/api/parties/{pid}/expenses/{eid}", {"amount": "1", "payer_id": A.id, "participants": [A.id]}),
        ("delete", f"/api/parties/{pid}/expenses/{eid}", None),
        ("delete", f"/api/parties/{pid}/members/{B.id}", None),
        ("post", f"/api/parties/{pid}/end", None),
        ("post", f"/api/parties/{pid}/transfers/{uuid.uuid4()}/paid", None),
        ("get", f"/api/parties/{uuid.uuid4()}", None),
    ]
    for method, url, body in calls:
        kwargs = {"headers": HX}
        if body is not None:
            kwargs["json"] = body
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 404, (method, url, resp.status_code)
    assert len(ps.expenses_) == 1 and len(ps.members(pid)) == 3


def test_bad_party_id_422(client):
    assert client.get("/api/parties/not-a-uuid", headers=HA).status_code == 422


# ---------- detail ----------

def test_party_detail_shape(client, trio):
    add(client, trio, amount="900", payer_id=A.id, participants=[A.id, B.id, C.id], note="ที่พัก")
    d = client.get(f"/api/parties/{trio['id']}", headers=HB).json()
    assert d["party"]["code"] == trio["code"] and d["me"] == {"user_id": B.id, "is_host": False}
    assert [(m["name"], m["is_host"]) for m in d["members"]] == [("เอ", True), ("บี", False), ("ซี", False)]
    e = d["expenses"][0]
    assert e["amount_satang"] == 90000 and e["note"] == "ที่พัก" and e["can_edit"] is False
    assert sorted(s["share_satang"] for s in e["shares"]) == [30000, 30000, 30000]
    assert {b["user_id"]: b["net"] for b in d["balances"]} == {A.id: 60000, B.id: -30000, C.id: -30000}
    assert d["total_satang"] == 90000 and d["transfers"] == []


# ---------- expenses ----------

def test_add_expense_splits_and_saves(client, trio, ps):
    resp = add(client, trio, headers=HB, amount="100", payer_id=B.id, participants=[A.id, B.id, C.id])
    assert resp.status_code == 201
    shares = next(iter(ps.expenses_.values()))["shares"]
    assert shares == split(10000, [A.id, B.id, C.id]) and sum(shares.values()) == 10000
    assert sorted(shares.values()) == [3333, 3333, 3334]


@pytest.mark.parametrize(
    "overrides",
    [{"payer_id": X.id}, {"participants": [A.id, X.id]}, {"participants": []}, {"participants": [A.id, A.id]},
     {"payer_id": "not-a-uuid"}],
)
def test_add_expense_validates_people(client, trio, ps, overrides):
    assert add(client, trio, **overrides).status_code == 422
    assert ps.expenses_ == {}


@pytest.mark.parametrize("amount", ["", "abc", "0", "-5", "1.234", "1e3", "10000000.01"])
def test_add_expense_bad_amount_422(client, trio, amount):
    resp = add(client, trio, amount=amount)
    assert resp.status_code == 422


def test_duplicate_slip_409(client, trio):
    sha = "a" * 64
    assert add(client, trio, source="slip", trans_ref="R1", image_sha256=sha).status_code == 201
    resp = add(client, trio, headers=HB, payer_id=B.id, participants=[B.id], source="slip", trans_ref="R1")
    assert resp.status_code == 409 and "สลิปนี้ถูกบันทึกในปาร์ตี้แล้ว" in resp.json()["detail"]
    assert add(client, trio, source="slip", image_sha256=sha).status_code == 409


def test_update_delete_permissions(client, trio, ps):
    add(client, trio, headers=HB, payer_id=B.id, participants=[B.id])
    eid = next(iter(ps.expenses_))
    url = f"/api/parties/{trio['id']}/expenses/{eid}"
    body = {"amount": "50", "payer_id": B.id, "participants": [B.id, C.id], "note": "แก้"}
    assert client.put(url, json=body, headers=HC).status_code == 403
    assert client.put(url, json=body, headers=HB).status_code == 200
    assert ps.expenses_[eid]["shares"] == split(5000, [B.id, C.id])
    assert client.put(url, json={**body, "amount": "60"}, headers=HA).status_code == 200  # host แก้ได้
    assert client.delete(url, headers=HC).status_code == 403
    assert client.delete(url, headers=HA).status_code == 204
    assert ps.expenses_ == {}


def test_closed_party_rejects_changes(client, trio, ps):
    add(client, trio)
    eid = next(iter(ps.expenses_))
    assert client.post(f"/api/parties/{trio['id']}/end", headers=HA).status_code == 200
    assert add(client, trio).status_code == 409
    resp = client.put(f"/api/parties/{trio['id']}/expenses/{eid}",
                      json={"amount": "1", "payer_id": A.id, "participants": [A.id]}, headers=HA)
    assert resp.status_code == 409 and "ปิดแล้ว" in resp.json()["detail"]
    assert client.delete(f"/api/parties/{trio['id']}/expenses/{eid}", headers=HA).status_code == 409


# ---------- members ----------

def test_kick_and_leave(client, trio, ps):
    pid = trio["id"]
    assert client.delete(f"/api/parties/{pid}/members/{C.id}", headers=HB).status_code == 403  # ไม่ใช่ host
    assert client.delete(f"/api/parties/{pid}/members/{C.id}", headers=HA).status_code == 204  # host เตะ
    assert client.delete(f"/api/parties/{pid}/members/{B.id}", headers=HB).status_code == 204  # ออกเอง
    assert [m["user_id"] for m in ps.members(pid)] == [A.id]
    resp = client.delete(f"/api/parties/{pid}/members/{A.id}", headers=HA)
    assert resp.status_code == 409 and "host" in resp.json()["detail"]


def test_cannot_remove_involved_member(client, trio):
    add(client, trio, participants=[A.id, B.id])
    resp = client.delete(f"/api/parties/{trio['id']}/members/{B.id}", headers=HA)
    assert resp.status_code == 409 and "รายการ" in resp.json()["detail"]


# ---------- end / transfers ----------

def test_end_host_only(client, trio):
    resp = client.post(f"/api/parties/{trio['id']}/end", headers=HB)
    assert resp.status_code == 403 and "host" in resp.json()["detail"]


def test_end_creates_transfers_spec_example(client, trio):
    everyone = [A.id, B.id, C.id]
    add(client, trio, amount="900", payer_id=A.id, participants=everyone)
    add(client, trio, headers=HB, amount="300", payer_id=B.id, participants=everyone)
    d = client.post(f"/api/parties/{trio['id']}/end", headers=HA).json()
    assert d["party"]["status"] == "closed"
    assert [(t["from_id"], t["to_id"], t["amount_satang"]) for t in d["transfers"]] == [
        (C.id, A.id, 40000), (B.id, A.id, 10000)]
    again = client.post(f"/api/parties/{trio['id']}/end", headers=HA).json()  # กดซ้ำ → ชุดเดิม
    assert [t["id"] for t in again["transfers"]] == [t["id"] for t in d["transfers"]]


def test_end_with_nothing_owed_settles(client, trio):
    d = client.post(f"/api/parties/{trio['id']}/end", headers=HA).json()
    assert d["party"]["status"] == "settled" and d["transfers"] == []


def test_paid_toggle_and_settled(client, trio):
    add(client, trio, amount="300", payer_id=A.id, participants=[B.id, C.id])
    d = client.post(f"/api/parties/{trio['id']}/end", headers=HA).json()
    t1, t2 = d["transfers"]
    url = f"/api/parties/{trio['id']}/transfers/{{}}/paid"
    stranger = t2["from_id"]  # คนที่ไม่เกี่ยวกับรายการแรก
    assert client.post(url.format(t1["id"]), headers=HC if stranger == C.id else HB).status_code == 403
    me_t1 = HB if t1["from_id"] == B.id else HC
    d = client.post(url.format(t1["id"]), headers=me_t1).json()
    assert d["transfers"][0]["paid"] is True and d["party"]["status"] == "closed"
    me_t2 = HB if t2["from_id"] == B.id else HC
    d = client.post(url.format(t2["id"]), headers=me_t2).json()
    assert d["party"]["status"] == "settled"
    resp = client.post(url.format(t2["id"]), headers=HA)
    assert resp.status_code == 409


# ---------- slips / pages ----------

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_slips_read_returns_image_sha256(client):
    resp = client.post("/api/slips/read", files={"file": ("s.png", PNG, "image/png")}, headers=HA)
    assert resp.status_code == 200
    assert resp.json()["image_sha256"] == hashlib.sha256(PNG).hexdigest()


@pytest.mark.parametrize("path", ["/parties", f"/party/{uuid.uuid4()}", "/join/ABC234"])
def test_party_pages_served(client, path):
    resp = client.get(path)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")


# ---------- หน้าเว็บ/JS ต้องไม่ถูก browser ใช้ของเก่าจาก cache (หน้าปาร์ตี้ว่างเพราะ app.js เวอร์ชันเก่า) ----------

@pytest.mark.parametrize("path", ["/static/app.js", "/static/party.js", "/static/style.css", "/party/x", "/dashboard"])
def test_pages_and_static_must_revalidate(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
