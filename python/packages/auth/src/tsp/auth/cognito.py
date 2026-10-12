from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx
import jwt
from pydantic import BaseModel, Field


class AuthError(Exception):
    """The token is missing, malformed, expired, or not meant for this app."""


class Claims(BaseModel):
    sub: str
    token_use: str
    username: str | None = None
    email: str | None = None
    groups: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)


class CognitoVerifier:
    """Validates signature, issuer, expiry, token type and app client of Cognito JWTs.

    ``issuer`` is ``https://cognito-idp.<region>.amazonaws.com/<user-pool-id>``. Keys are fetched
    from its JWKS once and cached; an unknown ``kid`` triggers at most one refresh per minute
    (key rotation) so a flood of forged tokens cannot hammer Cognito.
    """

    def __init__(
        self,
        issuer: str,
        client_ids: Sequence[str],
        *,
        jwks_ttl: float = 3600,
        leeway: float = 10,
        http: httpx.AsyncClient | None = None,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_ids = set(client_ids)
        if not self.client_ids:
            raise ValueError("at least one app client id is required")
        self._jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self._ttl = jwks_ttl
        self._leeway = leeway
        self._http = http or httpx.AsyncClient(timeout=5)
        self._owns_http = http is None
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    async def _refresh(self) -> None:
        resp = await self._http.get(self._jwks_url)
        resp.raise_for_status()
        self._keys = {
            k["kid"]: jwt.PyJWK.from_dict(k).key
            for k in resp.json()["keys"]
            if k.get("kty") == "RSA"
        }
        self._fetched_at = time.monotonic()

    async def _key(self, kid: str) -> Any:
        age = time.monotonic() - self._fetched_at
        if kid not in self._keys and (not self._keys or age > 60) or age > self._ttl:
            try:
                await self._refresh()
            except httpx.HTTPError as e:
                if not self._keys:
                    raise AuthError(f"cannot load signing keys: {e}") from e
        if kid not in self._keys:
            raise AuthError("unknown signing key")
        return self._keys[kid]

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("malformed token") from e
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AuthError("unsupported token header")
        key = await self._key(header["kid"])
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthError("token expired") from e
        except jwt.PyJWTError as e:
            raise AuthError("invalid token") from e

        use = claims.get("token_use")
        if use == "access":
            audience = claims.get("client_id")
        elif use == "id":
            audience = claims.get("aud")
        else:
            raise AuthError("unexpected token type")
        if audience not in self.client_ids:
            raise AuthError("token was issued for a different app")

        return Claims(
            sub=claims["sub"],
            token_use=use,
            username=claims.get("username") or claims.get("cognito:username"),
            email=claims.get("email"),
            groups=claims.get("cognito:groups", []),
            scope=claims.get("scope", "").split(),
            raw=claims,
        )

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
