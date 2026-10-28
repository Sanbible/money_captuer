"""เว็บ Money Captuer — FastAPI เสิร์ฟหน้า HTML (web/static) และ /api/*

รันในเครื่อง: python -m uvicorn web.app:create_default_app --factory --reload
ทุก /api/* (ยกเว้น /api/config) ต้องมี `Authorization: Bearer <Supabase access token>`
ทุก query กรองด้วย user_id ของผู้เรียก — ของคนอื่นตอบ 404 เหมือนไม่มีอยู่
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import UUID

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import WEB_REQUIRED, Config, load_config
from slip_common import MONTH_RE, bkk_now, normalize_slip_date, normalize_slip_time
from store import ExpenseStore, NewExpense
from web.auth import AuthError, AuthUser, SupabaseAuth
from web.slips import SlipDraft, read_draft

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/png": "image/png", "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg"}
SUMMARY_MONTHS = 6
UNCATEGORIZED = "ไม่ระบุหมวด"
EXPENSE_FIELDS = ("id", "date", "time", "amount", "receiver", "category", "note", "bank", "trans_ref", "source",
                  "created_at")

Reader = Callable[[bytes, str, str, str], SlipDraft]


class ExpenseIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    category: str
    date: str | None = None  # "YYYY-MM-DD" (ไม่ส่ง = วันนี้ เวลาไทย)
    time: str | None = None  # "HH:MM"
    receiver: str = Field("", max_length=200)
    note: str = Field("", max_length=500)
    bank: str = Field("", max_length=100)
    trans_ref: str | None = Field(None, max_length=100)


async def current_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> AuthUser:
    """ตรวจ Bearer token กับ Supabase Auth แล้วรับรายการจากบอทที่ยังไม่มีเจ้าของ (ถ้าผูก Discord ไว้)"""
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
            log.warning("รับรายการจากบอทไม่สำเร็จ user=%s: %s", user.id, e)
    return user


# ระดับ module เพราะ `from __future__ import annotations` ทำให้ FastAPI หา type จาก globals
Me = Annotated[AuthUser, Depends(current_user)]


def create_app(cfg: Config, *, store: Any, auth: Any, reader: Reader = read_draft) -> FastAPI:
    """store / auth / reader ส่งเข้ามาเพื่อให้เทสต์ใช้ fake ได้"""
    app = FastAPI(title="Money Captuer", docs_url=None, redoc_url=None)
    app.state.store, app.state.auth = store, auth
    ocr_slot = asyncio.Semaphore(1)  # Typhoon OCR จำกัด 20 req/min — อ่านทีละใบเหมือนบอท

    # ---------- pages ----------

    for path, page in (("/", "index.html"), ("/dashboard", "dashboard.html"), ("/settings", "settings.html")):
        app.add_api_route(path, _page(page), methods=["GET"], include_in_schema=False)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    async def public_config() -> dict:
        return {"supabase_url": cfg.supabase_url, "supabase_anon_key": cfg.supabase_anon_key,
                "categories": list(cfg.categories)}

    # ---------- slips ----------

    @app.post("/api/slips/read")
    async def read_slip(me: Me, file: Annotated[UploadFile, File()]) -> dict:
        mime = IMAGE_MIME_TYPES.get((file.content_type or "").lower())
        if mime is None:
            raise HTTPException(415, "รองรับเฉพาะรูป PNG หรือ JPEG")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "ไฟล์ใหญ่เกิน 10 MB")
        async with ocr_slot:
            draft = await asyncio.to_thread(reader, data, mime, cfg.typhoon_api_key, cfg.typhoon_llm_model)
        duplicate = False
        if draft.trans_ref:
            duplicate = await asyncio.to_thread(
                store.is_duplicate, draft.trans_ref, user_id=me.id, discord_id=me.discord_id
            )
        return {**asdict(draft), "duplicate": duplicate}

    # ---------- expenses ----------

    @app.post("/api/expenses", status_code=201)
    async def create_expense(me: Me, body: ExpenseIn) -> dict:
        if body.category not in cfg.categories:
            raise HTTPException(422, "หมวดไม่ถูกต้อง")
        expense_date = bkk_now().date().isoformat()
        if body.date:
            expense_date = normalize_slip_date(body.date) or _invalid("วันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)")
        expense_time = ""
        if body.time:
            expense_time = normalize_slip_time(body.time) or _invalid("เวลาไม่ถูกต้อง (ใช้ HH:MM)")
        exp = NewExpense(
            date=expense_date, time=expense_time, amount=round(body.amount, 2), receiver=body.receiver.strip(),
            category=body.category, note=body.note.strip(), bank=body.bank.strip(),
            trans_ref=(body.trans_ref or "").strip(), source="web", user_id=me.id, discord_id=me.discord_id,
        )
        row = await asyncio.to_thread(store.insert, exp)
        if row is None:
            raise HTTPException(409, "สลิปนี้บันทึกไปแล้ว")
        return _public(row)

    @app.get("/api/expenses")
    async def list_expenses(me: Me, month: Annotated[str | None, Query()] = None) -> list[dict]:
        start = _month_start(month)
        rows = await asyncio.to_thread(
            store.list_expenses, start=start.isoformat(), end=_add_months(start, 1).isoformat(), user_id=me.id
        )
        return [_public(r) for r in rows]

    @app.delete("/api/expenses/{expense_id}", status_code=204)
    async def delete_expense(me: Me, expense_id: UUID) -> Response:
        if not await asyncio.to_thread(store.delete, str(expense_id), user_id=me.id):
            raise HTTPException(404, "ไม่พบรายการนี้")
        return Response(status_code=204)

    @app.get("/api/summary")
    async def summary(me: Me, month: Annotated[str | None, Query()] = None) -> dict:
        start = _month_start(month)
        first = _add_months(start, -(SUMMARY_MONTHS - 1))
        rows = await asyncio.to_thread(
            store.list_expenses, start=first.isoformat(), end=_add_months(start, 1).isoformat(), user_id=me.id
        )
        return summarize(rows, start, first)

    return app


def create_default_app() -> FastAPI:
    """ใช้กับ uvicorn --factory: อ่าน .env / env ของ Render แล้วต่อ Supabase จริง"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(required=WEB_REQUIRED)
    store = ExpenseStore(cfg.supabase_url, cfg.supabase_service_role_key)
    auth = SupabaseAuth(cfg.supabase_url, cfg.supabase_anon_key)
    return create_app(cfg, store=store, auth=auth)


