"""ตรวจ access token ของ Supabase Auth แล้วคืนข้อมูลผู้ใช้ (blocking — web.app ห่อด้วย asyncio.to_thread)"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class AuthError(Exception):
    """token ไม่มี / หมดอายุ / ปลอม"""


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    discord_id: str | None  # None = ยังไม่ได้ผูก Discord
    providers: tuple[str, ...]  # วิธี login ที่ผูกไว้ เช่น ("discord", "google")
    name: str = ""  # ชื่อที่แสดง (เช่นในปาร์ตี้)


class SupabaseAuth:
    def __init__(self, supabase_url: str, anon_key: str, *, client: httpx.Client | None = None) -> None:
        self._user_url = supabase_url.rstrip("/") + "/auth/v1/user"
        self._anon_key = anon_key
        self._client = client or httpx.Client(timeout=10.0)

    def get_user(self, access_token: str | None) -> AuthUser:
        token = (access_token or "").strip()
        if not token:
            raise AuthError("ไม่มี token")
        resp = self._client.get(self._user_url, headers={"apikey": self._anon_key, "Authorization": f"Bearer {token}"})
        if resp.status_code in (401, 403):
            raise AuthError("token ไม่ถูกต้องหรือหมดอายุ")
        resp.raise_for_status()
        data = resp.json()
        identities = data.get("identities") or []
        return AuthUser(
            id=data["id"],
            email=data.get("email") or None,
            discord_id=discord_id_from_identities(identities),
            providers=tuple(i.get("provider", "") for i in identities),
            name=display_name(data),
        )


def display_name(data: dict[str, Any]) -> str:
    """ชื่อที่แสดง: ชื่อ Discord (global name) → ชื่อเต็ม → ชื่อผู้ใช้ → อีเมล"""
    meta = data.get("user_metadata") or {}
    claims = meta.get("custom_claims") or {}
    for value in (claims.get("global_name"), meta.get("full_name"), meta.get("name"), meta.get("user_name"),
                  data.get("email")):
        if isinstance(value, str) and value.strip():
            return value.strip()[:100]
    return "ผู้ใช้"


def discord_id_from_identities(identities: list[dict[str, Any]] | None) -> str | None:
    """Discord user id จาก identity ที่ provider == "discord" (ไม่มี → None)"""
    for identity in identities or []:
        if identity.get("provider") != "discord":
            continue
        data = identity.get("identity_data") or {}
        value = data.get("provider_id") or data.get("sub") or identity.get("id")
        if value:
            return str(value)
    return None
