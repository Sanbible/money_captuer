"""คำนวณเงินของปาร์ตี้: หารรายการ, ยอดสุทธิ, รายการโอนที่น้อยครั้งที่สุด — pure functions ไม่ต่อ Discord / network

เงินเป็นจำนวนเต็มหน่วยสตางค์ทุกที่ (ห้าม float) แปลงเป็นบาทเฉพาะตอนแสดงผล
id ผู้ใช้เป็นสตริง (uuid ของบัญชีเว็บ) — ลำดับที่แน่นอนใช้การเรียงสตริง
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_AMOUNT_SATANG = 1_000_000_000  # 10,000,000 บาท


@dataclass(frozen=True)
class Expense:
    payer: str
    amount: int  # สตางค์
    participants: tuple[str, ...]


@dataclass(frozen=True)
class Balance:
    paid: int  # จ่ายไปทั้งหมด
    owed: int  # ส่วนที่ต้องรับผิดชอบ
    net: int  # paid - owed (บวก = ได้คืน, ลบ = ต้องโอน)


@dataclass(frozen=True)
class Transfer:
    from_id: str
    to_id: str
    amount: int


def _order(member_id: str) -> str:
    return member_id


def split(amount: int, participants: Sequence[str]) -> dict[str, int]:
    """หาร amount ให้ผู้มีส่วน: คนละ amount // n แล้วแจกเศษทีละ 1 สตางค์ตามลำดับ id — ผลรวม == amount เสมอ"""
    if amount <= 0:
        raise ValueError("ยอดต้องมากกว่า 0")
    if not participants or len(set(participants)) != len(participants):
        raise ValueError("ผู้มีส่วนต้องมีอย่างน้อย 1 คนและไม่ซ้ำกัน")
    ordered = sorted(participants, key=_order)
    base, remainder = divmod(amount, len(ordered))
    return {member: base + (1 if i < remainder else 0) for i, member in enumerate(ordered)}


def balances(members: Sequence[str], expenses: Sequence[Expense]) -> dict[str, Balance]:
    """จ่ายไป / รับผิดชอบ / สุทธิ ของทุกคน (สมาชิก + ทุก id ที่อยู่ในรายการ)"""
    paid = dict.fromkeys(members, 0)
    owed = dict.fromkeys(members, 0)
    for exp in expenses:
        paid[exp.payer] = paid.get(exp.payer, 0) + exp.amount
        owed.setdefault(exp.payer, 0)
        for member, share in split(exp.amount, exp.participants).items():
            owed[member] = owed.get(member, 0) + share
            paid.setdefault(member, 0)
    result = {m: Balance(paid=paid[m], owed=owed[m], net=paid[m] - owed[m]) for m in paid}
    assert sum(b.net for b in result.values()) == 0, "ผลรวมยอดสุทธิต้องเป็น 0"
    return result


def settle(net: Mapping[str, int]) -> list[Transfer]:
    """greedy: คนติดลบมากสุดโอนให้คนบวกมากสุด min(|ลูกหนี้|, เจ้าหนี้) วนจนทุกคนเป็น 0 — ได้ไม่เกิน n-1 รายการ"""
    if sum(net.values()) != 0:
        raise ValueError("ผลรวมยอดสุทธิต้องเป็น 0")
    left = {m: v for m, v in net.items() if v != 0}
    transfers: list[Transfer] = []
    while left:
        debtor = min((m for m in left if left[m] < 0), key=lambda m: (left[m], _order(m)))
        creditor = min((m for m in left if left[m] > 0), key=lambda m: (-left[m], _order(m)))
        amount = min(-left[debtor], left[creditor])
        transfers.append(Transfer(debtor, creditor, amount))
        for member, delta in ((debtor, amount), (creditor, -amount)):
            left[member] += delta
            if left[member] == 0:
                del left[member]
    return transfers


def format_satang(satang: int) -> str:
    """123450 -> "1,234.50" (บาท)"""
    sign = "-" if satang < 0 else ""
    baht, sat = divmod(abs(satang), 100)
    return f"{sign}{baht:,}.{sat:02d}"


_BAHT_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?")


def parse_baht(text: str | None) -> int | None:
    """ยอดที่ผู้ใช้พิมพ์ เช่น "1,234.50", "฿120", "99 บาท" -> สตางค์ ; ผิดรูปแบบ / ≤ 0 / เกิน 10 ล้านบาท -> None"""
    s = (text or "").strip().removeprefix("฿").removesuffix("บาท").strip()
    if not _BAHT_RE.fullmatch(s):
        return None
    try:
        satang = int(Decimal(s.replace(",", "")) * 100)
    except InvalidOperation:
        return None
    return satang if 0 < satang <= MAX_AMOUNT_SATANG else None
