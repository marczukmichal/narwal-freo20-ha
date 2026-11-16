"""Minimal Narwal cloud client (login with email + password, token refresh).

The password is used only for the login call and is never stored.
Tokens live in the config entry, never in logs.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from aiohttp import ClientSession

APP_VERSION = "2.11.90"
HOSTS = {
    "eu": "eu-app.narwaltech.com",
    "us": "us-app.narwaltech.com",
    "il": "il-app.narwaltech.com",
}
LOGIN_PATH = "/user-authentication-server/v2/login/loginByEmail"
REFRESH_PATH = "/user-authentication-server/v1/token/refresh"


class NarwalChmuraError(Exception):
    """Cloud error."""


class NarwalChmuraAuthError(NarwalChmuraError):
    """Bad credentials or expired session."""


def _jwt_exp(token: str) -> float:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp", 0))
    except Exception:  # noqa: BLE001
        return 0.0


class NarwalChmuraApi:
    def __init__(self, session: ClientSession, region: str, tokens: dict | None = None) -> None:
        self._session = session
        self.region = region
        self.host = HOSTS.get(region, HOSTS["eu"])
        self.tokens: dict[str, Any] = dict(tokens or {})

    def headers(self, device_id: str = "", product_id: str = "") -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "APP-VERSION": APP_VERSION,
            "app_version": APP_VERSION,
            "APP-LANGUAGE": "en",
            "language_code": "en",
            "country_code": "PL",
            "is_app": "true",
            "isMainland": "false",
            "is_beta": "false",
            "app_type": "normal",
            "platform": "android",
            "did": device_id,
            "Aiot-Application-Id": product_id,
            "Auth-Token": self.tokens.get("token", ""),
            "UUID": self.tokens.get("uuid", ""),
        }

    async def _raw(self, method: str, path: str, *, body=None, params=None,
                   device_id: str = "", product_id: str = "") -> dict:
        async with self._session.request(
            method, f"https://{self.host}{path}", json=body, params=params,
            headers=self.headers(device_id, product_id), timeout=20,
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except ValueError:
                data = {"http_status": resp.status, "text": text[:500]}
            if isinstance(data, dict):
                data.setdefault("http_status", resp.status)
            return data

    def _store(self, result: dict) -> None:
        token = result.get("token") or ""
        if not token:
            raise NarwalChmuraAuthError("no token in the response")
        self.tokens["token"] = token
        self.tokens["refresh_token"] = (
            result.get("refresh_token") or result.get("refreshToken")
            or self.tokens.get("refresh_token") or token
        )
        if result.get("uuid"):
            self.tokens["uuid"] = result["uuid"]
        self.tokens["exp"] = _jwt_exp(token)

    async def login(self, email: str, password: str) -> None:
        data = await self._raw("POST", LOGIN_PATH, body={"email": email, "password": password})
        if data.get("code") != 0:
            raise NarwalChmuraAuthError(str(data.get("msg") or "login failed"))
        self._store(data.get("result") or {})

    async def refresh(self) -> None:
        rt = self.tokens.get("refresh_token")
        if not rt:
            raise NarwalChmuraAuthError("no refresh token")
        data = await self._raw("POST", REFRESH_PATH, body={"refreshToken": rt})
        if data.get("code") != 0:
            raise NarwalChmuraAuthError(str(data.get("msg") or "token refresh failed"))
        self._store(data.get("result") or {})

    async def ensure_fresh(self) -> bool:
        """Refresh if the token expires within 10 minutes. Returns True if refreshed."""
        exp = self.tokens.get("exp") or 0
        if exp and exp - time.time() > 600:
            return False
        await self.refresh()
        return True

    async def request(self, method: str, path: str, **kw) -> dict:
        await self.ensure_fresh()
        data = await self._raw(method, path, **kw)
        if data.get("http_status") in (401, 403) or str(data.get("code")) in ("401", "403"):
            await self.refresh()
            data = await self._raw(method, path, **kw)
        return data
