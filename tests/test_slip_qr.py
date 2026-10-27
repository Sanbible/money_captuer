"""Unit tests for slip_qr.py — EMVCo TLV parsing + QR reading from slip images."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

import slip_qr
from slip_qr import (
    BANK_NAMES,
    bank_name,
    parse_slip_qr,
    parse_tlv,
    read_qr_payloads,
    read_slip_qr,
)

# --- test data ---------------------------------------------------------------

# test case บังคับจากสเปก (SKILL.md)
SLIP_PAYLOAD = "0038000600000101030060217A306e5fe310f649b65102TH9104D6DA"
SLIP_EXPECTED = {
    "api_id": "000001",
    "bank_code": "006",
    "trans_ref": "A306e5fe310f649b6",
    "country": "TH",
}

# QR พร้อมเพย์สำหรับ "จ่ายเงิน" (ไม่ใช่ QR สลิป) — TLV ถูกต้อง แต่ tag 00 = "01"
PROMPTPAY_PAYLOAD = (
    "00020101021129370016A000000677010111011300668123456785802TH530376463045D82"
)


# --- image helpers (สร้างรูป QR ในเทสต์เอง ไม่ใช้ไฟล์ภายนอก) --------------------


def make_qr(payload: str, module_px: int = 8, quiet_modules: int = 4) -> np.ndarray:
    """Grayscale QR image: each module = module_px pixels, with a white quiet zone."""
    qr = cv2.QRCodeEncoder.create().encode(payload)
    qr = cv2.resize(qr, None, fx=module_px, fy=module_px, interpolation=cv2.INTER_NEAREST)
    border = quiet_modules * module_px
    return cv2.copyMakeBorder(
        qr, border, border, border, border, cv2.BORDER_CONSTANT, value=255
    )


def to_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def to_jpeg(img: np.ndarray, quality: int = 75) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


def make_slip_canvas(
    payloads: list[str] | None,
    width: int = 1080,
    height: int = 2340,
    module_px: int = 3,
) -> np.ndarray:
    """Tall white 'phone screenshot' with text lines and small QR(s) near the bottom."""
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    # แถบหัวสี + ข้อความ คล้ายสลิปธนาคาร
    cv2.rectangle(canvas, (0, 0), (width, height // 10), (120, 60, 20), thickness=-1)
    cv2.putText(canvas, "Transfer successful", (40, height // 20),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3)
    lines = [
        "26 Oct 69  18:33",
        "From  MR SOMCHAI J.",
        "xxx-x-x1234-x",
        "To  SHOP CO., LTD.",
        "Amount  1,250.00 THB",
        "Fee  0.00 THB",
        "Ref  A306e5fe310f649b6",
    ]
    y = height // 10 + 120
    for line in lines:
        cv2.putText(canvas, line, (60, y), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (30, 30, 30), 2)
        y += 110
    if payloads:
        x = width - 60
        for payload in payloads:
            qr = cv2.cvtColor(make_qr(payload, module_px=module_px), cv2.COLOR_GRAY2BGR)
            h, w = qr.shape[:2]
            top = height - h - 220
            canvas[top:top + h, x - w:x] = qr
            x -= w + 80
    return canvas


# --- parse_tlv -----------------------------------------------------------------


def test_parse_tlv_top_level_of_spec_payload():
    assert parse_tlv(SLIP_PAYLOAD) == {
        "00": "000600000101030060217A306e5fe310f649b6",
        "51": "TH",
        "91": "D6DA",
    }


def test_parse_tlv_nested_value():
    assert parse_tlv("000600000101030060217A306e5fe310f649b6") == {
        "00": "000001",
        "01": "006",
        "02": "A306e5fe310f649b6",
    }


def test_parse_tlv_empty_string_is_empty_dict():
    assert parse_tlv("") == {}


def test_parse_tlv_zero_length_value():
    assert parse_tlv("00000102ab") == {"00": "", "01": "ab"}


def test_parse_tlv_promptpay_payload():
    tlv = parse_tlv(PROMPTPAY_PAYLOAD)
    assert tlv["00"] == "01"
    assert tlv["58"] == "TH"
    assert tlv["63"] == "5D82"


@pytest.mark.parametrize(
    "bad",
    [
        "0005abc",                 # length 5 แต่มีแค่ 3 ตัว (truncated value)
        SLIP_PAYLOAD[:-1],         # ตัดท้ายไป 1 ตัว
        "0X02ab",                  # tag ไม่ใช่ตัวเลข
        "00a2ab",                  # length ไม่ใช่ตัวเลข
        "00-1x",                   # length ติดลบ / ไม่ใช่ตัวเลข
        "  02ab",                  # tag เป็นช่องว่าง
        "٠٠0١a",    # Arabic-Indic digits ไม่นับเป็นตัวเลข
        "000",                     # header ไม่ครบ 4 ตัว
        "0002ab5",                 # มีเศษท้าย
        "0002ab0002cd",            # tag ซ้ำ
    ],
)
def test_parse_tlv_malformed_raises_value_error(bad: str):
    with pytest.raises(ValueError):
        parse_tlv(bad)


# --- parse_slip_qr ---------------------------------------------------------------


def test_parse_slip_qr_spec_case():
    assert parse_slip_qr(SLIP_PAYLOAD) == SLIP_EXPECTED


def test_parse_slip_qr_ignores_surrounding_whitespace():
    assert parse_slip_qr(f"  {SLIP_PAYLOAD}\r\n") == SLIP_EXPECTED


def test_parse_slip_qr_without_country_tag():
    payload = "0038000600000101030060217A306e5fe310f649b6" + "9104D6DA"
    assert parse_slip_qr(payload) == {
        "api_id": "000001",
        "bank_code": "006",
        "trans_ref": "A306e5fe310f649b6",
        "country": "",
    }


def test_parse_slip_qr_other_bank():
    # กสิกรไทย 004, transRef อีกแบบ
    ref = "016299183512BTF05961"
    inner = "0006000001" + "0103004" + f"02{len(ref):02d}{ref}"
    payload = f"00{len(inner):02d}{inner}5102TH91041234"
    result = parse_slip_qr(payload)
    assert result == {"api_id": "000001", "bank_code": "004", "trans_ref": ref, "country": "TH"}


@pytest.mark.parametrize(
    "payload",
    [
        PROMPTPAY_PAYLOAD,                         # QR จ่ายเงินพร้อมเพย์
        "000201010211",                            # หัว QR พร้อมเพย์
        "",
        "   ",
        "hello world",
        "https://example.com/slip?id=123",
        SLIP_PAYLOAD[:-3],                         # TLV เสีย
        "5102TH9104D6DA",                          # ไม่มี tag 00
        "0017000600000101030065102TH",             # tag 00 ไม่มี sub-tag 02
        "0021000600000101030060200" + "5102TH",    # sub-tag 02 ว่าง
        "0004abcd5102TH",                          # tag 00 ข้างในไม่ใช่ TLV
    ],
)
def test_parse_slip_qr_returns_none_for_non_slip(payload: str):
    assert parse_slip_qr(payload) is None


@pytest.mark.parametrize("payload", [None, b"0038000600000101030060217A306e5fe310f649b6", 123])
def test_parse_slip_qr_non_str_returns_none(payload):
    assert parse_slip_qr(payload) is None  # type: ignore[arg-type]


# --- bank_name --------------------------------------------------------------------


def test_bank_names_are_exactly_the_spec_list():
    assert BANK_NAMES == {
        "002": "กรุงเทพ",
        "004": "กสิกรไทย",
        "006": "กรุงไทย",
        "011": "ทีทีบี",
        "014": "ไทยพาณิชย์",
        "025": "กรุงศรี",
        "030": "ออมสิน",
    }


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("002", "กรุงเทพ"),
        ("004", "กสิกรไทย"),
        ("006", "กรุงไทย"),
        ("011", "ทีทีบี"),
        ("014", "ไทยพาณิชย์"),
        ("025", "กรุงศรี"),
        ("030", "ออมสิน"),
    ],
)
def test_bank_name_known(code: str, name: str):
    assert bank_name(code) == name


def test_bank_name_unknown_returns_raw_code():
    assert bank_name("999") == "999"
    assert bank_name("069") == "069"


def test_bank_name_none_or_empty():
    assert bank_name(None) == ""
    assert bank_name("") == ""


def test_bank_name_from_parsed_spec_payload():
    assert bank_name(parse_slip_qr(SLIP_PAYLOAD)["bank_code"]) == "กรุงไทย"


# --- read_qr_payloads / read_slip_qr: images ---------------------------------------


def test_read_clean_png():
    data = to_png(make_qr(SLIP_PAYLOAD))
    assert read_qr_payloads(data) == [SLIP_PAYLOAD]  # dedupe: หลาย variant แต่ได้ผลเดียว
    assert read_slip_qr(data) == SLIP_EXPECTED


def test_read_clean_jpeg():
    data = to_jpeg(cv2.cvtColor(make_qr(SLIP_PAYLOAD), cv2.COLOR_GRAY2BGR))
    assert data[:2] == b"\xff\xd8"
    assert read_qr_payloads(data) == [SLIP_PAYLOAD]
    assert read_slip_qr(data) == SLIP_EXPECTED


@pytest.mark.parametrize(
    ("width", "height", "module_px"),
    [
        (1080, 2340, 3),   # screenshot มือถือ QR เล็ก (~111px)
        (720, 1560, 2),    # รูปที่ถูกย่อ QR เล็กมาก (~74px) — ต้องพึ่ง upscale
        (1440, 3200, 4),   # จอความละเอียดสูง (ไม่ upscale เพราะรูปใหญ่)
    ],
)
@pytest.mark.parametrize("encode", [to_png, to_jpeg], ids=["png", "jpeg"])
def test_read_small_qr_in_slip_like_screenshot(width, height, module_px, encode):
    data = encode(make_slip_canvas([SLIP_PAYLOAD], width, height, module_px))
    assert read_qr_payloads(data) == [SLIP_PAYLOAD]
    assert read_slip_qr(data) == SLIP_EXPECTED


def test_read_image_without_qr():
    data = to_png(make_slip_canvas(None))
    assert read_qr_payloads(data) == []
    assert read_slip_qr(data) is None


def test_read_blank_jpeg_without_qr():
    data = to_jpeg(np.full((400, 300, 3), 255, dtype=np.uint8))
    assert read_qr_payloads(data) == []
    assert read_slip_qr(data) is None


def test_read_promptpay_qr_is_not_a_slip():
    data = to_png(make_qr(PROMPTPAY_PAYLOAD))
    assert read_qr_payloads(data) == [PROMPTPAY_PAYLOAD]
    assert read_slip_qr(data) is None


def test_read_slip_qr_picks_slip_among_multiple_qrs():
    data = to_png(make_slip_canvas([PROMPTPAY_PAYLOAD, SLIP_PAYLOAD], module_px=5))
    payloads = read_qr_payloads(data)
    assert set(payloads) == {PROMPTPAY_PAYLOAD, SLIP_PAYLOAD}
    assert len(payloads) == 2
    assert read_slip_qr(data) == SLIP_EXPECTED


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        bytes(range(256)) * 4,
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 50,       # PNG header แต่ข้างในพัง
        b"\xff\xd8\xff\xe0" + b"garbage" * 20,     # JPEG header แต่ข้างในพัง
        None,
        "0038000600000101030060217A306e5fe310f649b6",
    ],
    ids=["empty", "text", "random", "broken-png", "broken-jpeg", "none", "str"],
)
def test_read_garbage_never_raises(data):
    assert read_qr_payloads(data) == []  # type: ignore[arg-type]
    assert read_slip_qr(data) is None  # type: ignore[arg-type]


def test_read_truncated_png_never_raises():
    data = to_png(make_qr(SLIP_PAYLOAD))
    truncated = data[: len(data) // 2]
    assert isinstance(read_qr_payloads(truncated), list)
    assert read_slip_qr(truncated) in (None, SLIP_EXPECTED)


def test_read_accepts_bytearray():
    data = bytearray(to_png(make_qr(SLIP_PAYLOAD)))
    assert read_qr_payloads(data) == [SLIP_PAYLOAD]  # type: ignore[arg-type]


def test_read_qr_payloads_swallows_detector_errors(monkeypatch):
    class BrokenDetector:
        def __init__(self, *args, **kwargs):
            raise cv2.error("boom")

    monkeypatch.setattr(slip_qr.cv2, "QRCodeDetector", BrokenDetector)
    if hasattr(slip_qr.cv2, "QRCodeDetectorAruco"):
        monkeypatch.setattr(slip_qr.cv2, "QRCodeDetectorAruco", BrokenDetector)
    data = to_png(make_qr(SLIP_PAYLOAD))
    assert read_qr_payloads(data) == []
    assert read_slip_qr(data) is None


def test_read_slip_qr_swallows_errors(monkeypatch):
    def boom(_payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(slip_qr, "parse_slip_qr", boom)
    assert read_slip_qr(to_png(make_qr(SLIP_PAYLOAD))) is None


def test_read_slip_qr_matches_first_slip_payload_of_read_qr_payloads():
    data = to_png(make_slip_canvas([PROMPTPAY_PAYLOAD, SLIP_PAYLOAD], module_px=5))
    first_slip = next(
        p for p in read_qr_payloads(data) if parse_slip_qr(p) is not None
    )
    assert read_slip_qr(data) == parse_slip_qr(first_slip)
