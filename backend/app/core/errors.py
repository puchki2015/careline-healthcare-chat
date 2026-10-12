"""Central API exception handlers and stable client-facing error contracts."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agents.admin_graph import AdminChatUnderstandingError
from app.agents.admin_pending_actions import AdminPendingActionNotFoundError
from app.agents.appointment_graph import AppointmentUnderstandingError
from app.agents.classifier import ChatConfigurationError, ChatModelUnavailableError
from app.agents.patient_graph import ChatUnderstandingError
from app.agents.pending_actions import PendingActionNotFoundError
from app.core.security import AuthenticationError
from app.schemas.patient import (
    EDITABLE_KEY_ORDER,
    READABLE_FIELD_ORDER,
    UnsupportedPatientAttributeError,
)
from app.services.admin_service import (
    AdminAuthorizationError,
    PatientInvitationConflictError,
    PatientInvitationServiceError,
)
from app.services.appointment_service import (
    AppointmentConflictError,
    AppointmentDataIntegrityError,
    AppointmentServiceUnavailableError,
    AppointmentValidationError,
)
from app.services.chat_history_service import (
    ChatHistoryCursorError,
    ChatHistoryDataIntegrityError,
    ChatHistoryServiceUnavailableError,
    ChatSessionNotFoundError,
    ChatSessionStateError,
)
from app.services.chat_turn_service import ChatTurnAlreadyProcessedError
from app.services.patient_service import (
    InvalidPatientFieldsError,
    PatientAttributeValidationError,
    PatientDataIntegrityError,
    PatientNotFoundError,
    PatientServiceUnavailableError,
)

logger = logging.getLogger(__name__)


def _error_body(*, code: str, message: str, request_id: str) -> dict[str, dict[str, str]]:
    """Build the error envelope used consistently by Careline endpoints."""

    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }


async def authentication_error_handler(
    request: Request,
    _: AuthenticationError,
) -> JSONResponse:
    """Return a generic 401 without exposing why token verification failed."""

    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=_error_body(
            code="authentication_required",
            message="A valid bearer access token is required.",
            request_id=request_id,
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def invalid_patient_fields_handler(
    request: Request,
    _: InvalidPatientFieldsError,
) -> JSONResponse:
    """Return the readable whitelist without echoing untrusted field text."""

    request_id = getattr(request.state, "request_id", "-")
    allowed_fields = ", ".join(READABLE_FIELD_ORDER)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="invalid_fields",
            message=f"One or more requested fields are invalid. Allowed fields: {allowed_fields}.",
            request_id=request_id,
        ),
    )


async def patient_not_found_handler(
    request: Request,
    _: PatientNotFoundError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_error_body(
            code="patient_not_found",
            message="The patient profile was not found.",
            request_id=request_id,
        ),
    )


async def patient_service_unavailable_handler(
    request: Request,
    _: PatientServiceUnavailableError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="patient_service_unavailable",
            message="The patient service is temporarily unavailable.",
            request_id=request_id,
        ),
    )


async def patient_data_integrity_handler(
    request: Request,
    _: PatientDataIntegrityError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            code="invalid_patient_data",
            message="The stored patient profile could not be processed.",
            request_id=request_id,
        ),
    )


async def unsupported_patient_attribute_handler(
    request: Request,
    _: UnsupportedPatientAttributeError,
) -> JSONResponse:
    """Return the editable whitelist without echoing an untrusted path key."""

    request_id = getattr(request.state, "request_id", "-")
    editable_fields = ", ".join(EDITABLE_KEY_ORDER)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="attribute_not_editable",
            message=f"The requested attribute is not editable. Editable fields: {editable_fields}.",
            request_id=request_id,
        ),
    )


async def patient_attribute_validation_handler(
    request: Request,
    _: PatientAttributeValidationError,
) -> JSONResponse:
    """Return a value-free 422 for field-specific validation failures."""

    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_error_body(
            code="invalid_attribute_value",
            message="The supplied attribute value is invalid.",
            request_id=request_id,
        ),
    )


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return a generic 422 without reflecting request bodies or invalid values."""

    request_id = getattr(request.state, "request_id", "-")
    # Log only schema locations and stable error categories. The rejected input
    # itself may contain passwords, tokens or patient information and must not
    # be copied into application logs.
    issues = [
        {
            "location": ".".join(str(part) for part in error.get("loc", ())),
            "type": str(error.get("type", "unknown")),
        }
        for error in exc.errors()
    ]
    logger.warning(
        "request_validation_failed",
        extra={"validation_error_count": len(issues), "validation_issues": issues},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_error_body(
            code="request_validation_failed",
            message="The request structure or parameter format is invalid.",
            request_id=request_id,
        ),
    )


