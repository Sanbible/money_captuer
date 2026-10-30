"""ทดสอบ slip_reader โดย mock openai client (ไม่เรียก API จริง)"""
from __future__ import annotations

import base64
import io
import json
import logging
from datetime import date
from types import SimpleNamespace

import openai
import pytest

try:  # openai >= 3 ใช้ httpx2 แทน httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover - openai 1.x/2.x
    import httpx
from PIL import Image

import slip_reader
from slip_reader import SlipData

API_KEY = "sk-test-SECRET-key-123"
CHAT_URL = "https://api.opentyphoon.ai/v1/chat/completions"
OCR_TEXT = "โอนเงินสำเร็จ\nไปยัง ร้านลับสุดยอด\nจำนวน: 85.00 บาท\nค่าธรรมเนียม: 0.00 บาท"


# ---------------------------------------------------------------- helpers / fakes

def _png(width: int, height: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    # RGBA = ดำโปร่งใส: ถ้าแปลง RGB ตรง ๆ จะได้สีดำ (ข้อความหาย) ต้องปูพื้นขาวก่อน
    color = (0, 0, 0, 0) if mode == "RGBA" else "white"
    Image.new(mode, (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", CHAT_URL))
    return cls(f"HTTP {status}", response=response, body=None)


def _rate_limit() -> openai.RateLimitError:
    return _status_error(openai.RateLimitError, 429)  # type: ignore[return-value]


def _auth_error() -> openai.AuthenticationError:
    return _status_error(openai.AuthenticationError, 401)  # type: ignore[return-value]


def _completion(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeOpenAI:
    """ใช้แทนคลาส openai.OpenAI: เรียกเป็น factory ได้, จำ kwargs, ตอบตามคิว outcomes"""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.init_kwargs: list[dict] = []
        self.calls: list[dict] = []
        self.closed = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __call__(self, **kwargs: object) -> "FakeOpenAI":
        self.init_kwargs.append(kwargs)
        return self

    def __enter__(self) -> "FakeOpenAI":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed += 1

    def _create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("API ถูกเรียกเกินจำนวนที่คาด")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _completion(outcome)  # type: ignore[arg-type]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    def install(*outcomes: object) -> FakeOpenAI:
        client = FakeOpenAI(list(outcomes))
        monkeypatch.setattr(slip_reader, "OpenAI", client)
        return client

    return install


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(slip_reader, "_sleep", recorded.append)
    return recorded


def _sent_image(call: dict) -> tuple[str, bytes]:
    """คืน (mime, bytes) ของรูปที่ส่งใน OCR request"""
    url = call["messages"][0]["content"][1]["image_url"]["url"]
    header, b64 = url.split(",", 1)
    assert header.startswith("data:") and header.endswith(";base64")
    return header[len("data:"):-len(";base64")], base64.b64decode(b64)


# ---------------------------------------------------------------- client config

def test_client_uses_typhoon_base_url_timeout_and_no_sdk_retries(fake) -> None:
    client = fake("ข้อความ")
    slip_reader.ocr_image(_png(100, 100), API_KEY)
    kwargs = client.init_kwargs[0]
    assert kwargs["base_url"] == "https://api.opentyphoon.ai/v1"
    assert kwargs["api_key"] == API_KEY
    assert kwargs["max_retries"] == 0
    assert 30 <= kwargs["timeout"] <= 120
    assert client.closed >= 1


# ---------------------------------------------------------------- OCR

def test_ocr_request_matches_typhoon_ocr_v15(fake) -> None:
    client = fake("# สลิป")
    slip_reader.ocr_image(_png(100, 100), API_KEY)
    call = client.calls[0]
    assert call["model"] == "typhoon-ocr"
    messages = call["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert content[0] == {"type": "text", "text": slip_reader.OCR_PROMPT_V15}
    assert content[1]["type"] == "image_url"
    assert call["extra_body"] == {"repetition_penalty": 1.1, "temperature": 0.1, "top_p": 0.6}
    assert call["max_tokens"] > 0


def test_ocr_prompt_is_v15_text() -> None:
    prompt = slip_reader.OCR_PROMPT_V15
    assert prompt.startswith("Extract all text from the image.\n")
    assert "- Only return the clean Markdown." in prompt
    assert "- Tables: Render tables using <table>...</table> in clean HTML format." in prompt
    assert "then give a concise overall summary. Describe in Thai.\n</figure>" in prompt
    assert prompt.endswith("- Checkboxes: Use ☐ for unchecked and ☑ for checked boxes.\n    ")


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1080, 2340), (830, 1800)),   # สลิปแนวตั้ง → ด้านยาว 1800
        ((2000, 1000), (1800, 900)),   # แนวนอน
        ((400, 600), (1200, 1800)),    # เล็กกว่า 1800 แต่เกิน 300 → ขยายเป็น 1800 เหมือน package
        ((200, 300), (200, 300)),      # ไม่เกิน 300 ทั้งสองด้าน → ไม่ resize
    ],
)
def test_ocr_resizes_like_typhoon_ocr_package(fake, size, expected) -> None:
    client = fake("ok")
    slip_reader.ocr_image(_png(*size), API_KEY)
    mime, data = _sent_image(client.calls[0])
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert img.size == expected


def test_ocr_flattens_transparent_png(fake) -> None:
    client = fake("ok")
    slip_reader.ocr_image(_png(50, 50, mode="RGBA"), API_KEY, mime_type="image/png")
    _, data = _sent_image(client.calls[0])
    with Image.open(io.BytesIO(data)) as img:
        assert img.mode == "RGB"
        r, g, b = img.getpixel((25, 25))
        assert min(r, g, b) > 240  # พื้นโปร่งใส → ขาว ไม่ใช่ดำ


def test_ocr_sends_undecodable_bytes_raw_with_given_mime(fake) -> None:
    client = fake("ok")
    raw = b"not-really-an-image"
    slip_reader.ocr_image(raw, API_KEY, mime_type="image/webp")
    mime, data = _sent_image(client.calls[0])
    assert mime == "image/webp"
    assert data == raw


def test_ocr_returns_markdown_text(fake) -> None:
    fake("  # โอนเงินสำเร็จ\n85.00 บาท  ")
    assert slip_reader.ocr_image(_png(10, 10), API_KEY) == "# โอนเงินสำเร็จ\n85.00 บาท"


@pytest.mark.parametrize("content", [None, "", "   \n"])
def test_ocr_empty_response_raises(fake, content) -> None:
    fake(content)
    with pytest.raises(Exception):
        slip_reader.ocr_image(_png(10, 10), API_KEY)


# ---------------------------------------------------------------- 429 backoff

def test_429_backoff_then_success(fake, sleeps) -> None:
    client = fake(_rate_limit(), _rate_limit(), "ok")
    assert slip_reader.ocr_image(_png(10, 10), API_KEY) == "ok"
    assert len(client.calls) == 3
    assert sleeps == [1, 2]


def test_429_gives_up_after_three_retries(fake, sleeps) -> None:
    client = fake(_rate_limit(), _rate_limit(), _rate_limit(), _rate_limit(), "never")
    with pytest.raises(openai.RateLimitError):
        slip_reader.ocr_image(_png(10, 10), API_KEY)
    assert len(client.calls) == 4  # ครั้งแรก + retry 3 ครั้ง
    assert sleeps == [1, 2, 4]


def test_429_backoff_also_applies_to_llm(fake, sleeps) -> None:
    client = fake(_rate_limit(), '{"amount": 10, "date": null, "time": null, "receiver": null}')
    data = slip_reader.extract_fields("ข้อความ", API_KEY, "typhoon-v2.5-30b-a3b-instruct")
    assert data.amount == 10.0
    assert len(client.calls) == 2
    assert sleeps == [1]


def test_non_429_error_is_not_retried(fake, sleeps) -> None:
    client = fake(_auth_error(), "never")
    with pytest.raises(openai.AuthenticationError):
        slip_reader.ocr_image(_png(10, 10), API_KEY)
    assert len(client.calls) == 1
    assert sleeps == []


# ---------------------------------------------------------------- LLM extraction

MODEL = "typhoon-v2.5-30b-a3b-instruct"


def test_extract_fields_request(fake) -> None:
    client = fake('{"amount": 85, "date": "2026-10-26", "time": "14:30", "receiver": "ร้าน"}')
    slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)
    call = client.calls[0]
    assert call["model"] == MODEL
    assert 0 <= call["temperature"] <= 0.2
    system = call["messages"][0]
    assert system["role"] == "system"
    for needle in ("พ.ศ.", "2569", "2026", "ค่าธรรมเนียม", "ม.ค.", "ธ.ค.", "YYYY-MM-DD", "HH:MM", "null", "JSON"):
        assert needle in system["content"], needle
    user = call["messages"][-1]
    assert user["role"] == "user"
    assert OCR_TEXT in user["content"]


