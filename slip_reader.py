"""อ่านสลิปโอนเงิน: Typhoon OCR → Typhoon LLM (ดึง JSON) → regex สำรอง

ทุกฟังก์ชันเป็น blocking — ผู้เรียกต้องห่อด้วย asyncio.to_thread
ทดสอบด้วยมือ: python slip_reader.py path/to/slip.jpg   (ต้องมี TYPHOON_API_KEY ใน .env)
"""
from __future__ import annotations

import base64
import html
import io
import json
import logging
import math
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable, Iterator

import openai
from openai import OpenAI
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

TYPHOON_BASE_URL = "https://api.opentyphoon.ai/v1"
OCR_MODEL = "typhoon-ocr"        # Typhoon OCR 1.5 — rate limit 2 req/s, 20 req/min
REQUEST_TIMEOUT = 60.0           # วินาทีต่อ request
MAX_429_RETRIES = 3              # HTTP 429 → รอ 1s, 2s, 4s แล้วลองใหม่ ครบแล้ว raise
BACKOFF_BASE_SECONDS = 1.0
OCR_HEDGE_STAGGER = 0.5          # วินาทีก่อนยิง OCR request สำรอง (ocr_first) — ไม่เกิน 2 req/s

_sleep = time.sleep  # แยกไว้ให้ unit test แทนที่ได้


@dataclass
class SlipData:
    amount: float | None = None      # ยอดที่โอนจริง ไม่ใช่ค่าธรรมเนียม
    date: str | None = None          # "YYYY-MM-DD" ปี ค.ศ.
    time: str | None = None          # "HH:MM"
    receiver: str | None = None
    error: str | None = None         # เหตุผลสั้น ๆ เมื่อ OCR/LLM ล้ม (None = สำเร็จ)


# =============================================================== Typhoon OCR
# ที่มา (ห้ามแก้เอง): PyPI package `typhoon-ocr` 0.4.1, ไฟล์ typhoon_ocr/ocr_utils.py
#   - prompt  = PROMPTS_SYS["v1.5"](figure_language="Thai") คัดลอกตรงตัว รวมบรรทัดว่างคู่และ "\n    " ท้ายข้อความ
#   - รูป      = prepare_ocr_messages(): resize_if_needed(img, max_size=1800) → convert("RGB") → JPEG
#                → message เดียว role "user" = [text prompt, image_url data URL]
#   - request = ocr_document(): model="typhoon-ocr", max_tokens=16384,
#               extra_body={"repetition_penalty": 1.1, "temperature": 0.1, "top_p": 0.6}
# ตรวจเทียบกับ model card https://huggingface.co/scb10x/typhoon-ocr1.5-2b แล้ว (ข้อความเดียวกัน
# ต่างแค่จำนวนบรรทัดว่าง; model card บอกว่าโมเดลเทรนด้วยรูปด้านยาว 1800 px)
# ไม่เรียก package ตรง ๆ เพราะรับแค่ path ไฟล์ (ต้องเขียน temp file) และตั้ง timeout/retry เองไม่ได้
OCR_PROMPT_V15 = """Extract all text from the image.


Instructions:
- Only return the clean Markdown.
- Do not include any explanation or extra text.
- You must include all information on the page.


Formatting Rules:
- Tables: Render tables using <table>...</table> in clean HTML format.
- Equations: Render equations using LaTeX syntax with inline ($...$) and block ($$...$$).
- Images/Charts/Diagrams: Wrap any clearly defined visual areas (e.g. charts, diagrams, pictures) in:


<figure>
Describe the image's main elements (people, objects, text), note any contextual clues (place, event, culture), mention visible text and its meaning, provide deeper analysis when relevant (especially for financial charts, graphs, or documents), comment on style or architecture if relevant, then give a concise overall summary. Describe in Thai.
</figure>


- Page Numbers: Wrap page numbers in <page_number>...</page_number> (e.g., <page_number>14</page_number>).
- Checkboxes: Use ☐ for unchecked and ☑ for checked boxes.
    """  # noqa: E501

OCR_TARGET_IMAGE_DIM = 1800
# package ใช้ 16384 แต่สลิปมีข้อความไม่กี่ร้อย token — จำกัดไว้ไม่ให้โมเดลที่วนซ้ำกินเวลาจนเกิน timeout
OCR_MAX_TOKENS = 4096
OCR_EXTRA_BODY = {"repetition_penalty": 1.1, "temperature": 0.1, "top_p": 0.6}


