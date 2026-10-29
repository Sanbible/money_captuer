"""เก็บรายจ่ายในตาราง `expenses` ของ Supabase ผ่าน PostgREST (รายจ่ายส่วนตัวของเว็บ)

ใช้ service-role key (ข้าม RLS) — ทุก method ที่อ่าน/ลบต้องระบุเจ้าของ (user_id และ/หรือ discord_id)
ทุก method เป็น blocking (web ห่อด้วย asyncio.to_thread)
schema: supabase/schema.sql
ทดสอบกับ Supabase จริง: python store.py   (ต้องมี SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY ใน .env)
"""
from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from slip_common import normalize_ref

log = logging.getLogger(__name__)

UNIQUE_VIOLATION = "23505"  # Postgres error code ของ unique index (สลิปซ้ำ)


@dataclass
class NewExpense:
    date: str  # "YYYY-MM-DD"
    time: str  # "HH:MM" หรือ ""
    amount: float
    receiver: str
    category: str
    note: str
    bank: str
    trans_ref: str  # "" ถ้าไม่มี QR
    source: str  # "web" | "discord"
    user_id: str | None = None  # บัญชีเว็บ (None = มาจากบอท ยังไม่ผูกบัญชี)
    discord_id: str | None = None
    discord_link: str = ""


class ExpenseStore:
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

    # ------------------------------------------------------------------ public API

    def insert(self, exp: NewExpense) -> dict | None:
        """บันทึก 1 แถวแล้วคืนแถวนั้น; None ถ้า trans_ref ซ้ำกับของเจ้าของเดียวกัน"""
        body = asdict(exp)
        body["trans_ref"] = normalize_ref(exp.trans_ref) or None
        body["time"] = exp.time or None
        body["discord_link"] = exp.discord_link or None
        resp = self._request("POST", "expenses", json=body, prefer="return=representation")
        if resp.status_code == 409 and _error_code(resp) == UNIQUE_VIOLATION:
            log.info("สลิปซ้ำ ref=%s", body["trans_ref"])
            return None
        resp.raise_for_status()
        return resp.json()[0]

    def is_duplicate(self, trans_ref: str | None, *, user_id: str | None = None, discord_id: str | None = None) -> bool:
        """เจ้าของคนนี้เคยบันทึกเลขอ้างอิงนี้แล้วหรือยัง ("" → False ไม่เรียก API)"""
        owner = _owner_params(user_id, discord_id)
        ref = normalize_ref(trans_ref)
        if not ref:
            return False
        resp = self._request("GET", "expenses", params=[("select", "id"), ("trans_ref", f"eq.{ref}"), *owner, ("limit", "1")])
        resp.raise_for_status()
        return bool(resp.json())

    def list_expenses(
        self, *, start: str, end: str, user_id: str | None = None, discord_id: str | None = None
    ) -> list[dict]:
        """รายการที่ start <= date < end เรียงใหม่สุดก่อน"""
        owner = _owner_params(user_id, discord_id)
        resp = self._request(
            "GET",
            "expenses",
            params=[
                ("select", "*"),
                *owner,
                ("date", f"gte.{start}"),
                ("date", f"lt.{end}"),
                ("order", "date.desc,time.desc.nullslast,created_at.desc"),
            ],
        )
        resp.raise_for_status()
        return resp.json()

    def delete(self, expense_id: str, *, user_id: str) -> bool:
        """ลบรายการของ user นี้; False ถ้าไม่มีหรือเป็นของคนอื่น"""
        if not user_id:
            raise ValueError("ต้องระบุ user_id")
        resp = self._request(
            "DELETE",
            "expenses",
            params=[("id", f"eq.{expense_id}"), ("user_id", f"eq.{user_id}")],
            prefer="return=representation",
        )
        resp.raise_for_status()
        return bool(resp.json())

    def claim_discord_expenses(self, user_id: str, discord_id: str) -> int:
        """ผูกรายการจากบอทที่ยังไม่มีเจ้าของ (discord_id นี้) เข้าบัญชีเว็บ — คืนจำนวนแถวที่ผูก"""
        resp = self._request("POST", "rpc/claim_discord_expenses", json={"p_user": user_id, "p_discord": discord_id})
        resp.raise_for_status()
        return int(resp.json() or 0)

    # ------------------------------------------------------------------ internals

    def _request(self, method: str, path: str, *, prefer: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        return self._client.request(method, f"{self._rest}/{path}", headers=headers, **kwargs)


def _owner_params(user_id: str | None, discord_id: str | None) -> list[tuple[str, str]]:
    """filter เจ้าของ — ต้องมีอย่างน้อยหนึ่งอย่าง กันเผลอดึงข้อมูลทุกคน"""
    if user_id and discord_id:
        return [("or", f'(user_id.eq.{user_id},discord_id.eq."{discord_id}")')]
    if user_id:
        return [("user_id", f"eq.{user_id}")]
    if discord_id:
        return [("discord_id", f"eq.{discord_id}")]
    raise ValueError("ต้องระบุ user_id หรือ discord_id")


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json().get("code")
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------- smoke test


def _smoke_test() -> int:
    """เพิ่มแถวทดสอบ (discord_id="smoke") ลง Supabase จริง แล้วเช็กซ้ำ + อ่านกลับ"""
    from datetime import datetime

    from config import ConfigError, load_config

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = load_config(required=["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
    except ConfigError as exc:
        print(f"ตั้งค่าไม่ครบ: {exc}")
        return 1

    store = ExpenseStore(cfg.supabase_url, cfg.supabase_service_role_key)
    ref = "SMOKETEST" + datetime.now().strftime("%Y%m%d%H%M%S")
    exp = NewExpense(
        date="2000-01-15", time="12:34", amount=1234.5, receiver="ร้านทดสอบ (python store.py)", category="อื่นๆ",
        note="แถวทดสอบ ลบได้", bank="กรุงไทย", trans_ref=ref, source="discord", discord_id="smoke",
    )
    print(f"is_duplicate ก่อนบันทึก = {store.is_duplicate(ref, discord_id='smoke')}  (ควรเป็น False)")
    print(f"insert = {store.insert(exp)}")
    print(f"insert ซ้ำ = {store.insert(exp)}  (ควรเป็น None)")
    dup = store.is_duplicate(ref, discord_id="smoke")
    print(f"is_duplicate หลังบันทึก = {dup}  (ควรเป็น True)")
    rows = store.list_expenses(start="2000-01-01", end="2000-02-01", discord_id="smoke")
    print(f"list_expenses เดือน 2000-01 ของ discord_id=smoke: {len(rows)} แถว")
    print("ลบแถวทดสอบได้ด้วย SQL: delete from expenses where discord_id = 'smoke';")
    return 0 if dup else 1


if __name__ == "__main__":
    sys.exit(_smoke_test())