def test_extract_fields_plain_json(fake) -> None:
    fake('{"amount": 85.0, "date": "2026-10-26", "time": "14:30", "receiver": "ร้านลับสุดยอด"}')
    data = slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)
    assert data == SlipData(amount=85.0, date="2026-10-26", time="14:30", receiver="ร้านลับสุดยอด")


def test_extract_fields_strips_json_fence(fake) -> None:
    fake('```json\n{"amount": "1,234.50", "date": "2026-01-05", "time": "09:05", "receiver": null}\n```')
    data = slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)
    assert data == SlipData(amount=1234.5, date="2026-01-05", time="09:05", receiver=None)


def test_extract_fields_finds_object_in_surrounding_text(fake) -> None:
    fake('นี่คือผลลัพธ์:\n{"amount": 99, "date": null, "time": null, "receiver": "A {B}"}\nหวังว่าจะช่วยได้')
    data = slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)
    assert data.amount == 99.0 and data.receiver == "A {B}"


def test_extract_fields_ignores_think_block(fake) -> None:
    fake('<think>ลองคิดดู {ไม่ใช่ json}</think>\n{"amount": 42.5, "date": null, "time": null, "receiver": null}')
    assert slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL).amount == 42.5


def test_extract_fields_converts_buddhist_year(fake) -> None:
    fake('{"amount": 1, "date": "2569-10-26", "time": "14:30", "receiver": null}')
    assert slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL).date == "2026-10-26"


