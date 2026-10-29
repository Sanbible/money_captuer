"""dependency ที่ทุก route ใช้ร่วมกัน (web.app และ web.parties)"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from web.auth import AuthError, AuthUser

log = logging.getLogger("web.app")


async def current_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> AuthUser:
    """ตรวจ Bearer token กับ Supabase Auth แล้วรับรายการที่บันทึกด้วย Discord ID ก่อนผูกบัญชี (ถ้ามี)"""
    auth, store = request.app.state.auth, request.app.state.store
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "กรุณาเข้าสู่ระบบ")
    try:
        user = await asyncio.to_thread(auth.get_user, token.strip())
    except AuthError:
        raise HTTPException(401, "กรุณาเข้าสู่ระบบ") from None
    if user.discord_id:
        try:  # ล้มก็ใช้งานต่อได้ — ลองใหม่ใน request ถัดไป
            await asyncio.to_thread(store.claim_discord_expenses, user.id, user.discord_id)
        except Exception as e:  # noqa: BLE001
            log.warning("รับรายการที่บันทึกด้วย Discord ID ไม่สำเร็จ user=%s: %s", user.id, e)
    return user


# ระดับ module เพราะ `from __future__ import annotations` ทำให้ FastAPI หา type จาก globals
Me = Annotated[AuthUser, Depends(current_user)]
