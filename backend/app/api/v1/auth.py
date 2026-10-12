"""Authenticated diagnostic endpoint for verifying a Supabase session."""

from fastapi import APIRouter

from app.core.errors import AUTHENTICATION_ERROR_RESPONSE
from app.deps import CurrentUserDependency
from app.schemas.auth import AuthVerificationResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get(
    "/verify",
    response_model=AuthVerificationResponse,
    responses=AUTHENTICATION_ERROR_RESPONSE,
    summary="Verify the current access token",
    description="Returns success only after the supplied Supabase access token is fully verified.",
)
def verify_authentication(_: CurrentUserDependency) -> AuthVerificationResponse:
    """Prove authentication without returning identity or token details."""

    return AuthVerificationResponse(authenticated=True)
