"""Staff sign-in with the staff Cognito pool: authorization code + PKCE, state and nonce."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from sqladmin.authentication import AuthenticationBackend
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from tsp.audit import client_ip
from tsp.auth import AuthError, CognitoVerifier

from .roles import role_for_groups

COOKIE = "tsp_admin"


@dataclass
class AdminSettings:
    issuer: str  # https://cognito-idp.<region>.amazonaws.com/<staff-pool-id>
    client_id: str
    client_secret: str
    domain: str  # hosted UI base, https://<prefix>.auth.<region>.amazoncognito.com
    external_url: str  # https://app.example.com (what the browser sees)
    allowed_cidrs: list[str] = field(default_factory=list)
    session_hours: int = 8
    base_path: str = "/api/admin"

    @property
    def redirect_uri(self) -> str:
        return f"{self.external_url.rstrip('/')}{self.base_path}/oidc/callback"

    @property
    def session_key(self) -> str:
        # Derived from the client secret so one Secrets Manager secret is enough; rotating it signs everyone out.
        return hmac.new(
            self.client_secret.encode(), b"tsp-admin-session-v1", hashlib.sha256
        ).hexdigest()


def session_middleware(settings: AdminSettings) -> Middleware:
    # SameSite=Lax (not Strict): the browser returns from Cognito on a cross-site top-level GET,
    # which Strict would strip the cookie from. State-changing forms carry a CSRF token as well.
    return Middleware(
        SessionMiddleware,
        secret_key=settings.session_key,
        session_cookie=COOKIE,
        max_age=settings.session_hours * 3600,
        same_site="lax",
        https_only=True,
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _ip_allowed(settings: AdminSettings, request: Request) -> bool:
    if not settings.allowed_cidrs:
        return True
    raw = client_ip(request)
    try:
        ip = ipaddress.ip_address(raw or "")
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(c, strict=False) for c in settings.allowed_cidrs)


class AdminAuth(AuthenticationBackend):
    def __init__(
        self,
        settings: AdminSettings,
        verifier: CognitoVerifier,
        http: httpx.AsyncClient | None = None,
    ):
        super().__init__(secret_key=settings.session_key)
        self.middlewares = [session_middleware(settings)]
        self.settings = settings
        self.verifier = verifier
        self.http = http or httpx.AsyncClient(timeout=10)

    def _start(self) -> str:
        return f"{self.settings.base_path}/oidc/start"

    async def authenticate(self, request: Request) -> Response | bool:
        if not _ip_allowed(self.settings, request):
            return PlainTextResponse("forbidden", status_code=403)
        staff = request.session.get("staff")
        if not staff or staff.get("exp", 0) < time.time() or not staff.get("role"):
            request.session.pop("staff", None)
            return RedirectResponse(self._start(), status_code=303)
        return True

    async def get_user_id(self, request: Request) -> Any:
        staff = request.session.get("staff")
        return staff["sub"] if staff else None

    async def login(self, request: Request) -> Response | bool:
        return RedirectResponse(self._start(), status_code=303)

    async def logout(self, request: Request) -> Response | bool:
        request.session.clear()
        q = urlencode(
            {
                "client_id": self.settings.client_id,
                "logout_uri": f"{self.settings.external_url}{self.settings.base_path}/",
            }
        )
        return RedirectResponse(f"{self.settings.domain}/logout?{q}", status_code=303)

    # -- OIDC endpoints (mounted at {base}/oidc, before the admin app) ----------------------------

    async def start(self, request: Request) -> Response:
        if not _ip_allowed(self.settings, request):
            return PlainTextResponse("forbidden", status_code=403)
        state, nonce, verifier = (secrets.token_urlsafe(24) for _ in range(3))
        request.session["oidc"] = {"state": state, "nonce": nonce, "verifier": verifier}
        q = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
                "code_challenge_method": "S256",
            }
        )
        return RedirectResponse(f"{self.settings.domain}/oauth2/authorize?{q}", status_code=303)

    async def callback(self, request: Request) -> Response:
        if not _ip_allowed(self.settings, request):
            return PlainTextResponse("forbidden", status_code=403)
        pending = request.session.pop("oidc", None)
        params = request.query_params
        if (
            not pending
            or "error" in params
            or not hmac.compare_digest(str(params.get("state", "")), pending["state"])
            or not params.get("code")
        ):
            return PlainTextResponse("sign-in failed; start again", status_code=400)
        resp = await self.http.post(
            f"{self.settings.domain}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": params["code"],
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "code_verifier": pending["verifier"],
            },
            auth=(self.settings.client_id, self.settings.client_secret),
        )
        if resp.status_code != 200:
            return PlainTextResponse("sign-in failed; start again", status_code=400)
        try:
            claims = await self.verifier.verify(resp.json()["id_token"])
        except (AuthError, KeyError):
            return PlainTextResponse("sign-in failed; start again", status_code=400)
        if claims.token_use != "id" or claims.raw.get("nonce") != pending["nonce"]:
            return PlainTextResponse("sign-in failed; start again", status_code=400)
        role = role_for_groups(claims.groups)
        if role is None:
            return PlainTextResponse("this account has no staff role", status_code=403)
        request.session["staff"] = {
            "sub": claims.sub,
            "email": claims.email,
            "role": role,
            "csrf": secrets.token_urlsafe(24),
            "exp": time.time() + self.settings.session_hours * 3600,
        }
        return RedirectResponse(f"{self.settings.base_path}/", status_code=303)

    def oidc_app(self) -> Starlette:
        return Starlette(
            routes=[Route("/start", self.start), Route("/callback", self.callback)],
            middleware=[session_middleware(self.settings)],
        )
