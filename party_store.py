"""เก็บข้อมูลปาร์ตี้ใน Supabase ผ่าน PostgREST + เรียก SQL functions ที่ล็อกแถว (supabase/party_schema.sql)

ใช้ service-role key ; ทุก method เป็น blocking (party_bot ห่อด้วย asyncio.to_thread)
สถานะที่ RPC คืนเป็นสตริงสั้น ๆ (เช่น "ok", "full") — party_bot แปลเป็นข้อความไทย
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
EXPENSE_SELECT = "*,party_expense_shares(discord_id,share_satang)"


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

    def create_party(self, *, name: str, host_id: str, guild_id: str, channel_id: str, thread_id: str) -> dict:
        """สร้างปาร์ตี้ด้วยโค้ดสุ่ม (ชนกับที่มี → สุ่มใหม่) แล้วใส่ host เป็นสมาชิกคนแรก"""
        for _ in range(CODE_ATTEMPTS):
            body = {"code": new_code(), "name": name, "host_id": host_id, "guild_id": guild_id,
                    "channel_id": channel_id, "thread_id": thread_id}
            resp = self._request("POST", "parties", json=body, prefer="return=representation")
            if _is_unique_violation(resp):
                continue
            resp.raise_for_status()
            party = resp.json()[0]
            self._request("POST", "party_members", json={"party_id": party["id"], "discord_id": host_id}
                          ).raise_for_status()
            return party
        raise RuntimeError("สร้างโค้ดปาร์ตี้ที่ไม่ซ้ำไม่สำเร็จ")

    def update_party(self, party_id: str, **fields: Any) -> None:
        self._request("PATCH", "parties", params=[("id", f"eq.{party_id}")], json=fields).raise_for_status()

    def get_party(self, party_id: str) -> dict | None:
        return self._one("parties", [("id", f"eq.{party_id}")])

    def party_by_thread(self, thread_id: str) -> dict | None:
        return self._one("parties", [("thread_id", f"eq.{thread_id}")])

    def party_by_code(self, code: str) -> dict | None:
        return self._one("parties", [("code", f"eq.{(code or '').strip().upper()}")])

    def members(self, party_id: str) -> list[str]:
        rows = self._get("party_members", [("select", "discord_id"), ("party_id", f"eq.{party_id}"),
                                           ("order", "joined_at.asc")])
        return [r["discord_id"] for r in rows]

    def join(self, party_id: str, member_id: str) -> str:
        return self._rpc("party_join", p_party=party_id, p_member=member_id)

    def remove_member(self, party_id: str, member_id: str, actor_id: str) -> str:
        return self._rpc("party_remove_member", p_party=party_id, p_member=member_id, p_actor=actor_id)

    def close(self, party_id: str, actor_id: str) -> str:
        """open → closed (host เท่านั้น) และลบ draft ที่ค้าง"""
        return self._rpc("party_close", p_party=party_id, p_actor=actor_id)

    # ------------------------------------------------------------------ expenses

    def create_draft(
        self,
        party_id: str,
        *,
        created_by: str,
        payer_id: str,
        participants: list[str],
        amount_satang: int | None,
        note: str,
        source: str,
        trans_ref: str | None = None,
        image_sha256: str | None = None,
        slip_message_url: str | None = None,
    ) -> dict:
        body = {"party_id": party_id, "status": "draft", "created_by": created_by, "payer_id": payer_id,
                "participants": participants, "amount_satang": amount_satang, "note": note, "source": source,
                "trans_ref": trans_ref, "image_sha256": image_sha256, "slip_message_url": slip_message_url}
        resp = self._request("POST", "party_expenses", json=body, prefer="return=representation")
        resp.raise_for_status()
        return resp.json()[0]

    def get_expense(self, expense_id: str) -> dict | None:
        return self._one("party_expenses", [("id", f"eq.{expense_id}")], select=EXPENSE_SELECT)

    def update_draft(self, expense_id: str, **fields: Any) -> dict | None:
        """แก้ได้เฉพาะ draft — None ถ้าไม่ใช่ draft แล้ว (ยืนยัน/ลบไปแล้ว)"""
        resp = self._request(
            "PATCH", "party_expenses", params=[("id", f"eq.{expense_id}"), ("status", "eq.draft")],
            json=fields, prefer="return=representation",
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def update_expense_message(self, expense_id: str, message_id: str) -> None:
        self._request("PATCH", "party_expenses", params=[("id", f"eq.{expense_id}")],
                      json={"summary_message_id": message_id}).raise_for_status()

    def is_duplicate_slip(
        self,
        party_id: str,
        *,
        trans_ref: str | None,
        image_sha256: str | None,
        exclude_expense_id: str | None = None,
    ) -> bool:
        """สลิปนี้ (เลขอ้างอิงหรือรูปเดียวกัน) ถูกยืนยันในปาร์ตี้แล้วหรือยัง"""
        checks = [(col, value) for col, value in (("trans_ref", trans_ref), ("image_sha256", image_sha256)) if value]
        if not checks:
            return False
        if len(checks) == 1:
            match = [(checks[0][0], f"eq.{checks[0][1]}")]
        else:
            match = [("or", "(" + ",".join(f'{col}.eq."{value}"' for col, value in checks) + ")")]
        params = [("select", "id"), ("party_id", f"eq.{party_id}"), ("status", "eq.confirmed"), *match]
        if exclude_expense_id:
            params.append(("id", f"neq.{exclude_expense_id}"))
        return bool(self._get("party_expenses", [*params, ("limit", "1")]))

    def save_expense(
        self, expense_id: str, actor_id: str, *, amount_satang: int, payer_id: str, note: str, shares: dict[str, int]
    ) -> str:
        """ยืนยัน draft หรือแก้รายการที่ยืนยันแล้ว (ตรวจสิทธิ์/สมาชิก/ผลรวม/สลิปซ้ำในฐานข้อมูล)"""
        return self._rpc("party_save_expense", p_expense=expense_id, p_actor=actor_id, p_amount=amount_satang,
                         p_payer=payer_id, p_note=note, p_shares=shares)

    def delete_expense(self, expense_id: str, actor_id: str) -> str:
        return self._rpc("party_delete_expense", p_expense=expense_id, p_actor=actor_id)

    def confirmed_expenses(self, party_id: str) -> list[dict]:
        return self._get("party_expenses", [("select", EXPENSE_SELECT), ("party_id", f"eq.{party_id}"),
                                            ("status", "eq.confirmed"), ("order", "created_at.asc")])

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

    def _one(self, table: str, filters: list[tuple[str, str]], *, select: str = "*") -> dict | None:
        rows = self._get(table, [("select", select), *filters, ("limit", "1")])
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
    """ทดสอบ SQL functions กับ Supabase จริง แล้วลบปาร์ตี้ทดสอบทิ้ง"""
    import time

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

    host = "100"
    party = store.create_party(name="SMOKE TEST", host_id=host, guild_id="0", channel_id="0",
                               thread_id=f"smoke-{time.time_ns()}")
    pid = party["id"]
    try:
        for i in range(1, 10):
            check(f"join คนที่ {i + 1}", store.join(pid, str(100 + i)), "ok")
        check("join คนที่ 11", store.join(pid, "200"), "full")
        check("join ซ้ำ", store.join(pid, "101"), "already")
        check("สมาชิก", len(store.members(pid)), MAX_MEMBERS)

        members = ["100", "101", "102"]
        draft = store.create_draft(pid, created_by="101", payer_id="101", participants=members, amount_satang=None,
                                   note="", source="slip", trans_ref="SMOKEREF", image_sha256="smokehash")
        check("ผลรวม shares ผิด", store.save_expense(draft["id"], "101", amount_satang=10000, payer_id="101", note="",
                                                     shares={"100": 1, "101": 1}), "invalid")
        check("คนอื่นยืนยันแทน", store.save_expense(draft["id"], "102", amount_satang=10000, payer_id="101",
                                                    note="", shares=split(10000, members)), "forbidden")
        check("ยืนยัน", store.save_expense(draft["id"], "101", amount_satang=10000, payer_id="101", note="ทดสอบ",
                                           shares=split(10000, members)), "ok")
        check("is_duplicate_slip", store.is_duplicate_slip(pid, trans_ref="SMOKEREF", image_sha256=None), True)
        dup = store.create_draft(pid, created_by="102", payer_id="102", participants=members, amount_satang=5000,
                                 note="", source="slip", trans_ref="SMOKEREF")
        check("สลิปซ้ำ", store.save_expense(dup["id"], "102", amount_satang=5000, payer_id="102", note="",
                                            shares=split(5000, members)), "duplicate")
        check("เตะคนที่มีรายการ", store.remove_member(pid, "101", host), "involved")
        check("เตะคนไม่มีรายการ", store.remove_member(pid, "109", host), "ok")
        check("ออกเองไม่ใช่ host", store.remove_member(pid, "108", "107"), "forbidden")
        check("host ออก", store.remove_member(pid, host, host), "host")

        check("ปิดโดยคนอื่น", store.close(pid, "101"), "forbidden")
        check("ปิด", store.close(pid, host), "ok")
        check("ยืนยันหลังปิด", store.save_expense(draft["id"], "101", amount_satang=10000, payer_id="101",
                                                  note="", shares=split(10000, members)), "closed")
        check("draft ถูกลบตอนปิด", store.get_expense(dup["id"]), None)

        rows = store.confirmed_expenses(pid)
        exps = [Expense(r["payer_id"], r["amount_satang"],
                        tuple(s["discord_id"] for s in r["party_expense_shares"])) for r in rows]
        transfers = settle({k: v.net for k, v in balances(store.members(pid), exps).items()})
        first = store.create_transfers(pid, transfers)
        check("จำนวนรายการโอน", len(first), 2)
        check("สร้างซ้ำได้ชุดเดิม", [t["id"] for t in store.create_transfers(pid, transfers)], [t["id"] for t in first])
        check("คนนอกกดโอนแล้ว", store.toggle_paid(first[0]["id"], "105"), "forbidden")
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
