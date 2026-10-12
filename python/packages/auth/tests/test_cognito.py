import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from tsp.auth import AuthError, CognitoVerifier

ISS = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TEST"
CLIENT = "client123"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(key, kid):
    d = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**d, "kid": kid, "alg": "RS256", "use": "sig"}


def token(key, kid="k1", **over):
    now = int(time.time())
    claims = {
        "sub": "u1",
        "iss": ISS,
        "exp": now + 300,
        "iat": now,
        "token_use": "access",
        "client_id": CLIENT,
        "username": "ann",
        "cognito:groups": ["admin"],
        "scope": "openid email",
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class Jwks:
    def __init__(self, *keys):
        self.keys, self.calls = list(keys), 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert str(request.url) == f"{ISS}/.well-known/jwks.json"
        return httpx.Response(200, json={"keys": self.keys})


def verifier(jwks):
    return CognitoVerifier(
        ISS, [CLIENT], http=httpx.AsyncClient(transport=httpx.MockTransport(jwks.handler))
    )


async def test_valid_access_token():
    key = make_key()
    v = verifier(Jwks(jwk_for(key, "k1")))
    c = await v.verify(token(key))
    assert (c.sub, c.username, c.groups, c.scope) == ("u1", "ann", ["admin"], ["openid", "email"])


async def test_valid_id_token_uses_aud():
    key = make_key()
    v = verifier(Jwks(jwk_for(key, "k1")))
    c = await v.verify(token(key, token_use="id", client_id=None, aud=CLIENT, email="a@b.c"))
    assert c.token_use == "id" and c.email == "a@b.c"


@pytest.mark.parametrize(
    "over,msg",
    [
        ({"exp": int(time.time()) - 600}, "expired"),
        ({"iss": "https://evil.example/pool"}, "invalid"),
        ({"client_id": "other"}, "different app"),
        ({"token_use": "refresh"}, "token type"),
    ],
)
async def test_rejected(over, msg):
    key = make_key()
    v = verifier(Jwks(jwk_for(key, "k1")))
    with pytest.raises(AuthError, match=msg):
        await v.verify(token(key, **over))


async def test_wrong_signature_and_garbage_rejected():
    good, evil = make_key(), make_key()
    v = verifier(Jwks(jwk_for(good, "k1")))
    with pytest.raises(AuthError, match="invalid"):
        await v.verify(token(evil, kid="k1"))
    with pytest.raises(AuthError, match="malformed"):
        await v.verify("not.a.jwt")


async def test_alg_none_rejected():
    key = make_key()
    v = verifier(Jwks(jwk_for(key, "k1")))
    forged = jwt.encode(
        {"sub": "u1", "iss": ISS, "exp": int(time.time()) + 99},
        None,
        algorithm="none",
        headers={"kid": "k1"},
    )
    with pytest.raises(AuthError):
        await v.verify(forged)


async def test_keys_cached_and_unknown_kid_refreshes_once():
    k1, k2 = make_key(), make_key()
    jwks = Jwks(jwk_for(k1, "k1"))
    v = verifier(jwks)
    await v.verify(token(k1))
    await v.verify(token(k1))
    assert jwks.calls == 1
    jwks.keys.append(jwk_for(k2, "k2"))
    v._fetched_at -= 120  # pretend the last fetch was a while ago
    await v.verify(token(k2, kid="k2"))
    assert jwks.calls == 2
    with pytest.raises(
        AuthError, match="unknown"
    ):  # forged kid right after a refresh: no new fetch
        await v.verify(token(k2, kid="zzz"))
    assert jwks.calls == 2
