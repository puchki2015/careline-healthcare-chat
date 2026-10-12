"""Unit tests for strict Supabase JWT verification."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientError

from app.core.config import Settings
from app.core.security import AuthenticationError, verify_access_token


class FakeJwksClient:
    """Small deterministic replacement for network-backed PyJWKClient."""

    def __init__(self, public_key: object) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
        return SimpleNamespace(key=self.public_key)


class FailingJwksClient:
    def get_signing_key_from_jwt(self, _: str) -> None:
        raise PyJWKClientError("simulated JWKS failure")


@pytest.fixture(scope="module")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def build_token(
    settings: Settings,
    private_key: rsa.RSAPrivateKey,
    **claim_overrides: object,
) -> tuple[str, str]:
    """Create a signed token and return it with its expected user ID."""

    user_id = str(uuid4())
    claims: dict[str, object] = {
        "sub": user_id,
        "aud": "authenticated",
        "iss": settings.supabase_issuer,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    return token, user_id


def test_valid_token_returns_protected_identity(
    test_settings: Settings,
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    token, user_id = build_token(test_settings, rsa_private_key)

    current_user = verify_access_token(
        token,
        test_settings,
        jwks_client=FakeJwksClient(rsa_private_key.public_key()),  # type: ignore[arg-type]
    )

    assert str(current_user.id) == user_id
    assert current_user.access_token.get_secret_value() == token
    assert token not in repr(current_user)


@pytest.mark.parametrize(
    ("claim_overrides", "expected_reason"),
    [
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, "expired"),
        ({"aud": "wrong-audience"}, "invalid_audience"),
        ({"iss": "https://wrong-project.example/auth/v1"}, "invalid_issuer"),
        ({"sub": "not-a-uuid"}, "invalid_subject"),
    ],
)
def test_invalid_claims_are_rejected(
    test_settings: Settings,
    rsa_private_key: rsa.RSAPrivateKey,
    claim_overrides: dict[str, object],
    expected_reason: str,
) -> None:
    token, _ = build_token(test_settings, rsa_private_key, **claim_overrides)

    with pytest.raises(AuthenticationError, match=expected_reason):
        verify_access_token(
            token,
            test_settings,
            jwks_client=FakeJwksClient(rsa_private_key.public_key()),  # type: ignore[arg-type]
        )


def test_missing_required_claim_is_rejected(
    test_settings: Settings,
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    claims = {
        "sub": str(uuid4()),
        "aud": "authenticated",
        "iss": test_settings.supabase_issuer,
        # exp is intentionally absent.
    }
    token = jwt.encode(claims, rsa_private_key, algorithm="RS256", headers={"kid": "test-key"})

    with pytest.raises(AuthenticationError, match="missing_claim"):
        verify_access_token(
            token,
            test_settings,
            jwks_client=FakeJwksClient(rsa_private_key.public_key()),  # type: ignore[arg-type]
        )


def test_bad_signature_is_rejected(
    test_settings: Settings,
    rsa_private_key: rsa.RSAPrivateKey,
) -> None:
    token, _ = build_token(test_settings, rsa_private_key)
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(AuthenticationError, match="invalid_token"):
        verify_access_token(
            token,
            test_settings,
            jwks_client=FakeJwksClient(unrelated_key.public_key()),  # type: ignore[arg-type]
        )


def test_disallowed_algorithm_is_rejected(test_settings: Settings) -> None:
    hmac_test_secret = "test-secret-that-is-at-least-32-bytes-long"
    claims = {
        "sub": str(uuid4()),
        "aud": "authenticated",
        "iss": test_settings.supabase_issuer,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(claims, hmac_test_secret, algorithm="HS256")

    with pytest.raises(AuthenticationError, match="invalid_token"):
        verify_access_token(
            token,
            test_settings,
            jwks_client=FakeJwksClient(hmac_test_secret),  # type: ignore[arg-type]
        )


def test_jwks_failure_fails_closed(test_settings: Settings) -> None:
    with pytest.raises(AuthenticationError, match="signing_key_unavailable"):
        verify_access_token(
            "header.payload.signature",
            test_settings,
            jwks_client=FailingJwksClient(),  # type: ignore[arg-type]
        )
