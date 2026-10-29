"""API ปาร์ตี้หารค่าใช้จ่าย `/api/parties/...`

ตรวจ login ด้วย current_user เดิม แล้วตรวจว่าเป็นสมาชิกปาร์ตี้ทุก request — ไม่ใช่สมาชิก = 404 (เหมือนไม่มีปาร์ตี้นั้น)
กฎที่ต้องถูกต้องแม้กดพร้อมกัน (≤ 10 คน, สลิปซ้ำ, ปิดแล้วห้ามแก้, สิทธิ์แก้/ลบ) อยู่ใน SQL functions (supabase/party_schema.sql)
เงินเป็นสตางค์ — ยอดรับเป็นสตริงแล้ว parse_baht (ไม่ผ่าน float), หารด้วย party_math.split
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from party_math import Expense, balances, parse_baht, settle, split
from party_store import PartyStore
from web.deps import Me

router = APIRouter(prefix="/api/parties")

CLOSED_TEXT = "ปาร์ตี้นี้ปิดแล้ว เพิ่ม/แก้รายการไม่ได้"
NOT_FOUND_TEXT = "ไม่พบปาร์ตี้นี้"
# สถานะจาก SQL function → (HTTP status, ข้อความ)
STATUS = {
    "full": (409, "ปาร์ตี้นี้เต็มแล้ว (สูงสุด 10 คน)"),
    "closed": (409, CLOSED_TEXT),
    "already_closed": (409, "ปาร์ตี้นี้ปิดไปแล้ว"),
    "involved": (409, "ออก/เตะไม่ได้ เพราะคนนี้มีรายการเกี่ยวข้องอยู่ (เป็นคนจ่าย หรือมีส่วนในบางรายการ)"),
    "host": (409, "host ออกจากปาร์ตี้หรือถูกเตะไม่ได้"),
    "duplicate": (409, "สลิปนี้ถูกบันทึกในปาร์ตี้แล้ว"),
    "locked": (409, "ปาร์ตี้นี้เคลียร์ครบแล้ว"),
    "forbidden": (403, "คุณไม่มีสิทธิ์ทำรายการนี้"),
    "invalid": (422, "ข้อมูลไม่ถูกต้อง (คนจ่าย/ผู้มีส่วนต้องเป็นสมาชิก และผลรวมต้องเท่ากับยอด)"),
    "not_member": (404, NOT_FOUND_TEXT),
    "not_found": (404, "ไม่พบรายการนี้"),
}


def fail(status: str, detail: str | None = None) -> HTTPException:
    code, text = STATUS.get(status, (500, "เกิดข้อผิดพลาด ลองใหม่อีกครั้ง"))
    return HTTPException(code, detail or text)


def get_store(request: Request) -> PartyStore:
    store = getattr(request.app.state, "party_store", None)
    if store is None:
        raise HTTPException(503, "ระบบปาร์ตี้ยังไม่พร้อมใช้งาน")
    return store


Store = Annotated[PartyStore, Depends(get_store)]


async def run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------- bodies

UserId = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class JoinBody(BaseModel):
    code: str = Field(min_length=1, max_length=20)


class ExpenseBody(BaseModel):
    amount: str = Field(min_length=1, max_length=20)  # "1,234.50" — ไม่รับ float
    note: str = Field("", max_length=100)
    payer_id: UserId
    participants: list[UserId] = Field(min_length=1, max_length=10)


class NewExpenseBody(ExpenseBody):
    source: Literal["slip", "cash"] = "cash"
    trans_ref: str | None = Field(None, max_length=100)
    image_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------- helpers

def shares_of(row: dict) -> dict[str, int]:
    return {s["user_id"]: int(s["share_satang"]) for s in row.get("party_expense_shares") or []}


def to_expenses(rows: list[dict]) -> list[Expense]:
    return [Expense(r["payer_id"], int(r["amount_satang"]), tuple(sorted(shares_of(r)))) for r in rows]


def party_detail(party: dict, members: list[dict], rows: list[dict], transfers: list[dict], me_id: str) -> dict:
    """ข้อมูลทั้งหมดที่หน้าปาร์ตี้ใช้ (ทุกสมาชิกเห็นทุกรายการ)"""
    host = party["host_id"]
    member_ids = [m["user_id"] for m in members]
    bal = balances(member_ids, to_expenses(rows))
    return {
        "party": {k: party.get(k) for k in ("id", "code", "name", "status", "host_id", "created_at")},
        "me": {"user_id": me_id, "is_host": me_id == host},
        "members": [{"user_id": m["user_id"], "name": m.get("display_name") or "สมาชิก", "is_host": m["user_id"] == host}
                    for m in members],
        "expenses": [
            {
                "id": r["id"], "amount_satang": int(r["amount_satang"]), "payer_id": r["payer_id"],
                "created_by": r["created_by"], "note": r.get("note") or "", "source": r.get("source"),
                "created_at": r.get("created_at"),
                "shares": [{"user_id": u, "share_satang": v} for u, v in sorted(shares_of(r).items())],
                "can_edit": party["status"] == "open" and me_id in (r["created_by"], host),
            }
            for r in rows
        ],
        "balances": [{"user_id": u, "paid": b.paid, "owed": b.owed, "net": b.net} for u, b in bal.items()],
        "total_satang": sum(int(r["amount_satang"]) for r in rows),
        "transfers": [
            {"id": t["id"], "seq": t["seq"], "from_id": t["from_id"], "to_id": t["to_id"],
             "amount_satang": int(t["amount_satang"]), "paid": bool(t["paid"]),
             "can_mark": party["status"] == "closed" and me_id in (t["from_id"], t["to_id"])}
            for t in transfers
        ],
    }


async def load_member_party(store: PartyStore, party_id: UUID, me_id: str) -> tuple[dict, list[dict]]:
    """ปาร์ตี้ + สมาชิก — ไม่มีปาร์ตี้หรือไม่ได้เป็นสมาชิก → 404"""
    party, members = await asyncio.gather(run(store.get_party, str(party_id)), run(store.members, str(party_id)))
    if party is None or me_id not in {m["user_id"] for m in members}:
        raise HTTPException(404, NOT_FOUND_TEXT)
    return party, members


async def detail_of(store: PartyStore, party_id: str, me_id: str) -> dict:
    party, members, rows, transfers = await asyncio.gather(
        run(store.get_party, party_id), run(store.members, party_id), run(store.expenses, party_id),
        run(store.transfers, party_id),
    )
    return party_detail(party, members, rows, transfers, me_id)


def checked_split(body: ExpenseBody, members: list[dict]) -> tuple[int, dict[str, int]]:
    """ตรวจยอด + คน แล้วหาร — ผิด → 422 ข้อความไทย"""
    amount = parse_baht(body.amount)
    if amount is None:
        raise HTTPException(422, "ยอดไม่ถูกต้อง ใส่ตัวเลขบวก ทศนิยมไม่เกิน 2 ตำแหน่ง เช่น 1,234.50")
    ids = {m["user_id"] for m in members}
    if len(set(body.participants)) != len(body.participants):
        raise HTTPException(422, "เลือกผู้มีส่วนซ้ำกัน")
    if body.payer_id not in ids or not set(body.participants) <= ids:
        raise HTTPException(422, "คนจ่ายและผู้มีส่วนต้องเป็นสมาชิกปาร์ตี้")
    return amount, split(amount, body.participants)


# ---------------------------------------------------------------------- routes

@router.get("")
async def list_parties(me: Me, store: Store) -> list[dict]:
    parties = await run(store.parties_of, me.id)

    async def summary(p: dict) -> dict:
        members, rows = await asyncio.gather(run(store.members, p["id"]), run(store.expenses, p["id"]))
        bal = balances([m["user_id"] for m in members], to_expenses(rows))
        mine = bal.get(me.id)
        return {"id": p["id"], "code": p["code"], "name": p["name"], "status": p["status"],
                "is_host": p["host_id"] == me.id, "member_count": len(members),
                "total_satang": sum(int(r["amount_satang"]) for r in rows),
                "my_net_satang": mine.net if mine else 0, "created_at": p.get("created_at")}

    items = list(await asyncio.gather(*(summary(p) for p in parties)))
    order = {"open": 0, "closed": 1, "settled": 2}
    items.sort(key=lambda i: i["created_at"] or "", reverse=True)  # ใหม่สุดก่อน
    items.sort(key=lambda i: order.get(i["status"], 3))  # stable: ปาร์ตี้ที่ยังเปิดอยู่ขึ้นก่อน
    return items


@router.post("", status_code=201)
async def create_party(me: Me, store: Store, body: CreateBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "ตั้งชื่อปาร์ตี้ก่อน")
    party = await run(store.create_party, name=name, host_id=me.id, host_name=me.name)
    return {k: party.get(k) for k in ("id", "code", "name", "status")}


@router.post("/join")
async def join_party(me: Me, store: Store, body: JoinBody) -> dict:
    party = await run(store.party_by_code, body.code)
    if party is None:
        raise HTTPException(404, "ไม่พบปาร์ตี้ที่ใช้โค้ดนี้")
    status = await run(store.join, party["id"], me.id, me.name)
    if status not in ("ok", "already"):
        raise fail(status)
    return {"id": party["id"], "joined": status == "ok"}


@router.get("/{party_id}")
async def get_party(me: Me, store: Store, party_id: UUID) -> dict:
    await load_member_party(store, party_id, me.id)
    return await detail_of(store, str(party_id), me.id)


@router.post("/{party_id}/expenses", status_code=201)
async def add_expense(me: Me, store: Store, party_id: UUID, body: NewExpenseBody) -> dict:
    party, members = await load_member_party(store, party_id, me.id)
    if party["status"] != "open":
        raise HTTPException(409, CLOSED_TEXT)
    amount, shares = checked_split(body, members)
    status, expense_id = await run(
        store.add_expense, str(party_id), me.id, amount_satang=amount, payer_id=body.payer_id,
        note=body.note.strip(), source=body.source, trans_ref=(body.trans_ref or "").strip() or None,
        image_sha256=body.image_sha256, shares=shares,
    )
    if status != "ok":
        raise fail(status)
    return {"id": expense_id}


@router.put("/{party_id}/expenses/{expense_id}")
async def update_expense(me: Me, store: Store, party_id: UUID, expense_id: UUID, body: ExpenseBody) -> dict:
    party, members = await load_member_party(store, party_id, me.id)
    if party["status"] != "open":
        raise HTTPException(409, CLOSED_TEXT)
    amount, shares = checked_split(body, members)
    status = await run(store.update_expense, str(expense_id), me.id, amount_satang=amount, payer_id=body.payer_id,
                       note=body.note.strip(), shares=shares)
    if status != "ok":
        raise fail(status)
    return {"ok": True}


@router.delete("/{party_id}/expenses/{expense_id}", status_code=204)
async def delete_expense(me: Me, store: Store, party_id: UUID, expense_id: UUID) -> Response:
    await load_member_party(store, party_id, me.id)
    status = await run(store.delete_expense, str(expense_id), me.id)
    if status != "ok":
        raise fail(status)
    return Response(status_code=204)


@router.delete("/{party_id}/members/{user_id}", status_code=204)
async def remove_member(me: Me, store: Store, party_id: UUID, user_id: UUID) -> Response:
    await load_member_party(store, party_id, me.id)
    status = await run(store.remove_member, str(party_id), str(user_id), me.id)
    if status != "ok":
        raise fail(status, "เฉพาะ host เท่านั้นที่เตะสมาชิกได้" if status == "forbidden" else None)
    return Response(status_code=204)


@router.post("/{party_id}/end")
async def end_party(me: Me, store: Store, party_id: UUID) -> dict:
    """host ล็อกปาร์ตี้ → คำนวณรายการโอน → บันทึกครั้งเดียว (เรียกซ้ำได้ ได้ชุดเดิม)"""
    await load_member_party(store, party_id, me.id)
    status = await run(store.close, str(party_id), me.id)
    if status == "forbidden":
        raise HTTPException(403, "เฉพาะ host เท่านั้นที่ปิดปาร์ตี้ได้")
    if status not in ("ok", "already_closed"):
        raise fail(status)
    members, rows = await asyncio.gather(run(store.members, str(party_id)), run(store.expenses, str(party_id)))
    net = {u: b.net for u, b in balances([m["user_id"] for m in members], to_expenses(rows)).items()}
    await run(store.create_transfers, str(party_id), settle(net))
    return await detail_of(store, str(party_id), me.id)


@router.post("/{party_id}/transfers/{transfer_id}/paid")
async def toggle_paid(me: Me, store: Store, party_id: UUID, transfer_id: UUID) -> dict:
    await load_member_party(store, party_id, me.id)
    if str(transfer_id) not in {t["id"] for t in await run(store.transfers, str(party_id))}:
        raise HTTPException(404, "ไม่พบรายการโอนนี้")
    status = await run(store.toggle_paid, str(transfer_id), me.id)
    if status not in ("paid", "unpaid", "settled"):
        raise fail(status, "เฉพาะคนโอนหรือคนรับของรายการนี้เท่านั้นที่กดได้" if status == "forbidden" else None)
    return await detail_of(store, str(party_id), me.id)