async def chat_bad_request_handler(
    request: Request,
    _: ChatUnderstandingError | PendingActionNotFoundError,
) -> JSONResponse:
    """Return one generic response for unsafe model output or stale actions."""

    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="chat_action_invalid",
            message="The chat request could not be completed. Please start the action again.",
            request_id=request_id,
        ),
    )


async def appointment_bad_request_handler(
    request: Request,
    _: AppointmentUnderstandingError | AppointmentValidationError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="appointment_request_invalid",
            message="Please provide valid appointment IDs and exact timezone-aware dates.",
            request_id=request_id,
        ),
    )


async def appointment_conflict_handler(
    request: Request, _: AppointmentConflictError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            code="appointment_conflict",
            message="That appointment or time slot is no longer available. Please search again.",
            request_id=request_id,
        ),
    )


async def appointment_unavailable_handler(
    request: Request, _: AppointmentServiceUnavailableError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="appointment_service_unavailable",
            message="The appointment service is temporarily unavailable.",
            request_id=request_id,
        ),
    )


async def appointment_integrity_handler(
    request: Request, _: AppointmentDataIntegrityError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            code="invalid_appointment_data",
            message="Stored appointment data could not be processed.",
            request_id=request_id,
        ),
    )


async def admin_chat_bad_request_handler(
    request: Request,
    _: AdminChatUnderstandingError | AdminPendingActionNotFoundError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="admin_chat_action_invalid",
            message="The administrator chat action is invalid. Please start it again.",
            request_id=request_id,
        ),
    )


async def admin_authorization_handler(
    request: Request,
    _: AdminAuthorizationError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=_error_body(
            code="admin_access_required",
            message="An active administrator account is required.",
            request_id=request_id,
        ),
    )


async def patient_invitation_conflict_handler(
    request: Request,
    _: PatientInvitationConflictError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            code="patient_invitation_conflict",
            message="The patient could not be invited with that email address.",
            request_id=request_id,
        ),
    )


async def patient_invitation_service_handler(
    request: Request,
    _: PatientInvitationServiceError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="patient_invitation_unavailable",
            message="The patient invitation service is temporarily unavailable.",
            request_id=request_id,
        ),
    )


async def chat_unavailable_handler(
    request: Request,
    _: ChatConfigurationError | ChatModelUnavailableError,
) -> JSONResponse:
    """Hide provider/configuration details behind a stable 503 response."""

    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="chat_service_unavailable",
            message="The chat service is temporarily unavailable.",
            request_id=request_id,
        ),
    )


async def chat_history_cursor_handler(
    request: Request,
    _: ChatHistoryCursorError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_error_body(
            code="invalid_history_cursor",
            message="The chat history cursor is invalid or expired.",
            request_id=request_id,
        ),
    )


async def chat_session_not_found_handler(
    request: Request,
    _: ChatSessionNotFoundError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_error_body(
            code="chat_session_not_found",
            message="The chat session was not found.",
            request_id=request_id,
        ),
    )


async def chat_session_state_handler(
    request: Request,
    _: ChatSessionStateError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            code="chat_session_state_conflict",
            message="The chat session is not in a state that allows this operation.",
            request_id=request_id,
        ),
    )