def test_extract_fields_invalid_values_become_none(fake) -> None:
    fake('{"amount": "abc", "date": "2026-13-45", "time": "25:99", "receiver": "  "}')
    data = slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)
    assert data == SlipData()


@pytest.mark.parametrize(
    "content",
    ["ขอโทษ อ่านไม่ออก", "", '["not", "an", "object"]', '{"amount": 1,'],
)
def test_extract_fields_unparseable_raises(fake, content) -> None:
    fake(content)
    with pytest.raises(ValueError):
        slip_reader.extract_fields(OCR_TEXT, API_KEY, MODEL)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (85, 85.0),
        (85.5, 85.5),
        ("1,234.50", 1234.5),
        ("฿ 99.00", 99.0),
        ("1,000.00 บาท", 1000.0),
        ("THB 20", 20.0),
        (-350.0, 350.0),
        (0, None),
        (None, None),
        (True, None),
        ("", None),
        ("ไม่ทราบ", None),
        (float("nan"), None),
    ],
)
def test_coerce_amount(value, expected) -> None:
    assert slip_reader._coerce_amount(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-26", "2026-10-26"),
        ("2026-1-5", "2026-01-05"),
        ("2569-10-26", "2026-10-26"),        # พ.ศ. → ค.ศ.
        ("2069-10-26", "2026-10-26"),        # LLM ตีความ "69" เป็น 2069 → แก้เป็น 2026
        ("2027-01-02", "2027-01-02"),        # อนาคตไม่เกิน 1 ปี ไม่แตะ
        ("2026-02-30", None),
        ("26/10/2026", None),
        ("", None),
        (None, None),
        (20261026, None),
    ],
)
def test_normalize_date(value, expected) -> None:
    assert slip_reader._normalize_date(value, today=date(2026, 10, 26)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14:30", "14:30"),
        ("9:05", "09:05"),
        ("14:30:59", "14:30"),
        ("14.30", "14:30"),
        ("14:30 น.", "14:30"),
        ("24:00", None),
        ("12:60", None),
        ("noon", None),
        (None, None),
    ],
)
def test_normalize_time(value, expected) -> None:
    assert slip_reader._normalize_time(value) == expected


