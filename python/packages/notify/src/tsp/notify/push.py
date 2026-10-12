"""Push notifications through Firebase Cloud Messaging (HTTP v1): one service account, Android/iOS/web."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx
import jwt

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class PushResult:
    def __init__(self, sent: int, invalid_tokens: list[str]):
        self.sent, self.invalid_tokens = sent, invalid_tokens


class FcmPush:
    def __init__(
        self,
        service_account: Mapping[str, Any],
        *,
        http: httpx.AsyncClient | None = None,
        clock: Any = time.time,
    ):
        self._sa = dict(service_account)
        self.project_id = self._sa["project_id"]
        self._http = http or httpx.AsyncClient(timeout=15)
        self._clock = clock
        self._token: tuple[str, float] | None = None

    async def _access_token(self) -> str:
        now = self._clock()
        if self._token and self._token[1] - 60 > now:
            return self._token[0]
        assertion = jwt.encode(
            {
                "iss": self._sa["client_email"],
                "scope": _SCOPE,
                "aud": _TOKEN_URL,
                "iat": int(now),
                "exp": int(now) + 3600,
            },
            self._sa["private_key"],
            algorithm="RS256",
        )
        resp = await self._http.post(
            _TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = (data["access_token"], now + int(data.get("expires_in", 3600)))
        return self._token[0]

    async def send(
        self, tokens: list[str], title: str, body: str, data: Mapping[str, str] | None = None
    ) -> PushResult:
        """Send to each device token. Tokens FCM reports as unregistered are returned so you can delete them."""
        bearer = await self._access_token()
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        sent, invalid = 0, []
        for token in tokens:
            msg = {
                "message": {
                    "token": token,
                    "notification": {"title": title, "body": body},
                    "data": dict(data or {}),
                }
            }
            resp = await self._http.post(
                url, json=msg, headers={"Authorization": f"Bearer {bearer}"}
            )
            if resp.status_code == 200:
                sent += 1
            elif resp.status_code in (400, 404) and any(
                d.get("errorCode") in ("UNREGISTERED", "INVALID_ARGUMENT")
                for d in (resp.json().get("error", {}).get("details") or [])
            ):
                invalid.append(token)
            elif resp.status_code == 404:
                invalid.append(token)
            else:
                resp.raise_for_status()
        return PushResult(sent, invalid)

    async def aclose(self) -> None:
        await self._http.aclose()