async def chat_history_unavailable_handler(
    request: Request,
    _: ChatHistoryServiceUnavailableError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="chat_history_unavailable",
            message="Chat history is temporarily unavailable.",
            request_id=request_id,
        ),
    )


async def chat_history_integrity_handler(
    request: Request,
    _: ChatHistoryDataIntegrityError,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            code="invalid_chat_history_data",
            message="Stored chat history could not be processed.",
            request_id=request_id,
        ),
    )


async def chat_turn_duplicate_handler(
    request: Request,
    _: ChatTurnAlreadyProcessedError,
) -> JSONResponse:
    """Reject a reused idempotency key without replaying sensitive output."""

    request_id = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            code="chat_turn_already_processed",
            message="This client message was already processed. Use a new ID for a new action.",
            request_id=request_id,
        ),
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Register handlers centrally so every router has the same behavior."""

    application.add_exception_handler(
        AppointmentUnderstandingError,
        appointment_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AppointmentValidationError,
        appointment_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AppointmentConflictError,
        appointment_conflict_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AppointmentServiceUnavailableError,
        appointment_unavailable_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AppointmentDataIntegrityError,
        appointment_integrity_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AuthenticationError,
        authentication_error_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AdminAuthorizationError,
        admin_authorization_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AdminChatUnderstandingError,
        admin_chat_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        AdminPendingActionNotFoundError,
        admin_chat_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientInvitationConflictError,
        patient_invitation_conflict_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientInvitationServiceError,
        patient_invitation_service_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        InvalidPatientFieldsError,
        invalid_patient_fields_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientNotFoundError,
        patient_not_found_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientServiceUnavailableError,
        patient_service_unavailable_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientDataIntegrityError,
        patient_data_integrity_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        UnsupportedPatientAttributeError,
        unsupported_patient_attribute_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PatientAttributeValidationError,
        patient_attribute_validation_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatUnderstandingError,
        chat_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        PendingActionNotFoundError,
        chat_bad_request_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatConfigurationError,
        chat_unavailable_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatModelUnavailableError,
        chat_unavailable_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatHistoryCursorError,
        chat_history_cursor_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatSessionNotFoundError,
        chat_session_not_found_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatSessionStateError,
        chat_session_state_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatHistoryServiceUnavailableError,
        chat_history_unavailable_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatHistoryDataIntegrityError,
        chat_history_integrity_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        ChatTurnAlreadyProcessedError,
        chat_turn_duplicate_handler,  # type: ignore[arg-type]
    )


AUTHENTICATION_ERROR_RESPONSE: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Missing or invalid Supabase access token.",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "authentication_required",
                        "message": "A valid bearer access token is required.",
                        "request_id": "7b777a5b-0d2a-464a-95c5-e37acbd57c00",
                    }
                }
            }
        },
    }
}

PATIENT_GET_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTHENTICATION_ERROR_RESPONSE,
    400: {"description": "The field selection contains an unknown or empty name."},
    404: {"description": "No patient profile is visible for the authenticated user."},
    500: {"description": "Stored patient data does not match the API contract."},
    503: {"description": "Supabase could not complete the patient query."},
}

PATIENT_UPDATE_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTHENTICATION_ERROR_RESPONSE,
    400: {"description": "The requested attribute is not editable."},
    404: {"description": "No patient profile is visible for the authenticated user."},
    422: {"description": "The request body or attribute value is invalid."},
    500: {"description": "Updated patient data does not match the API contract."},
    503: {"description": "Supabase could not complete the patient update."},
}

CHAT_HISTORY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTHENTICATION_ERROR_RESPONSE,
    400: {"description": "The pagination cursor is invalid."},
    404: {"description": "The session does not belong to the authenticated patient."},
    409: {"description": "The session state does not permit the operation."},
    422: {"description": "The request structure or parameter format is invalid."},
    500: {"description": "Stored chat history violates the API contract."},
    503: {"description": "Supabase could not complete the history operation."},
}
