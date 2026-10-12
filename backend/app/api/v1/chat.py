"""Authenticated HTTP entry point for the LangGraph patient assistant."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.agents.appointment_classifier import (
    AppointmentClassifier,
    OpenAIAppointmentClassifier,
)
from app.agents.classifier import ChatClassifier, OpenAIChatClassifier
from app.agents.patient_graph import build_patient_chat_graph
from app.core.config import Settings
from app.core.errors import AUTHENTICATION_ERROR_RESPONSE
from app.deps import (
    AppointmentRepositoryDependency,
    ChatHistoryRepositoryDependency,
    CurrentUserDependency,
    UserSupabaseClientDependency,
    get_request_settings,
)
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_turn_service import (
    complete_chat_turn,
    fail_chat_turn,
    start_chat_turn,
)

router = APIRouter(prefix="/chat", tags=["Chat"])


def get_chat_classifier(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ChatClassifier:
    """Create the classifier lazily and reuse it for later requests."""

    classifier = getattr(request.app.state, "chat_classifier", None)
    if classifier is None:
        classifier = OpenAIChatClassifier(settings)
        request.app.state.chat_classifier = classifier
    return classifier


def get_appointment_classifier(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AppointmentClassifier:
    """Lazily create the dedicated appointment specialist model."""

    classifier = getattr(request.app.state, "appointment_classifier", None)
    if classifier is None:
        classifier = OpenAIAppointmentClassifier(settings)
        request.app.state.appointment_classifier = classifier
    return classifier


@router.post(
    "",
    response_model=ChatResponse,
    responses={
        **AUTHENTICATION_ERROR_RESPONSE,
        400: {"description": "The model suggestion or pending action was invalid."},
        404: {"description": "The conversation session was not found."},
        409: {"description": "The session is closed or this client message was already used."},
        422: {"description": "The request or proposed value failed validation."},
        500: {"description": "Patient data did not match the application contract."},
        503: {"description": "The chat model or patient service was unavailable."},
    },
    summary="Chat with the patient orchestrator",
    description=(
        "Routes profile and appointment requests through the appropriate LangGraph nodes. "
        "Each turn is attached to an active persistent session. Any mutation is executed "
        "only by a later explicit, one-time confirmation request."
    ),
)
def patient_chat(
    body: ChatRequest,
    request: Request,
    current_user: CurrentUserDependency,
    patient_client: UserSupabaseClientDependency,
    history_repository: ChatHistoryRepositoryDependency,
    appointment_repository: AppointmentRepositoryDependency,
    classifier: Annotated[ChatClassifier, Depends(get_chat_classifier)],
    appointment_classifier: Annotated[AppointmentClassifier, Depends(get_appointment_classifier)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ChatResponse:
    """Persist, run and complete one observable graph turn."""

    # Request IDs are always normalized to UUIDs by RequestLoggingMiddleware
    # and become the unique correlation key in chat_runs.
    started_turn = start_chat_turn(
        history_repository,
        current_user.id,
        UUID(request.state.request_id),
        body,
        model_name=settings.chat_model,
        prompt_version=settings.chat_prompt_version,
        context_limit=settings.chat_context_message_limit,
    )

    graph = build_patient_chat_graph(
        classifier=classifier,
        appointment_classifier=appointment_classifier,
        appointment_repository=appointment_repository,
        current_user=current_user,
        patient_client=patient_client,
        history_repository=history_repository,
        pending_action_ttl_seconds=settings.chat_pending_action_ttl_seconds,
        context=started_turn.context,
        memory=started_turn.memory,
    )
    try:
        result = graph.invoke({"request": body})
        return complete_chat_turn(
            history_repository,
            current_user.id,
            started_turn,
            result["response"],
            result.get("decision"),
            result.get("traces", []),
            result.get("pending_action"),
        )
    except Exception as exc:
        # Only the exception class category is stored; exception messages and
        # provider/database bodies can contain sensitive values.
        fail_chat_turn(
            history_repository,
            current_user.id,
            body.session_id,
            started_turn.run_id,
            started_at=started_turn.started_at,
            error_code=type(exc).__name__,
        )
        raise
