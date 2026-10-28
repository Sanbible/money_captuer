"""อ่านสลิปที่อัปโหลดผ่านเว็บ — ใช้ slip_qr / slip_reader ตัวเดียวกับบอท (blocking)"""
from __future__ import annotations

import math
from dataclasses import dataclass

import slip_qr
import slip_reader
from slip_common import normalize_slip_date, normalize_slip_time


@dataclass
class SlipDraft:
    """ค่าที่อ่านได้จากสลิป ให้ผู้ใช้ตรวจ/แก้ก่อนบันทึก (ยังไม่บันทึก)"""

    amount: float | None
    date: str | None  # "YYYY-MM-DD"
    time: str | None  # "HH:MM"
    receiver: str | None
    bank: str  # ชื่อธนาคารจาก QR ("" = ไม่ทราบ)
    trans_ref: str | None
    error: str | None  # เหตุผลเมื่อ OCR ล้ม (None = สำเร็จ)


def read_draft(data: bytes, mime: str, api_key: str, model: str) -> SlipDraft:
    """QR (เลขอ้างอิง/ธนาคาร) → Typhoon OCR/LLM (ยอด/วันที่/ผู้รับ) ; ไม่ raise"""
    qr = slip_qr.read_slip_qr(data) or {}
    slip = slip_reader.read_slip(data, api_key, model, mime)
    amount = slip.amount
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        amount = None
    return SlipDraft(
        amount=None if amount is None else round(float(amount), 2),
        date=normalize_slip_date(slip.date),
        time=normalize_slip_time(slip.time),
        receiver=(slip.receiver or "").strip() or None,
        bank=slip_qr.bank_name(qr.get("bank_code")),
        trans_ref=qr.get("trans_ref") or None,
        error=slip.error,
    )