def _resize_if_needed(img: Image.Image, max_size: int = OCR_TARGET_IMAGE_DIM) -> Image.Image:
    """เหมือน typhoon_ocr.ocr_utils.resize_if_needed: ถ้าด้านใดเกิน 300 px ให้ปรับด้านยาวเป็น max_size
    (ทั้งย่อและขยาย) คงอัตราส่วน ปัดเศษลงด้วย int()"""
    width, height = img.size
    if width <= 300 and height <= 300:
        return img
    if width >= height:
        new_size = (max_size, int(height * (max_size / float(width))))
    else:
        new_size = (int(width * (max_size / float(height))), max_size)
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _to_rgb(img: Image.Image) -> Image.Image:
    """แปลงเป็น RGB; รูปโปร่งใสปูพื้นขาวก่อน (convert ตรง ๆ จะได้พื้นดำ)"""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _encode_image(image_bytes: bytes, mime_type: str) -> tuple[str, str]:
    """คืน (mime, base64) สำหรับ data URL — resize + JPEG แบบเดียวกับ package typhoon-ocr
    ถ้า Pillow เปิดไม่ได้ ส่ง bytes เดิมตาม mime_type ที่ได้รับ ให้ฝั่ง API ตัดสิน"""
    try:
        with Image.open(io.BytesIO(image_bytes)) as src:
            img = ImageOps.exif_transpose(src)  # รูปถ่ายจากมือถือ: หมุนตาม EXIF
            img = _resize_if_needed(_to_rgb(img))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG")
    except Exception:
        log.warning("Pillow เปิดรูปไม่ได้ ส่งไฟล์เดิม (%s) ให้ OCR", mime_type)
        return mime_type, base64.b64encode(image_bytes).decode("ascii")
    # package ติดป้าย data:image/png ทั้งที่เป็น JPEG — ที่นี่ติดป้ายตามจริง
    return "image/jpeg", base64.b64encode(buffer.getvalue()).decode("ascii")


def _client(api_key: str) -> OpenAI:
    # max_retries=0: ปิด retry ของ SDK แล้วทำ backoff เฉพาะ 429 เอง
    return OpenAI(api_key=api_key, base_url=TYPHOON_BASE_URL, timeout=REQUEST_TIMEOUT, max_retries=0)


def _create_with_backoff(client: OpenAI, **kwargs: Any) -> Any:
    """chat.completions.create + exponential backoff เมื่อโดน HTTP 429 (สูงสุด MAX_429_RETRIES ครั้ง)"""
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except openai.RateLimitError:
            if attempt >= MAX_429_RETRIES:
                raise
            delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            log.warning("Typhoon API 429 (rate limit) รอ %.0f วินาทีแล้วลองใหม่ (%d/%d)",
                        delay, attempt + 1, MAX_429_RETRIES)
            _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _message_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content.strip() if isinstance(content, str) else ""


def ocr_image(image_bytes: bytes, api_key: str, mime_type: str = "image/png") -> str:
    """ส่งรูปสลิปไป Typhoon OCR (v1.5) → ข้อความ Markdown ทั้งสลิป; ล้มเหลว raise"""
    mime, b64 = _encode_image(image_bytes, mime_type)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_PROMPT_V15},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    with _client(api_key) as client:
        response = _create_with_backoff(
            client,
            model=OCR_MODEL,
            messages=messages,
            max_tokens=OCR_MAX_TOKENS,
            extra_body=dict(OCR_EXTRA_BODY),
        )
    text = _message_text(response)
    if not text:
        raise RuntimeError("Typhoon OCR ไม่ได้ส่งข้อความกลับมา")
    return text


# =============================================================== Typhoon LLM → JSON
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 512
MAX_OCR_CHARS_FOR_LLM = 8000  # กัน prompt ยาวผิดปกติเมื่อ OCR วนซ้ำ

