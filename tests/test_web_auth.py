"""Unit tests สำหรับ web/auth.py — ใช้ httpx.MockTransport แทน Supabase Auth"""
from __future__ import annotations

import httpx
import pytest

from web.auth import AuthError, AuthUser, SupabaseAuth, discord_id_from_identities

URL = "https://proj.supabase.co"
ANON = "sb_publishable_abc"

USER_JSON = {
    "id": "user-a",
    "email": "a@example.com",
    "identities": [
        {"provider": "google", "identity_data": {"sub": "g-1"}},
        {"provider": "discord", "identity_data": {"provider_id": "123", "sub": "123"}},
    ],
}


def make_auth(handler) -> tuple[SupabaseAuth, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    return SupabaseAuth(URL, ANON, client=httpx.Client(transport=httpx.MockTransport(record))), seen


def test_get_user_returns_auth_user():
    auth, seen = make_auth(lambda req: httpx.Response(200, json=USER_JSON))
    user = auth.get_user("tok")
    assert user == AuthUser(id="user-a", email="a@example.com", discord_id="123", providers=("google", "discord"))
    assert str(seen[0].url) == f"{URL}/auth/v1/user"
    assert seen[0].headers["apikey"] == ANON
    assert seen[0].headers["authorization"] == "Bearer tok"


def test_get_user_without_discord():
    data = {**USER_JSON, "identities": [{"provider": "email", "identity_data": {}}]}
    auth, _ = make_auth(lambda req: httpx.Response(200, json=data))
    user = auth.get_user("tok")
    assert user.discord_id is None and user.providers == ("email",)


@pytest.mark.parametrize("status", [401, 403])
def test_invalid_token_raises_auth_error(status):
    auth, _ = make_auth(lambda req: httpx.Response(status, json={"msg": "invalid JWT"}))
    with pytest.raises(AuthError):
        auth.get_user("bad")


@pytest.mark.parametrize("token", ["", "   ", None])
def test_empty_token_raises_without_request(token):
    auth, seen = make_auth(lambda req: httpx.Response(200, json=USER_JSON))
    with pytest.raises(AuthError):
        auth.get_user(token)
    assert seen == []


@pytest.mark.parametrize(
    "identities, expected",
    [
        ([{"provider": "discord", "identity_data": {"provider_id": "1"}}], "1"),
        ([{"provider": "discord", "identity_data": {"sub": "2"}}], "2"),
        ([{"provider": "discord", "id": "3", "identity_data": {}}], "3"),
        ([{"provider": "google", "identity_data": {"sub": "9"}}], None),
        ([], None),
        (None, None),
    ],
)
def test_discord_id_from_identities(identities, expected):
    assert discord_id_from_identities(identities) == expected