# ---------------------------------------------------------------- read_slip pipeline

def test_read_slip_success(fake) -> None:
    client = fake(OCR_TEXT, '{"amount": 85, "date": "2569-10-26", "time": "14:30", "receiver": "ร้านลับสุดยอด"}')
    result = slip_reader.read_slip(_png(100, 100), API_KEY, MODEL)
    assert result == SlipData(amount=85.0, date="2026-10-26", time="14:30", receiver="ร้านลับสุดยอด")
    assert [c["model"] for c in client.calls] == ["typhoon-ocr", MODEL]


def test_read_slip_ocr_failure_returns_error(fake) -> None:
    client = fake(_auth_error())
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result.amount is None
    assert result.error and "401" in result.error
    assert len(client.calls) == 1  # ไม่เรียก LLM ต่อ


def test_read_slip_ocr_rate_limited_returns_error(fake, sleeps) -> None:
    fake(*[_rate_limit()] * 4)
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result.amount is None and result.error and "429" in result.error
    assert sleeps == [1, 2, 4]


def test_read_slip_ocr_network_error_returns_error(fake) -> None:
    fake(openai.APIConnectionError(request=httpx.Request("POST", CHAT_URL)))
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result.amount is None and result.error


def test_read_slip_llm_garbage_falls_back_to_regex(fake) -> None:
    fake(OCR_TEXT, "ขอโทษครับ ไม่สามารถตอบได้")
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result.amount == 85.0
    assert result.date is None and result.receiver is None
    assert result.error  # บอกว่า LLM ล้ม ใช้ regex สำรอง


def test_read_slip_llm_api_error_falls_back_to_regex(fake) -> None:
    fake(OCR_TEXT, _auth_error())
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result.amount == 85.0
    assert result.error


def test_read_slip_llm_null_amount_uses_regex(fake) -> None:
    fake(OCR_TEXT, '{"amount": null, "date": "2026-10-26", "time": null, "receiver": "ร้าน"}')
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert result == SlipData(amount=85.0, date="2026-10-26", receiver="ร้าน")


def test_read_slip_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(slip_reader, "ocr_image", lambda *a, **k: OCR_TEXT)
    monkeypatch.setattr(slip_reader, "extract_fields", boom)
    monkeypatch.setattr(slip_reader, "fallback_amount", boom)
    result = slip_reader.read_slip(b"x", API_KEY, MODEL)
    assert isinstance(result, SlipData)
    assert result.amount is None and result.error

    monkeypatch.setattr(slip_reader, "ocr_image", boom)
    result = slip_reader.read_slip(b"x", API_KEY, MODEL)
    assert result.amount is None and result.error


def test_read_slip_skips_llm_when_regex_finds_expected_amount(fake) -> None:
    client = fake(OCR_TEXT)  # มีแค่คำตอบ OCR — ถ้าเรียก LLM จะได้ error
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL, expect=85.0)
    assert result == SlipData(amount=85.0)
    assert len(client.calls) == 1


def test_read_slip_expect_mismatch_still_asks_llm(fake) -> None:
    client = fake(OCR_TEXT, '{"amount": 85, "date": null, "time": null, "receiver": null}')
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL, expect=90.0)
    assert result.amount == 85.0
    assert [c["model"] for c in client.calls] == ["typhoon-ocr", MODEL]


# ---------------------------------------------------------------- ยิง OCR ซ้ำ ใช้คำตอบแรก (hedged request)

@pytest.fixture
def ocr_calls(monkeypatch: pytest.MonkeyPatch):
    """แทน ocr_image ด้วยลำดับพฤติกรรม: "slow" = ค้างจนเทสต์จบ, Exception = raise, str = คืนข้อความ"""
    import threading

    release = threading.Event()
    calls: list[int] = []

    def install(*behaviours: object) -> list[int]:
        def fake_ocr(image_bytes: bytes, api_key: str, mime_type: str = "image/png") -> str:
            calls.append(len(calls))
            behaviour = behaviours[len(calls) - 1]
            if behaviour == "slow":
                release.wait(5)
                return "ช้า"
            if isinstance(behaviour, BaseException):
                raise behaviour
            return behaviour  # type: ignore[return-value]

        monkeypatch.setattr(slip_reader, "ocr_image", fake_ocr)
        return calls

    yield install
    release.set()


