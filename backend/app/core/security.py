"""Supabase access-token verification using asymmetric JWKS signing keys.

This module is the only place that interprets JWTs. Centralizing verification
prevents routes from accidentally applying different security rules.
"""

import logging
from functools import lru_cache
from typing import Literal
from uuid import UUID

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWKClientError,
)

from app.core.config import Settings
from app.schemas.auth import CurrentUser

logger = logging.getLogger(__name__)

# Never derive accepted algorithms from the token header. The allow-list blocks
# algorithm-confusion attacks and rejects unsigned ``alg=none`` tokens.
ALLOWED_JWT_ALGORITHMS = ("ES256", "RS256")
SUPABASE_JWT_AUDIENCE = "authenticated"
REQUIRED_JWT_CLAIMS = ("exp", "sub", "aud", "iss")

AuthFailureReason = Literal[
    "expired",
    "not_yet_valid",
    "invalid_audience",
    "invalid_issuer",
    "missing_claim",
    "signing_key_unavailable",
    "invalid_token",
    "invalid_subject",
    "verification_unavailable",
]


class AuthenticationError(Exception):
    """Internal authentication failure with a safe, non-token reason code.

    The reason is useful for server logs, but API clients intentionally receive
    one generic 401 message so they cannot probe verification internals.
    """

    def __init__(self, reason: AuthFailureReason) -> None:
        super().__init__(reason)
        self.reason = reason


@lru_cache(maxsize=8)
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """Create one cached JWKS client per Supabase project URL.

    PyJWT caches the fetched key set for five minutes and individual signing
    keys after use. This avoids a network request on every authenticated API
    call while still allowing normal Supabase signing-key rotation.
    """

    return PyJWKClient(
        jwks_url,
        cache_keys=True,
        max_cached_keys=16,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
        cooldown_duration=30,
    )


def verify_access_token(
    token: str,
    settings: Settings,
    *,
    jwks_client: PyJWKClient | None = None,
) -> CurrentUser:
    """Verify a Supabase JWT and return its trusted user identity.

    Signature, algorithm, expiration, audience, issuer, and required claims are
    validated before ``sub`` is accepted. The raw token is never logged and is
    immediately wrapped in SecretStr in the returned model.
    """

    client = jwks_client or get_jwks_client(settings.supabase_jwks_url)

    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_JWT_ALGORITHMS,
            audience=SUPABASE_JWT_AUDIENCE,
            issuer=settings.supabase_issuer,
            options={"require": list(REQUIRED_JWT_CLAIMS)},
        )
    except ExpiredSignatureError as exc:
        raise AuthenticationError("expired") from exc
    except ImmatureSignatureError as exc:
        raise AuthenticationError("not_yet_valid") from exc
    except InvalidAudienceError as exc:
        raise AuthenticationError("invalid_audience") from exc
    except InvalidIssuerError as exc:
        raise AuthenticationError("invalid_issuer") from exc
    except MissingRequiredClaimError as exc:
        raise AuthenticationError("missing_claim") from exc
    except PyJWKClientError as exc:
        # This covers missing/unknown key IDs and temporary JWKS fetch errors.
        raise AuthenticationError("signing_key_unavailable") from exc
    except InvalidTokenError as exc:
        # Malformed tokens, bad signatures, and disallowed algorithms all map
        # to the same external 401 response.
        raise AuthenticationError("invalid_token") from exc
    except Exception as exc:
        # Network/library failures must fail closed. Only the exception type is
        # logged; messages may contain URLs or lower-level response details.
        logger.error(
            "jwt_verification_unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise AuthenticationError("verification_unavailable") from exc

    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("invalid_subject") from exc

    logger.debug("jwt_verification_succeeded")
    return CurrentUser(id=user_id, access_token=token)