LLM_SYSTEM_PROMPT = """\
คุณคือระบบดึงข้อมูลจากสลิปโอนเงิน/ชำระเงินของธนาคารไทย (Thai bank transfer slip)
ข้อความที่ได้รับคือผล OCR (Markdown/HTML) ของสลิป 1 ใบ อาจสะกดผิดหรือเว้นวรรคเพี้ยนเล็กน้อย

ตอบเป็น JSON object เดียวเท่านั้น (JSON only) ห้ามมีคำอธิบาย ห้ามครอบด้วย ``` และใช้ key ตามนี้เท่านั้น:
{"amount": <ตัวเลข หรือ null>, "date": "YYYY-MM-DD" หรือ null, "time": "HH:MM" หรือ null, "receiver": "<ชื่อผู้รับ>" หรือ null}

กติกา:
1. amount = ยอดเงินที่โอน/จ่ายจริง (เช่น "จำนวน", "จำนวนเงิน", "ยอดเงิน", "Amount") เป็นตัวเลข JSON ไม่มีจุลภาค ไม่มีหน่วย
   เช่น "1,234.50 บาท" → 1234.5
   - ห้ามใช้ค่าธรรมเนียม (ค่าธรรมเนียม / fee) และห้ามใช้ยอดเงินคงเหลือ (คงเหลือ / balance)
2. date = วันที่ทำรายการ รูปแบบ YYYY-MM-DD เป็นปี ค.ศ. (Gregorian / CE)
   - ปีบนสลิปไทยเป็น พ.ศ. (Buddhist Era) ต้องลบ 543: "2569" = ค.ศ. 2026; ปี 2 หลัก "69" = พ.ศ. 2569 = ค.ศ. 2026
   - ถ้าสลิปภาษาอังกฤษแสดงปี ค.ศ. อยู่แล้ว (เช่น 2026) ให้ใช้ตามนั้น
   - เดือนย่อภาษาไทย: ม.ค.=01, ก.พ.=02, มี.ค.=03, เม.ย.=04, พ.ค.=05, มิ.ย.=06, ก.ค.=07, ส.ค.=08, ก.ย.=09, ต.ค.=10, พ.ย.=11, ธ.ค.=12
     (ชื่อเต็มก็เช่นกัน เช่น มกราคม=01, ธันวาคม=12)
   - ตัวอย่าง: "26 ต.ค. 69" → "2026-10-26", "5 ม.ค. 2569" → "2026-01-05"
3. time = เวลาทำรายการแบบ 24 ชั่วโมง "HH:MM" (ตัดวินาทีและคำว่า "น." ออก) เช่น "14:30:15 น." → "14:30"
4. receiver = ชื่อผู้รับเงินหรือร้านค้าปลายทาง (ฝั่ง "ไปยัง" / "ถึง" / "ผู้รับ" / "To") ไม่ใช่ชื่อผู้โอน
   ไม่ต้องใส่เลขบัญชีหรือชื่อธนาคาร
5. ค่าใดไม่พบหรือไม่แน่ใจ ให้เป็น null (unknown → null)
"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)
_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?::\d{2})?\s*(?:น\.?)?\s*$")
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NULL_WORDS = {"null", "none", "n/a", "-", "ไม่ทราบ", "ไม่พบ"}


def _parse_json_object(content: str) -> dict[str, Any]:
    """ดึง JSON object ตัวแรกจากคำตอบ LLM (ตัด <think>, ```json fence, ข้อความรอบ ๆ)"""
    text = _THINK_RE.sub("", content or "").strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("LLM ไม่ได้ตอบเป็น JSON object")


def _coerce_amount(value: Any) -> float | None:
    """85 / "1,234.50" / "฿ 99.00" / "1,000.00 บาท" → float (> 0) ; อย่างอื่น → None"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        cleaned = re.sub(r"[฿,\s]|บาท|THB|baht", "", value.translate(_THAI_DIGITS), flags=re.I)
        try:
            number = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    number = abs(number)  # บางแอปแสดงยอดโอนออกเป็นค่าติดลบ
    return round(number, 2) if number > 0 else None


def _normalize_date(value: Any, today: _date | None = None) -> str | None:
    """ตรวจรูปแบบ YYYY-MM-DD และแก้ปี พ.ศ. ที่หลุดมา; ไม่ถูกต้อง → None"""
    if not isinstance(value, str):
        return None
    match = _DATE_RE.match(value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    today = today or _date.today()
    if year > 2400:
        year -= 543  # ยังเป็น พ.ศ. (เช่น 2569) → ค.ศ.
    elif today.year + 1 < year < 2100:
        # LLM มักแปลปี พ.ศ. 2 หลัก "69" เป็น 2069 — ปีที่ถูกคือ 2569 - 543 = 2026 (= ปี - 43)
        fixed = year - 43
        if today.year - 10 <= fixed <= today.year + 1:
            year = fixed
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def _normalize_time(value: Any) -> str | None:
    """แปลง "14:30" / "9:05" / "14:30:59" / "14.30 น." → "HH:MM" ; ไม่ถูกต้อง → None"""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _clean_receiver(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or text.lower() in _NULL_WORDS:
        return None
    return text


def extract_fields(ocr_text: str, api_key: str, model: str) -> SlipData:
    """ส่งข้อความ OCR ไป Typhoon LLM → SlipData (amount/date/time/receiver); ล้มเหลว raise"""
    user_prompt = (
        f"วันนี้ (ค.ศ.): {_date.today().isoformat()}\n"
        "ข้อความ OCR ของสลิป:\n"
        "<<<\n"
        f"{ocr_text[:MAX_OCR_CHARS_FOR_LLM]}\n"
        ">>>\n"
        "ตอบเป็น JSON object เดียวเท่านั้น"
    )
    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    with _client(api_key) as client:
        response = _create_with_backoff(
            client,
            model=model,
            messages=messages,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
    data = _parse_json_object(_message_text(response))
    return SlipData(
        amount=_coerce_amount(data.get("amount")),
        date=_normalize_date(data.get("date")),
        time=_normalize_time(data.get("time")),
        receiver=_clean_receiver(data.get("receiver")),
    )


# =============================================================== regex สำรอง
# กติกาการเลือกยอด (fallback_amount):
#   1. ตัวเลขที่เข้าข่าย = มีทศนิยม 2 ตำแหน่ง หรือ ติดสัญลักษณ์เงิน (฿ / THB / บาท / บ.) หรือ อยู่หลัง label ยอดเงิน
#      (จำนวน / ยอด / Amount / Total) — ตัวเลขที่เป็นส่วนของวันที่ เวลา (14:30, 14.30 น.) เปอร์เซ็นต์ ถูกตัดทิ้ง
#   2. label = ข้อความระหว่างตัวเลขก่อนหน้า (หรือต้นบรรทัด) กับตัวเลขนี้; ถ้าตัวเลขเป็นตัวแรกของบรรทัดและไม่มี label
#      ใช้ label ที่ค้างท้ายบรรทัดก่อนหน้า (รองรับ "จำนวน:" แล้วยอดอยู่บรรทัดถัดไป / หัวตาราง markdown)
#   3. label มี ค่าธรรมเนียม / fee / คงเหลือ / balance → ไม่ใช่ยอดโอน ทิ้ง ; ยอด 0 ก็ทิ้ง
#   4. คะแนน = 3 (label ยอดเงิน) + 2 (ติดสัญลักษณ์เงิน) + 2 (ทศนิยม 2 ตำแหน่ง) → เลือกคะแนนสูงสุด
#      เสมอกันเลือกตัวที่อยู่บนสุด (ยอดเงินมักอยู่ส่วนบนของสลิป)
#   5. ค้นนอก <figure>...</figure> (คำบรรยายรูปที่โมเดลแต่งเอง) ก่อน ไม่เจอค่อยค้นรวม figure

_NUMBER_RE = re.compile(
    r"(?<![\d.,:/])"                       # ไม่ใช่ส่วนท้ายของตัวเลข / วันที่ / เวลา
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"     # 1,234,567 หรือ 1234567
    r"(?P<dec>\.\d{2})?"
    r"(?!\d|[.,:/]\d|\s*%|\s*(?:น\.|นาฬิกา))"  # ไม่ใช่ 14:30, 26/10, 14.30 น., 5%
)
_CURRENCY_BEFORE_RE = re.compile(r"(?:฿|THB)\s*-?\s*$", re.I)
_CURRENCY_AFTER_RE = re.compile(r"\s*(?:บาท|บ\.|฿|THB|baht)", re.I)
_AMOUNT_LABEL_RE = re.compile(r"จำนวน|ยอด|amount|total", re.I)
_EXCLUDE_LABEL_RE = re.compile(r"ค่าธรรมเนียม|\bfees?\b|คงเหลือ|balance", re.I)
_FIGURE_RE = re.compile(r"<figure>.*?</figure>", re.S | re.I)
_LINE_BREAK_TAG_RE = re.compile(r"<br\s*/?>|</(?:tr|p|div|li|h[1-6]|table|thead|tbody|figure)>", re.I)
_CELL_END_TAG_RE = re.compile(r"</t[dh]>", re.I)
_TAG_RE = re.compile(r"<[^>\n]+>")
_HAS_WORD_RE = re.compile(r"\w")


def _classify_label(segment: str) -> str | None:
    if _EXCLUDE_LABEL_RE.search(segment):
        return "exclude"
    if _AMOUNT_LABEL_RE.search(segment):
        return "amount"
    return None


def _ocr_to_lines(text: str) -> list[str]:
    """Markdown/HTML จาก OCR → บรรทัดข้อความล้วน (แถวตาราง HTML = 1 บรรทัด, เลขไทย → อารบิก)"""
    text = text.translate(_THAI_DIGITS)
    text = _LINE_BREAK_TAG_RE.sub("\n", text)
    text = _CELL_END_TAG_RE.sub(" | ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text).splitlines()


def _amount_candidates(lines: Iterable[str]) -> Iterator[tuple[float, int]]:
    """ไล่หา (ยอด, คะแนน) ตามลำดับบนลงล่าง"""
    carried_label: str | None = None
    for line in lines:
        if not _HAS_WORD_RE.search(line):
            continue  # บรรทัดว่าง / เส้นคั่นตาราง |---| → label บรรทัดก่อนยังส่งต่อได้
        prev_end = 0
        for index, match in enumerate(_NUMBER_RE.finditer(line)):
            label = _classify_label(line[prev_end:match.start()])
            if label is None and index == 0:
                label = carried_label
            prev_end = match.end()
            if label == "exclude":
                continue
            value = float(match["int"].replace(",", "") + (match["dec"] or ""))
            if value <= 0:
                continue
            has_label = label == "amount"
            has_currency = bool(
                _CURRENCY_BEFORE_RE.search(line, 0, match.start())
                or _CURRENCY_AFTER_RE.match(line, match.end())
            )
            has_decimals = match["dec"] is not None
            if not (has_label or has_currency or has_decimals):
                continue
            yield value, 3 * has_label + 2 * has_currency + 2 * has_decimals
        carried_label = _classify_label(line[prev_end:])


def _best_amount(text: str) -> float | None:
    best: tuple[float, int] | None = None
    for value, score in _amount_candidates(_ocr_to_lines(text)):
        if best is None or score > best[1]:
            best = (value, score)
    return best[0] if best else None


def fallback_amount(text: str) -> float | None:
    """หายอดเงินจากข้อความ OCR ด้วย regex (ใช้เมื่อ LLM ล้มเหลว) — ดูกติกาด้านบน; ไม่เจอ → None"""
    if not text:
        return None
    without_figures = _FIGURE_RE.sub("\n", text)
    amount = _best_amount(without_figures)
    if amount is None and without_figures != text:
        amount = _best_amount(text)
    return amount


# =============================================================== pipeline

def _describe_error(exc: BaseException) -> str:
    """เหตุผลสั้น ๆ ภาษาไทย (ไม่มี API key / ข้อความ OCR)"""
    if isinstance(exc, openai.APITimeoutError):
        return "หมดเวลารอ Typhoon API"
    if isinstance(exc, openai.APIConnectionError):
        return "เชื่อมต่อ Typhoon API ไม่ได้"
    if isinstance(exc, openai.AuthenticationError):
        return "API key ไม่ถูกต้อง (HTTP 401)"
    if isinstance(exc, openai.RateLimitError):
        return "เรียก Typhoon API ถี่เกินไป (HTTP 429)"
    if isinstance(exc, openai.APIStatusError):
        return f"Typhoon API ตอบกลับ HTTP {exc.status_code}"
    message = str(exc).strip()[:120]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def ocr_first(
    image_bytes: bytes, api_key: str, mime_type: str = "image/png", *, copies: int = 2,
    stagger: float = OCR_HEDGE_STAGGER,
) -> str:
    """ยิง OCR รูปเดียวกันได้ถึง `copies` request แล้วใช้คำตอบแรกที่สำเร็จ

    เวลารอคิวของ Typhoon สุ่มมาก (วัดได้ 8–60 วิกับรูปเดิม) — request สำรองช่วยตัดเคสที่ติดคิวนาน
    request ถัดไปเริ่มเมื่อตัวก่อนยังไม่ตอบภายใน `stagger` วินาที หรือล้มไปแล้ว (Typhoon OCR จำกัด 2 req/s)
    ล้มทุก request → raise error ของ request แรก ; ตัวที่แพ้ปล่อยให้จบเองใน thread (ยกเลิก HTTP กลางทางไม่ได้)
    """
    if copies <= 1:
        return ocr_image(image_bytes, api_key, mime_type)
    pool = ThreadPoolExecutor(max_workers=copies)
    try:
        started = [pool.submit(ocr_image, image_bytes, api_key, mime_type)]
        pending = set(started)
        errors: list[BaseException] = []
        while pending:
            more = len(started) < copies
            done, pending = wait(pending, timeout=stagger if more else None, return_when=FIRST_COMPLETED)
            for future in done:
                if future.exception() is None:
                    log.info("OCR ได้คำตอบจาก request ที่ %d/%d", started.index(future) + 1, len(started))
                    return future.result()
                errors.append(future.exception())  # type: ignore[arg-type]
            if more:
                future = pool.submit(ocr_image, image_bytes, api_key, mime_type)
                started.append(future)
                pending.add(future)
        raise errors[0]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _read_slip(
    image_bytes: bytes, api_key: str, model: str, mime_type: str, copies: int, expect: float | None
) -> SlipData:
    try:
        text = ocr_first(image_bytes, api_key, mime_type, copies=copies)
    except Exception as exc:
        reason = _describe_error(exc)
        log.warning("OCR ล้มเหลว: %s", reason)
        return SlipData(error=f"OCR ล้มเหลว: {reason}")

    if expect is not None and fallback_amount(text) == expect:
        log.info("ยอดในสลิปตรงกับยอดที่คาดไว้ ข้าม LLM")  # ยอดตรง = ผ่าน ไม่ต้องรอ LLM อีก ~1 วิ
        return SlipData(amount=expect)

    try:
        data = extract_fields(text, api_key, model)
    except Exception as exc:
        reason = _describe_error(exc)
        amount = fallback_amount(text)
        log.warning("LLM ล้มเหลว (%s) ใช้ regex สำรอง: amount=%s", reason, amount)
        return SlipData(amount=amount, error=f"LLM ล้มเหลว ใช้ regex สำรอง: {reason}")

    if data.amount is None:
        data.amount = fallback_amount(text)
        log.info("LLM ไม่พบยอด ใช้ regex สำรอง: amount=%s", data.amount)
    else:
        log.info("อ่านสลิปได้: amount=%s", data.amount)
    return data


def read_slip(
    image_bytes: bytes, api_key: str, model: str, mime_type: str = "image/png", *, copies: int = 1,
    expect: float | None = None,
) -> SlipData:
    """OCR → LLM → (regex สำรอง) ; ไม่ raise เด็ดขาด — ล้มเหลวคืน SlipData(error=...)

    copies > 1 = ยิง OCR สำรอง (ocr_first) ; expect = ยอดที่คาดไว้ — regex เจอยอดนี้พอดีก็ไม่ต้องถาม LLM
    """
    try:
        return _read_slip(image_bytes, api_key, model, mime_type, copies, expect)
    except Exception as exc:  # กันทุกกรณีที่ไม่คาดคิด
        reason = _describe_error(exc)
        log.error("อ่านสลิปล้มเหลว: %s", reason)
        return SlipData(error=f"อ่านสลิปไม่สำเร็จ: {reason}")


# =============================================================== CLI
_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("วิธีใช้: python slip_reader.py path/to/slip.jpg", file=sys.stderr)
        return 2

    from config import ConfigError, load_config

    try:
        cfg = load_config(required=["TYPHOON_API_KEY"])
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    path = Path(args[0])
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        print(f"เปิดไฟล์ไม่ได้: {exc}", file=sys.stderr)
        return 1

    mime_type = _MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    result = read_slip(image_bytes, cfg.typhoon_api_key, cfg.typhoon_llm_model, mime_type)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.error is None else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
