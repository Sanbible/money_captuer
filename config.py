"""โหลดค่าตั้งค่าจาก .env"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_LLM_MODEL = "typhoon-v2.5-30b-a3b-instruct"
DEFAULT_CATEGORIES = (
    "อาหาร", "เครื่องดื่ม/กาแฟ", "เดินทาง", "ช้อปปิ้ง", "ของใช้",
    "บิล/ค่าบริการ", "สุขภาพ", "บันเทิง", "อื่นๆ",
)

# ตัวแปรที่มีค่าเริ่มต้น ไม่ต้องกรอกก็ได้
ENV_DEFAULTS = {
    "TYPHOON_LLM_MODEL": DEFAULT_LLM_MODEL,
    "GOOGLE_CREDENTIALS_FILE": "credentials.json",
}

ALL_REQUIRED = (
    "DISCORD_TOKEN",
    "DISCORD_CHANNEL_ID",
    "TYPHOON_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDENTIALS_FILE",
)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    discord_channel_id: int
    typhoon_api_key: str
    typhoon_llm_model: str
    google_sheet_id: str
    google_credentials_file: str
    categories: tuple[str, ...]


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def load_config(required: Iterable[str] = ALL_REQUIRED) -> Config:
    """อ่าน .env แล้วคืน Config

    `required` = ชื่อตัวแปรที่ต้องมีค่า ถ้าขาดจะ raise ConfigError
    (สคริปต์ทดสอบที่ใช้แค่บางส่วนส่ง required เฉพาะที่ต้องใช้ได้)
    """
    load_dotenv(BASE_DIR / ".env")

    missing = [name for name in required if not _env(name, ENV_DEFAULTS.get(name, ""))]
    if missing:
        raise ConfigError(f"ยังไม่ได้ตั้งค่าใน .env: {', '.join(missing)}")

    channel_raw = _env("DISCORD_CHANNEL_ID", "0")
    try:
        channel_id = int(channel_raw)
    except ValueError:
        raise ConfigError("DISCORD_CHANNEL_ID ต้องเป็นตัวเลข") from None

    categories = tuple(c.strip() for c in _env("CATEGORIES").split(",") if c.strip())
    if not categories:
        categories = DEFAULT_CATEGORIES
    if len(categories) > 25:
        raise ConfigError("CATEGORIES มีได้ไม่เกิน 25 หมวด (ข้อจำกัดของ Discord select menu)")

    credentials = _env("GOOGLE_CREDENTIALS_FILE", ENV_DEFAULTS["GOOGLE_CREDENTIALS_FILE"])
    if not Path(credentials).is_absolute():
        credentials = str(BASE_DIR / credentials)

    return Config(
        discord_token=_env("DISCORD_TOKEN"),
        discord_channel_id=channel_id,
        typhoon_api_key=_env("TYPHOON_API_KEY"),
        typhoon_llm_model=_env("TYPHOON_LLM_MODEL", ENV_DEFAULTS["TYPHOON_LLM_MODEL"]),
        google_sheet_id=_env("GOOGLE_SHEET_ID"),
        google_credentials_file=credentials,
        categories=categories,
    )
