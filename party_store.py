"""เก็บข้อมูลปาร์ตี้ใน Supabase ผ่าน PostgREST + เรียก SQL functions ที่ล็อกแถว (supabase/party_schema.sql)

สมาชิก = บัญชีเว็บ (uuid ของ Supabase Auth) ; ใช้ service-role key ; ทุก method เป็น blocking (web ห่อด้วย asyncio.to_thread)
สถานะที่ RPC คืนเป็นสตริงสั้น ๆ (เช่น "ok", "full") — web/parties.py แปลเป็น HTTP status + ข้อความไทย
ทดสอบกับ Supabase จริง: python party_store.py   (ต้องรัน supabase/party_schema.sql ก่อน)
"""
from __future__ import annotations

import random
import secrets
import sys
from typing import Any

import httpx

from party_math import Transfer

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # ตัด 0 O 1 I L ที่ดูคล้ายกัน
CODE_LENGTH = 6
MAX_MEMBERS = 10
CODE_ATTEMPTS = 5
UNIQUE_VIOLATION = "23505"
EXPENSE_SELECT = "*,party_expense_shares(user_id,share_satang)"


def new_code(rng: random.Random | None = None) -> str:
    pick = rng.choice if rng is not None else secrets.choice
    return "".join(pick(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class PartyStore:
    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._rest = supabase_url.rstrip("/") + "/rest/v1"
        self._headers = {"apikey": service_role_key}
        # key แบบใหม่ (sb_secret_...) ไม่ใช่ JWT ส่งใน Authorization ไม่ได้; key แบบเก่า (JWT) ต้องส่งทั้งสองที่
        if not service_role_key.startswith("sb_"):
            self._headers["Authorization"] = f"Bearer {service_role_key}"
        self._client = client or httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------ parties

    def create_party(self, *, name: str, host_id: str, host_name: str) -> dict:
        """สร้างปาร์ตี้ + ใส่ host เป็นสมาชิก (atomic) ด้วยโค้ดสุ่ม — ชนกับที่มี → สุ่มใหม่"""
        for _ in range(CODE_ATTEMPTS):
            resp = self._request("POST", "rpc/party_create", json={
                "p_code": new_code(), "p_name": name, "p_host": host_id, "p_host_name": host_name})
            if _is_unique_violation(resp):
                continue
            resp.raise_for_status()
            party = self.get_party(resp.json())
            if party is None:
                raise RuntimeError("สร้างปาร์ตี้แล้วแต่อ่านกลับไม่ได้")
            return party
        raise RuntimeError("สร้างโค้ดปาร์ตี้ที่ไม่ซ้ำไม่สำเร็จ")

    def get_party(self, party_id: str) -> dict | None:
        return self._one("parties", [("id", f"eq.{party_id}")])

    def party_by_code(self, code: str) -> dict | None:
        return self._one("parties", [("code", f"eq.{(code or '').strip().upper()}")])

    def parties_of(self, user_id: str) -> list[dict]:
        """ปาร์ตี้ที่ผู้ใช้เป็นสมาชิก"""
        rows = self._get("party_members", [("select", "party_id,parties(*)"), ("user_id", f"eq.{user_id}")])
        return [r["parties"] for r in rows if r.get("parties")]

    def members(self, party_id: str) -> list[dict]:
        """[{user_id, display_name, joined_at}] เรียงตามเวลาที่เข้าร่วม"""
        return self._get("party_members", [("select", "user_id,display_name,joined_at"),
                                           ("party_id", f"eq.{party_id}"), ("order", "joined_at.asc")])

    def join(self, party_id: str, user_id: str, name: str) -> str:
        return self._rpc("party_join", p_party=party_id, p_user=user_id, p_name=name)

    def remove_member(self, party_id: str, member_id: str, actor_id: str) -> str:
        return self._rpc("party_remove_member", p_party=party_id, p_member=member_id, p_actor=actor_id)

    def close(self, party_id: str, actor_id: str) -> str:
        return self._rpc("party_close", p_party=party_id, p_actor=actor_id)

    # ------------------------------------------------------------------ expenses

    def add_expense(
        self,
        party_id: str,
        actor_id: str,
        *,
        amount_satang: int,
        payer_id: str,
        note: str,
        source: str,
        trans_ref: str | None,
        image_sha256: str | None,
        shares: dict[str, int],
    ) -> tuple[str, str | None]:
        """คืน (สถานะ, id ของรายการใหม่ถ้า ok)"""
        result = self._rpc("party_add_expense", p_party=party_id, p_actor=actor_id, p_amount=amount_satang,
                           p_payer=payer_id, p_note=note, p_source=source, p_trans_ref=trans_ref,
                           p_sha=image_sha256, p_shares=shares)
        return result["status"], result.get("id")

    def update_expense(
        self, expense_id: str, actor_id: str, *, amount_satang: int, payer_id: str, note: str, shares: dict[str, int]
    ) -> str:
        return self._rpc("party_update_expense", p_expense=expense_id, p_actor=actor_id, p_amount=amount_satang,
                         p_payer=payer_id, p_note=note, p_shares=shares)

    def delete_expense(self, expense_id: str, actor_id: str) -> str:
        return self._rpc("party_delete_expense", p_expense=expense_id, p_actor=actor_id)

    def expenses(self, party_id: str) -> list[dict]:
        return self._get("party_expenses", [("select", EXPENSE_SELECT), ("party_id", f"eq.{party_id}"),
                                            ("order", "created_at.asc")])

    # ------------------------------------------------------------------ transfers

    def transfers(self, party_id: str) -> list[dict]:
        return self._get("party_transfers", [("select", "*"), ("party_id", f"eq.{party_id}"), ("order", "seq.asc")])

    def create_transfers(self, party_id: str, transfers: list[Transfer]) -> list[dict]:
        """บันทึกรายการโอนครั้งเดียว (เรียกซ้ำได้ คืนชุดเดิม) ; ไม่มีรายการ → ปาร์ตี้เป็น settled"""
        items = [{"from_id": t.from_id, "to_id": t.to_id, "amount": t.amount} for t in transfers]
        return self._rpc("party_create_transfers", p_party=party_id, p_transfers=items)

    def toggle_paid(self, transfer_id: str, actor_id: str) -> str:
        return self._rpc("party_toggle_paid", p_transfer=transfer_id, p_actor=actor_id)

    # ------------------------------------------------------------------ internals

    def _rpc(self, name: str, **args: Any) -> Any:
        resp = self._request("POST", f"rpc/{name}", json=args)
        resp.raise_for_status()
        return resp.json()

    def _get(self, table: str, params: list[tuple[str, str]]) -> list[dict]:
        resp = self._request("GET", table, params=params)
        resp.raise_for_status()
        return resp.json()

    def _one(self, table: str, filters: list[tuple[str, str]]) -> dict | None:
        rows = self._get(table, [("select", "*"), *filters, ("limit", "1")])
        return rows[0] if rows else None

    def _request(self, method: str, path: str, *, prefer: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        return self._client.request(method, f"{self._rest}/{path}", headers=headers, **kwargs)


def _is_unique_violation(resp: httpx.Response) -> bool:
    if resp.status_code != 409:
        return False
    try:
        return resp.json().get("code") == UNIQUE_VIOLATION
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------- smoke test


def _smoke_test() -> int:
    """ทดสอบ SQL functions กับ Supabase จริง (ใช้ uuid สุ่มแทนผู้ใช้) แล้วลบปาร์ตี้ทดสอบทิ้ง"""
    import uuid

    from config import ConfigError, load_config
    from party_math import balances, settle, split, Expense

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = load_config(required=["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
    except ConfigError as exc:
        print(f"ตั้งค่าไม่ครบ: {exc}")
        return 1

    store = PartyStore(cfg.supabase_url, cfg.supabase_service_role_key)
    failures: list[str] = []

    def check(label: str, got: Any, expected: Any) -> None:
        ok = got == expected
        print(f"{'✅' if ok else '❌'} {label}: {got!r}" + ("" if ok else f" (ควรเป็น {expected!r})"))
        if not ok:
            failures.append(label)

    users = [str(uuid.uuid4()) for _ in range(11)]
    host, a, b = users[0], users[1], users[2]
    party = store.create_party(name="SMOKE TEST", host_id=host, host_name="host")
    pid = party["id"]
    try:
        check("host เป็นสมาชิก", [m["user_id"] for m in store.members(pid)], [host])
        check("parties_of", [p["id"] for p in store.parties_of(host)], [pid])
        for i, user in enumerate(users[1:10], start=2):
            check(f"join คนที่ {i}", store.join(pid, user, f"user{i}"), "ok")
        check("join คนที่ 11", store.join(pid, users[10], "x"), "full")
        check("join ซ้ำ", store.join(pid, a, "a"), "already")

        trio = [host, a, b]
        check("ผลรวม shares ผิด", store.add_expense(pid, a, amount_satang=10000, payer_id=a, note="", source="slip",
                                                    trans_ref="SMOKEREF", image_sha256="smokehash",
                                                    shares={host: 1, a: 1})[0], "invalid")
        check("คนนอกเพิ่มรายการ", store.add_expense(pid, users[10], amount_satang=100, payer_id=a, note="",
                                                    source="cash", trans_ref=None, image_sha256=None,
                                                    shares={a: 100})[0], "not_member")
        status, eid = store.add_expense(pid, a, amount_satang=10000, payer_id=a, note="ทดสอบ", source="slip",
                                        trans_ref="SMOKEREF", image_sha256="smokehash", shares=split(10000, trio))
        check("เพิ่มรายการ", status, "ok")
        check("สลิปซ้ำ (ref)", store.add_expense(pid, b, amount_satang=500, payer_id=b, note="", source="slip",
                                                 trans_ref="SMOKEREF", image_sha256=None,
                                                 shares=split(500, trio))[0], "duplicate")
        check("สลิปซ้ำ (รูป)", store.add_expense(pid, b, amount_satang=500, payer_id=b, note="", source="slip",
                                                 trans_ref=None, image_sha256="smokehash",
                                                 shares=split(500, trio))[0], "duplicate")
        check("แก้โดยคนอื่น", store.update_expense(eid, b, amount_satang=9000, payer_id=a, note="",
                                                   shares=split(9000, trio)), "forbidden")
        check("host แก้ได้", store.update_expense(eid, host, amount_satang=9000, payer_id=a, note="แก้",
                                                  shares=split(9000, trio)), "ok")
        check("shares หลังแก้", sorted(s["share_satang"] for s in store.expenses(pid)[0]["party_expense_shares"]),
              [3000, 3000, 3000])
        check("เตะคนที่มีรายการ", store.remove_member(pid, a, host), "involved")
        check("เตะคนไม่มีรายการ", store.remove_member(pid, users[9], host), "ok")
        check("ออกเองไม่ใช่ host", store.remove_member(pid, users[8], users[7]), "forbidden")
        check("host ออก", store.remove_member(pid, host, host), "host")

        check("ปิดโดยคนอื่น", store.close(pid, a), "forbidden")
        check("ปิด", store.close(pid, host), "ok")
        check("เพิ่มหลังปิด", store.add_expense(pid, a, amount_satang=100, payer_id=a, note="", source="cash",
                                                trans_ref=None, image_sha256=None, shares={a: 100})[0], "closed")
        check("join หลังปิด", store.join(pid, users[10], "x"), "closed")

        rows = store.expenses(pid)
        exps = [Expense(r["payer_id"], r["amount_satang"], tuple(s["user_id"] for s in r["party_expense_shares"]))
                for r in rows]
        transfers = settle({k: v.net for k, v in balances([m["user_id"] for m in store.members(pid)], exps).items()})
        first = store.create_transfers(pid, transfers)
        check("จำนวนรายการโอน", len(first), 2)
        check("สร้างซ้ำได้ชุดเดิม", [t["id"] for t in store.create_transfers(pid, transfers)], [t["id"] for t in first])
        check("คนนอกกดโอนแล้ว", store.toggle_paid(first[0]["id"], users[5]), "forbidden")
        check("กดโอนแล้ว", store.toggle_paid(first[0]["id"], first[0]["from_id"]), "paid")
        check("กดซ้ำเพื่อยกเลิก", store.toggle_paid(first[0]["id"], first[0]["to_id"]), "unpaid")
        check("กดอีกครั้ง", store.toggle_paid(first[0]["id"], first[0]["from_id"]), "paid")
        check("รายการสุดท้าย", store.toggle_paid(first[1]["id"], first[1]["to_id"]), "settled")
        check("หลัง settled", store.toggle_paid(first[1]["id"], first[1]["to_id"]), "locked")
        check("สถานะ", store.get_party(pid)["status"], "settled")
    finally:
        store._request("DELETE", "parties", params=[("id", f"eq.{pid}")]).raise_for_status()
        print("ลบปาร์ตี้ทดสอบแล้ว")
    print("ผ่านทั้งหมด" if not failures else f"ไม่ผ่าน {len(failures)} ข้อ")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(_smoke_test())
