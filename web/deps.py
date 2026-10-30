"""dependency ที่ทุก route ใช้ร่วมกัน (web.app และ web.parties): login, รับรูปสลิป, อ่านสลิป"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, UploadFile

from web.auth import AuthError, AuthUser
from web.slips import SlipDraft

log = logging.getLogger("web.app")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/png": "image/png", "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg"}


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


async def read_image(file: UploadFile) -> tuple[bytes, str]:
    """รูปสลิปที่อัปโหลด → (bytes, mime) ; ไม่ใช่ PNG/JPEG → 415 ; ใหญ่เกิน → 413"""
    mime = IMAGE_MIME_TYPES.get((file.content_type or "").lower())
    if mime is None:
        raise HTTPException(415, "รองรับเฉพาะรูป PNG หรือ JPEG")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "ไฟล์ใหญ่เกิน 10 MB")
    return data, mime


async def read_slip(request: Request, data: bytes, mime: str, *, expect: float | None = None) -> SlipDraft:
    """อ่านสลิปด้วย reader ของแอป — จำนวนที่อ่านพร้อมกันจำกัดด้วย app.state.ocr_slot (Typhoon OCR จำกัด 20 req/min)

    expect = ยอดที่คาดไว้ ส่งให้ reader เฉพาะเมื่อมีค่า
    """
    state = request.app.state
    extra = {"expect": expect} if expect is not None else {}
    async with state.ocr_slot:
        return await asyncio.to_thread(state.reader, data, mime, state.cfg.typhoon_api_key,
                                       state.cfg.typhoon_llm_model, **extra)
