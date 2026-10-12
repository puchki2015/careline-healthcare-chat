"""Authenticated administrator chat endpoint for patient invitations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.agents.admin_classifier import AdminChatClassifier, OpenAIAdminChatClassifier
from app.agents.admin_graph import build_admin_chat_graph
from app.core.config import Settings
from app.deps import (
    AdminChatRepositoryDependency,
    CurrentAdminDependency,
    get_request_settings,
)
from app.schemas.admin import AdminChatRequest, AdminChatResponse
from app.services.admin_chat_service import (
    complete_admin_turn,
    fail_admin_turn,
    start_admin_turn,
)

router = APIRouter(prefix="/admin/chat", tags=["Admin chat"])


def get_admin_chat_classifier(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdminChatClassifier:
    classifier = getattr(request.app.state, "admin_chat_classifier", None)
    if classifier is None:
        classifier = OpenAIAdminChatClassifier(settings)
        request.app.state.admin_chat_classifier = classifier
    return classifier


@router.post(
    "",
    response_model=AdminChatResponse,
    responses={
        400: {"description": "The intent, invitation data or confirmation was invalid."},
        401: {"description": "A valid Supabase access token is required."},
        403: {"description": "An active administrator profile is required."},
        404: {"description": "The administrator chat session was not found."},
        409: {"description": "The session/action conflicted or the email already exists."},
        422: {"description": "The request or patient invitation data was invalid."},
        503: {"description": "The model, database or invitation provider was unavailable."},
    },
    summary="Invite a patient through the administrator orchestrator",
)
def administrator_chat(
    body: AdminChatRequest,
    request: Request,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    classifier: Annotated[AdminChatClassifier, Depends(get_admin_chat_classifier)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdminChatResponse:
    """Persist and execute one role-gated administrator LangGraph turn."""

    turn, memory = start_admin_turn(
        repository,
        current_admin.id,
        UUID(request.state.request_id),
        body,
        model_name=settings.chat_model,
        prompt_version=settings.admin_chat_prompt_version,
        context_limit=settings.chat_context_message_limit,
    )
    graph = build_admin_chat_graph(
        classifier=classifier,
        current_admin=current_admin,
        repository=repository,
        invitation_redirect_url=settings.patient_invitation_redirect_url_value,
        pending_action_ttl_seconds=settings.chat_pending_action_ttl_seconds,
        context=turn.context,
        memory=memory,
    )
    try:
        result = graph.invoke({"request": body})
        return complete_admin_turn(
            repository,
            current_admin.id,
            turn,
            result["response"],
            result.get("decision"),
            result.get("traces", []),
            result.get("pending_action"),
        )
    except Exception as exc:
        fail_admin_turn(
            repository,
            current_admin.id,
            body.session_id,
            turn.run_id,
            started_at=turn.started_at,
            error_code=type(exc).__name__,
        )
        raise
