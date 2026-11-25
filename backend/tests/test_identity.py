import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from recruiting.identity import AuthenticationError, OidcAuthenticator

ISSUER = "https://login.example.com/tenant"
AUDIENCE = "recruiting-platform"


@pytest.fixture(scope="module")
def keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def make_token(private_key, **overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 300,
        "email": "bd@cbs.example",
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def authenticator(public_key):
    return OidcAuthenticator(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _t: public_key)


def bearer(token):
    return {"authorization": f"Bearer {token}"}


def test_valid_token_returns_email(keys):
    private_key, public_key = keys
    assert authenticator(public_key).identify(bearer(make_token(private_key))) == "bd@cbs.example"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-app"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 60},
        {"email": None},
    ],
)
def test_invalid_tokens_rejected(keys, overrides):
    private_key, public_key = keys
    with pytest.raises(AuthenticationError):
        authenticator(public_key).identify(bearer(make_token(private_key, **overrides)))


def test_token_signed_by_another_key_rejected(keys):
    _, public_key = keys
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        authenticator(public_key).identify(bearer(make_token(other)))


def test_missing_bearer_token(keys):
    _, public_key = keys
    with pytest.raises(AuthenticationError):
        authenticator(public_key).identify({})


@pytest.mark.parametrize("verified", [False, "false"])
def test_email_the_provider_has_not_verified_is_rejected(keys, verified):
    # Someone could otherwise put a staff member's address on an unverified account.
    private_key, public_key = keys
    token = make_token(private_key, email_verified=verified)
    with pytest.raises(AuthenticationError, match="not verified"):
        authenticator(public_key).identify(bearer(token))
    ok = make_token(private_key, email_verified=True)
    assert authenticator(public_key).identify(bearer(ok)) == "bd@cbs.example"
