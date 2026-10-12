import os
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from tsp.auth import Claims
from tsp.server import Settings, create_app, current_user, require_groups

ISS = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_T"
SECRET = "s" * 32


def base(**kw) -> Settings:
    return Settings(origin_verify=SECRET, **kw)


def edge(extra=None):
    return {"X-Origin-Verify": SECRET, **(extra or {})}


def test_health_is_exempt_from_origin_check_but_everything_else_is_not():
    with TestClient(create_app(base())) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        r = c.get("/api/ready")
        assert r.status_code == 403 and r.json()["error"]["type"] == "forbidden"
        assert c.get("/api/ready", headers={"X-Origin-Verify": "wrong"}).status_code == 403
        assert c.get("/api/ready", headers=edge()).status_code == 200


def test_no_origin_secret_means_open():
    with TestClient(create_app(Settings())) as c:
        assert c.get("/api/ready").status_code == 200


def test_request_id_header_and_error_envelope():
    app = create_app(Settings())
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/nope", headers={"x-request-id": "abc"})
        assert r.status_code == 404 and r.json()["error"]["type"] == "http_error"
        assert r.headers["x-request-id"] == "abc"

        @app.get("/api/boom")
        async def boom():
            raise RuntimeError("secret detail")

        r = c.get("/api/boom")
        assert r.status_code == 500 and "secret detail" not in r.text


def test_validation_error_envelope():
    app = create_app(Settings())

    @app.get("/api/n")
    async def n(x: int):
        return {"x": x}

    with TestClient(app) as c:
        r = c.get("/api/n?x=abc")
        assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"


@pytest.fixture
def authed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {
        **jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
        "kid": "k",
        "alg": "RS256",
    }
    app = create_app(Settings(auth_issuer=ISS, auth_client_ids=["cid"]))

    @app.get("/api/me")
    async def me(user: Claims = Depends(current_user)):
        return {"sub": user.sub}

    @app.get("/api/admin")
    async def admin(user: Claims = Depends(require_groups("admin"))):
        return {"sub": user.sub}

    def tok(groups):
        now = int(time.time())
        return jwt.encode(
            {
                "sub": "u1",
                "iss": ISS,
                "exp": now + 300,
                "token_use": "access",
                "client_id": "cid",
                "cognito:groups": groups,
            },
            key,
            algorithm="RS256",
            headers={"kid": "k"},
        )

    with TestClient(app) as c:
        c.app.state.verifier._http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"keys": [jwk]}))
        )
        yield c, tok


def test_auth_dependency(authed):
    c, tok = authed
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    r = c.get("/api/me", headers={"Authorization": f"Bearer {tok([])}"})
    assert r.status_code == 200 and r.json() == {"sub": "u1"}


def test_group_check(authed):
    c, tok = authed
    assert (
        c.get("/api/admin", headers={"Authorization": f"Bearer {tok(['user'])}"}).status_code == 403
    )
    assert (
        c.get("/api/admin", headers={"Authorization": f"Bearer {tok(['admin'])}"}).status_code
        == 200
    )


def test_auth_not_configured_is_503():
    app = create_app(Settings())

    @app.get("/api/me")
    async def me(user: Claims = Depends(current_user)):
        return {}

    with TestClient(app) as c:
        assert c.get("/api/me", headers={"Authorization": "Bearer x"}).status_code == 503


def test_ready_checks_real_dependencies():
    s = Settings(
        database_url=os.environ.get(
            "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
        ),
        cache_url=os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0"),
    )
    with TestClient(create_app(s)) as c:
        r = c.get("/api/ready")
        if r.status_code != 200:
            pytest.skip(f"local services not running: {r.json()}")
        assert r.json()["checks"] == {"db": "ok", "cache": "ok"}
    bad = Settings(cache_url="redis://127.0.0.1:1/0")
    with TestClient(create_app(bad)) as c:
        r = c.get("/api/ready")
        assert r.status_code == 503 and r.json()["checks"]["cache"] == "fail"


def test_settings_from_env():
    s = Settings.from_env(
        {
            "TSP_ENV": "prod",
            "AUTH_CLIENT_IDS": "a, b",
            "CORS_ORIGINS": "https://x.com",
            "DATABASE_URL": "",
        }
    )
    assert (
        s.env == "prod" and s.auth_client_ids == ["a", "b"] and s.cors_origins == ["https://x.com"]
    )
    assert s.database_url is None


def test_database_url_built_from_injected_parts():
    s = Settings.from_env(
        {"DB_HOST": "10.0.0.5", "DB_USER": "app", "DB_PASSWORD": "p@ss/w:rd", "DB_NAME": "db"}
    )
    assert s.database_url == "postgresql://app:p%40ss%2Fw%3Ard@10.0.0.5:5432/db"
    assert (
        Settings.from_env({"DATABASE_URL": "postgresql://x", "DB_HOST": "h"}).database_url
        == "postgresql://x"
    )
    assert Settings.from_env({"DB_HOST": "h"}).database_url is None
