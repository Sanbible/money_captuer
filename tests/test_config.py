"""Unit tests สำหรับ config.py — ตั้งค่าผ่าน monkeypatch ไม่อ่าน .env จริง"""
from __future__ import annotations

import pytest

import config
from config import BOT_REQUIRED, WEB_REQUIRED, ConfigError, load_config

ENV = {
    "DISCORD_TOKEN": "discord-token",
    "DISCORD_CHANNEL_ID": "123",
    "TYPHOON_API_KEY": "typhoon-key",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_ANON_KEY": "sb_publishable_x",
    "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_x",
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    for name in (*ENV, "PUBLIC_BASE_URL", "CATEGORIES", "TYPHOON_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def test_web_config_reads_supabase_settings():
    cfg = load_config(required=WEB_REQUIRED)
    assert cfg.supabase_url == "https://example.supabase.co"
    assert cfg.supabase_anon_key == "sb_publishable_x"
    assert cfg.supabase_service_role_key == "sb_secret_x"


def test_web_config_does_not_need_discord(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN")
    monkeypatch.delenv("DISCORD_CHANNEL_ID")
    cfg = load_config(required=WEB_REQUIRED)
    assert cfg.discord_channel_id == 0


@pytest.mark.parametrize("required", [WEB_REQUIRED, BOT_REQUIRED])
def test_missing_supabase_url_raises(monkeypatch, required):
    monkeypatch.delenv("SUPABASE_URL")
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        load_config(required=required)


def test_bot_config_does_not_need_google_sheet():
    cfg = load_config(required=BOT_REQUIRED)
    assert not hasattr(cfg, "google_sheet_id")


def test_public_base_url_defaults_to_empty():
    assert load_config(required=WEB_REQUIRED).public_base_url == ""


def test_public_base_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://money.onrender.com/")
    assert load_config(required=WEB_REQUIRED).public_base_url == "https://money.onrender.com"