def test_ocr_first_returns_fastest_copy(ocr_calls) -> None:
    calls = ocr_calls("slow", "เร็ว")
    assert slip_reader.ocr_first(b"x", API_KEY, copies=2, stagger=0.01) == "เร็ว"
    assert len(calls) == 2


def test_ocr_first_no_second_copy_when_first_is_quick(ocr_calls) -> None:
    calls = ocr_calls("ตอบไว")
    assert slip_reader.ocr_first(b"x", API_KEY, copies=2, stagger=1) == "ตอบไว"
    assert len(calls) == 1


def test_ocr_first_uses_other_copy_when_one_fails(ocr_calls) -> None:
    calls = ocr_calls(RuntimeError("ล้ม"), "สำรอง")
    assert slip_reader.ocr_first(b"x", API_KEY, copies=2, stagger=1) == "สำรอง"
    assert len(calls) == 2


def test_ocr_first_raises_first_error_when_all_fail(ocr_calls) -> None:
    ocr_calls(RuntimeError("แรก"), RuntimeError("สอง"))
    with pytest.raises(RuntimeError, match="แรก"):
        slip_reader.ocr_first(b"x", API_KEY, copies=2, stagger=0.01)


def test_read_slip_with_copies_uses_hedged_ocr(fake, ocr_calls) -> None:
    ocr_calls("slow", OCR_TEXT)
    fake('{"amount": 85, "date": null, "time": null, "receiver": null}')  # เหลือแค่ LLM
    result = slip_reader.read_slip(_png(10, 10), API_KEY, MODEL, copies=2)
    assert result.amount == 85.0 and result.error is None


def test_read_slip_does_not_log_ocr_text_or_api_key(fake, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    fake(OCR_TEXT, "garbage")
    slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    fake(OCR_TEXT, '{"amount": 85, "date": null, "time": null, "receiver": "ร้านลับสุดยอด"}')
    slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    fake(_auth_error())
    slip_reader.read_slip(_png(10, 10), API_KEY, MODEL)
    assert caplog.records  # มี log อย่างน้อยยอด/ข้อผิดพลาด
    for record in caplog.records:
        message = record.getMessage()
        assert API_KEY not in message
        assert "ร้านลับสุดยอด" not in message
        assert "โอนเงินสำเร็จ" not in message


# ---------------------------------------------------------------- CLI

def test_cli_prints_json(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    import config

    seen: dict = {}

    def fake_load_config(required=()):
        seen["required"] = list(required)
        return SimpleNamespace(typhoon_api_key=API_KEY, typhoon_llm_model=MODEL)

    def fake_read_slip(image_bytes, api_key, model, mime_type="image/png"):
        seen.update(image_bytes=image_bytes, api_key=api_key, model=model, mime_type=mime_type)
        return SlipData(amount=85.0, date="2026-10-26", time="14:30", receiver="ร้าน")

    monkeypatch.setattr(config, "load_config", fake_load_config)
    monkeypatch.setattr(slip_reader, "read_slip", fake_read_slip)
    slip = tmp_path / "slip.JPG"
    slip.write_bytes(b"jpeg-bytes")

    exit_code = slip_reader.main([str(slip)])

    out = capsys.readouterr().out
    assert json.loads(out) == {
        "amount": 85.0, "date": "2026-10-26", "time": "14:30", "receiver": "ร้าน", "error": None,
    }
    assert "ร้าน" in out  # ensure_ascii=False
    assert exit_code == 0
    assert seen["required"] == ["TYPHOON_API_KEY"]
    assert seen["mime_type"] == "image/jpeg"
    assert seen["image_bytes"] == b"jpeg-bytes"
    assert (seen["api_key"], seen["model"]) == (API_KEY, MODEL)


def test_cli_without_argument_shows_usage(capsys) -> None:
    assert slip_reader.main([]) != 0
    assert "slip_reader.py" in capsys.readouterr().err
