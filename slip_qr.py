"""อ่าน QR บนสลิปโอนเงินธนาคารไทย + parse EMVCo TLV.

QR สลิป (slip-verify / mini QR) มีแค่ API ID, รหัสธนาคารผู้โอน และเลขอ้างอิงธุรกรรม (transRef)
— ไม่มียอดเงิน/วันที่/ชื่อ ใช้สำหรับกันบันทึกซ้ำและแสดงชื่อธนาคารเท่านั้น

    tag 00 = ก้อนข้อมูลหลัก -> sub-tag 00 API ID, 01 รหัสธนาคาร (3 หลัก), 02 transRef
    tag 51 = ประเทศ ("TH"), tag 91 = CRC

ทุกฟังก์ชันเป็น blocking (web ห่อด้วย asyncio.to_thread)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# รหัสธนาคารตามสเปก (ตรวจสอบกับแหล่งอ้างอิงก่อนเพิ่มรหัสอื่น)
BANK_NAMES: dict[str, str] = {
    "002": "กรุงเทพ",
    "004": "กสิกรไทย",
    "006": "กรุงไทย",
    "011": "ทีทีบี",
    "014": "ไทยพาณิชย์",
    "025": "กรุงศรี",
    "030": "ออมสิน",
}

_ASCII_DIGITS = frozenset("0123456789")

# upscale ×2 เฉพาะรูปที่ไม่ใหญ่เกินไป (≈ screenshot 1080x2400 ยังทำ, 1440x3200 ข้าม)
_MAX_UPSCALE_PIXELS = 3_000_000


# --- EMVCo TLV ---------------------------------------------------------------


def _is_two_digits(s: str) -> bool:
    # ใช้ชุดตัวเลข ASCII เท่านั้น (str.isdigit / int() ยอมรับ " 2", "-1", เลขอารบิก ฯลฯ)
    return len(s) == 2 and all(c in _ASCII_DIGITS for c in s)


def parse_tlv(payload: str) -> dict[str, str]:
    """Parse EMVCo TLV: ``[tag 2 หลัก][length 2 หลัก][value]...`` -> ``{tag: value}``.

    Raises ValueError on malformed input (non-digit tag/length, truncated value,
    incomplete header, duplicate tag).
    """
    if not isinstance(payload, str):
        raise TypeError(f"payload must be str, got {type(payload).__name__}")

    result: dict[str, str] = {}
    pos, end = 0, len(payload)
    while pos < end:
        if end - pos < 4:
            raise ValueError(f"TLV header ไม่ครบที่ตำแหน่ง {pos}")
        tag, length_str = payload[pos:pos + 2], payload[pos + 2:pos + 4]
        if not _is_two_digits(tag):
            raise ValueError(f"TLV tag ไม่ใช่ตัวเลข 2 หลักที่ตำแหน่ง {pos}")
        if not _is_two_digits(length_str):
            raise ValueError(f"TLV length ไม่ใช่ตัวเลข 2 หลักที่ตำแหน่ง {pos + 2}")
        value_start = pos + 4
        value_end = value_start + int(length_str)
        if value_end > end:
            raise ValueError(f"TLV value ของ tag {tag} ขาด (ต้องการ {length_str} ตัว)")
        if tag in result:
            raise ValueError(f"TLV tag {tag} ซ้ำ")
        result[tag] = payload[value_start:value_end]
        pos = value_end
    return result


def parse_slip_qr(payload: str) -> dict[str, str] | None:
    """QR สลิป -> ``{"api_id", "bank_code", "trans_ref", "country"}``.

    Returns None if the payload isn't a Thai slip-verify QR (malformed TLV, no tag 00,
    tag 00 not a nested TLV, or empty/missing sub-tag 02 transRef). Missing optional
    fields (api_id, bank_code, country) are returned as "".
    """
    if not isinstance(payload, str):
        return None
    try:
        top = parse_tlv(payload.strip())
        main = top.get("00")
        if main is None:
            return None
        sub = parse_tlv(main)
    except ValueError:
        return None

    trans_ref = sub.get("02", "")
    if not trans_ref:
        return None
    return {
        "api_id": sub.get("00", ""),
        "bank_code": sub.get("01", ""),
        "trans_ref": trans_ref,
        "country": top.get("51", ""),
    }


def bank_name(code: str | None) -> str:
    """รหัสธนาคาร -> ชื่อไทย; ไม่รู้จัก -> คืนรหัสดิบ; None/"" -> ""."""
    if not code:
        return ""
    return BANK_NAMES.get(code, code)


# --- QR image reading ---------------------------------------------------------


def _decode_image(image_bytes: bytes) -> np.ndarray | None:
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    if buf.size == 0:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return None
    return img


def _variants(img: np.ndarray) -> Iterator[tuple[str, np.ndarray]]:
    """รูปต้นฉบับ + preprocessing fallback (สร้างแบบ lazy, ตัวที่แพงสุดไว้ท้าย)."""
    yield "original", img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    yield "gray", gray
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    yield "otsu", otsu
    height, width = gray.shape[:2]
    if height * width <= _MAX_UPSCALE_PIXELS:
        # QR บน screenshot มักเล็ก -> ขยาย ×2 ช่วยให้ detector หา finder pattern เจอ
        yield "upscale2x", cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)


def _make_detectors() -> list[Any]:
    """สร้าง detector ใหม่ทุกครั้ง (ปลอดภัยเมื่อเรียกพร้อมกันหลาย thread)."""
    detectors: list[Any] = []
    factories = [("QRCodeDetector", getattr(cv2, "QRCodeDetector", None)),
                 ("QRCodeDetectorAruco", getattr(cv2, "QRCodeDetectorAruco", None))]
    for name, factory in factories:
        if factory is None:
            continue
        try:
            detectors.append(factory())
        except Exception as exc:  # noqa: BLE001
            logger.debug("สร้าง %s ไม่ได้: %s", name, exc)
    return detectors


def _decode_with(detector: Any, img: np.ndarray) -> list[str]:
    """detectAndDecodeMulti ก่อน ถ้าไม่เจอค่อยลอง detectAndDecode (single)."""
    try:
        ok, decoded, _points, _straight = detector.detectAndDecodeMulti(img)
        found = [s for s in decoded if s and s.strip()] if ok else []
        if found:
            return found
    except Exception as exc:  # noqa: BLE001
        logger.debug("detectAndDecodeMulti ล้มเหลว: %s", exc)
    try:
        text, _points, _straight = detector.detectAndDecode(img)
        if text and text.strip():
            return [text]
    except Exception as exc:  # noqa: BLE001
        logger.debug("detectAndDecode ล้มเหลว: %s", exc)
    return []


def _iter_payloads(image_bytes: bytes) -> Iterator[str]:
    """Yield unique QR payloads, variant by variant. Never raises."""
    seen: set[str] = set()
    try:
        img = _decode_image(image_bytes)
        if img is None:
            logger.debug("อ่านรูปไม่ได้ (ไม่ใช่ PNG/JPEG หรือไฟล์เสีย)")
            return
        detectors = _make_detectors()
        if not detectors:
            return
        for variant_name, variant in _variants(img):
            for detector in detectors:
                found = _decode_with(detector, variant)
                if not found:
                    continue
                for payload in found:
                    if payload not in seen:
                        seen.add(payload)
                        logger.debug("พบ QR จาก variant %s", variant_name)
                        yield payload
                break  # detector ตัวแรกเจอแล้ว ไม่ต้องลองตัวถัดไปใน variant นี้
    except Exception as exc:  # noqa: BLE001
        logger.debug("อ่าน QR ล้มเหลว: %s", exc)


def read_qr_payloads(image_bytes: bytes) -> list[str]:
    """อ่าน QR ทั้งหมดในรูป PNG/JPEG (ลองทุก preprocessing variant, ตัดซ้ำ). Never raises; [] if none."""
    try:
        payloads = list(_iter_payloads(image_bytes))
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_qr_payloads ล้มเหลว: %s", exc)
        return []
    logger.debug("อ่าน QR ได้ %d รายการ", len(payloads))
    return payloads


def read_slip_qr(image_bytes: bytes) -> dict[str, str] | None:
    """QR สลิปตัวแรกในรูป (ตามลำดับเดียวกับ read_qr_payloads) -> dict, ไม่เจอ -> None. Never raises.

    หยุดทันทีที่เจอ QR สลิป จึงไม่ต้องรัน fallback ที่แพงถ้า variant แรกอ่านได้แล้ว
    """
    try:
        for payload in _iter_payloads(image_bytes):
            parsed = parse_slip_qr(payload)
            if parsed is not None:
                logger.debug("พบ QR สลิป ธนาคาร=%s ref=%s", parsed["bank_code"], parsed["trans_ref"])
                return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_slip_qr ล้มเหลว: %s", exc)
    return None
