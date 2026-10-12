"""Authenticated conversation-session and history endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.agents.summarizer import ChatSummarizer, OpenAIChatSummarizer
from app.core.config import Settings
from app.core.errors import CHAT_HISTORY_ERROR_RESPONSES
from app.deps import (
    ChatHistoryRepositoryDependency,
    CurrentUserDependency,
    get_request_settings,
)
from app.schemas.chat_history import (
    ChatMessagePage,
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionPage,
    ChatSessionUpdate,
)
from app.services.chat_history_service import (
    close_chat_session,
    create_chat_session,
    list_chat_messages,
    list_chat_sessions,
    rename_chat_session,
    set_chat_session_archived,
)

router = APIRouter(prefix="/chat/sessions", tags=["Chat sessions"])


@router.patch(
    "/{session_id}",
    response_model=ChatSessionOut,
    responses=CHAT_HISTORY_ERROR_RESPONSES,
    summary="Rename one owned chat session",
)
def update_chat_session(
    session_id: UUID,
    body: ChatSessionUpdate,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
) -> ChatSessionOut:
    return rename_chat_session(repository, current_user.id, session_id, body)


@router.post("/{session_id}/archive", response_model=ChatSessionOut)
def archive_chat_session(
    session_id: UUID,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
) -> ChatSessionOut:
    return set_chat_session_archived(repository, current_user.id, session_id, archived=True)


@router.post("/{session_id}/restore", response_model=ChatSessionOut)
def restore_chat_session(
    session_id: UUID,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
) -> ChatSessionOut:
    return set_chat_session_archived(repository, current_user.id, session_id, archived=False)


def get_chat_summarizer(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ChatSummarizer:
    """Create the summary model lazily and reuse it across close requests."""

    summarizer = getattr(request.app.state, "chat_summarizer", None)
    if summarizer is None:
        summarizer = OpenAIChatSummarizer(settings)
        request.app.state.chat_summarizer = summarizer
    return summarizer


@router.post(
    "",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
    responses=CHAT_HISTORY_ERROR_RESPONSES,
    summary="Start a new patient chat session",
)
def start_chat_session(
    body: ChatSessionCreate,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ChatSessionOut:
    """Create a server-owned conversation for the authenticated patient."""

    return create_chat_session(
        repository,
        current_user.id,
        body,
        model_name=settings.chat_model,
        prompt_version=settings.chat_prompt_version,
        prior_session_limit=settings.chat_prior_session_limit,
    )


@router.get(
    "",
    response_model=ChatSessionPage,
    responses=CHAT_HISTORY_ERROR_RESPONSES,
    summary="List the authenticated patient's recent chat sessions",
)
def get_chat_sessions(
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
) -> ChatSessionPage:
    """Return a bounded recent-first page protected by patient RLS."""

    return list_chat_sessions(
        repository,
        current_user.id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/{session_id}/messages",
    response_model=ChatMessagePage,
    responses=CHAT_HISTORY_ERROR_RESPONSES,
    summary="Load ordered messages for one owned chat session",
)
def get_chat_session_messages(
    session_id: UUID,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    newest_first: Annotated[bool, Query()] = False,
) -> ChatMessagePage:
    """Verify session ownership before returning its patient-visible messages."""

    return list_chat_messages(
        repository,
        current_user.id,
        session_id,
        limit=limit,
        cursor=cursor,
        newest_first=newest_first,
    )


@router.post(
    "/{session_id}/close",
    response_model=ChatSessionOut,
    responses=CHAT_HISTORY_ERROR_RESPONSES,
    summary="Close one owned chat session",
)
def end_chat_session(
    session_id: UUID,
    current_user: CurrentUserDependency,
    repository: ChatHistoryRepositoryDependency,
    summarizer: Annotated[ChatSummarizer, Depends(get_chat_summarizer)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ChatSessionOut:
    """Summarize and close an active session; repeated closes are idempotent."""

    return close_chat_session(
        repository,
        current_user.id,
        session_id,
        summarizer=summarizer,
        summary_message_limit=settings.chat_summary_message_limit,
        summary_max_chars=settings.chat_summary_max_chars,
    )