# ---------------------------------------------------------------------- helpers


def summarize(rows: list[dict], month_start: date, first_month: date) -> dict:
    """ยอดรวม + ยอดตามหมวดของเดือน month_start + ยอดรายเดือนตั้งแต่ first_month ถึง month_start"""
    month = month_start.strftime("%Y-%m")
    monthly = {_add_months(first_month, i).strftime("%Y-%m"): 0.0 for i in range(SUMMARY_MONTHS)}
    by_category: dict[str, float] = {}
    for row in rows:
        row_month = str(row["date"])[:7]
        amount = float(row["amount"])
        if row_month in monthly:
            monthly[row_month] += amount
        if row_month == month:
            category = (row.get("category") or "").strip() or UNCATEGORIZED
            by_category[category] = by_category.get(category, 0.0) + amount
    ordered = sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "month": month,
        "total": round(monthly[month], 2),
        "by_category": [{"category": c, "total": round(t, 2)} for c, t in ordered],
        "monthly": [{"month": m, "total": round(t, 2)} for m, t in monthly.items()],
    }


def _page(filename: str) -> Callable[[], FileResponse]:
    async def page() -> FileResponse:
        return FileResponse(STATIC_DIR / filename, media_type="text/html")

    return page


def _invalid(detail: str) -> Any:
    raise HTTPException(422, detail)


def _month_start(month: str | None) -> date:
    """"YYYY-MM" → วันที่ 1 ของเดือน ; None → เดือนปัจจุบัน (เวลาไทย) ; ผิดรูปแบบ → 422"""
    if month is None:
        return bkk_now().date().replace(day=1)
    if not MONTH_RE.fullmatch(month):
        _invalid("เดือนไม่ถูกต้อง (ใช้ YYYY-MM)")
    return date(int(month[:4]), int(month[5:]), 1)


def _add_months(d: date, n: int) -> date:
    index = d.year * 12 + (d.month - 1) + n
    return date(index // 12, index % 12 + 1, 1)


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in EXPENSE_FIELDS}
