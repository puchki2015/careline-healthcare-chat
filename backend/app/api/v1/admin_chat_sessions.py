"""Administrator-owned invitation conversation lifecycle endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.agents.admin_summarizer import AdminChatSummarizer, OpenAIAdminChatSummarizer
from app.core.config import Settings
from app.deps import (
    AdminChatRepositoryDependency,
    CurrentAdminDependency,
    get_request_settings,
)
from app.schemas.admin import AdminChatSessionCreate, AdminChatSessionOut, AdminChatSessionPage
from app.schemas.chat_history import ChatMessagePage, ChatSessionUpdate
from app.services.admin_chat_service import (
    close_admin_session,
    create_admin_session,
    list_admin_messages,
    list_admin_sessions,
    rename_admin_session,
    set_admin_session_archived,
)

router = APIRouter(prefix="/admin/chat/sessions", tags=["Admin chat sessions"])


@router.patch("/{session_id}", response_model=AdminChatSessionOut)
def update_admin_chat_session(
    session_id: UUID,
    body: ChatSessionUpdate,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
) -> AdminChatSessionOut:
    return rename_admin_session(repository, current_admin.id, session_id, body)


@router.post("/{session_id}/archive", response_model=AdminChatSessionOut)
def archive_admin_chat_session(
    session_id: UUID,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
) -> AdminChatSessionOut:
    return set_admin_session_archived(repository, current_admin.id, session_id, archived=True)


@router.post("/{session_id}/restore", response_model=AdminChatSessionOut)
def restore_admin_chat_session(
    session_id: UUID,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
) -> AdminChatSessionOut:
    return set_admin_session_archived(repository, current_admin.id, session_id, archived=False)


def get_admin_chat_summarizer(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdminChatSummarizer:
    summarizer = getattr(request.app.state, "admin_chat_summarizer", None)
    if summarizer is None:
        summarizer = OpenAIAdminChatSummarizer(settings)
        request.app.state.admin_chat_summarizer = summarizer
    return summarizer


@router.get("", response_model=AdminChatSessionPage)
def get_admin_chat_sessions(
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
) -> AdminChatSessionPage:
    """Restore the signed-in administrator's own conversation list."""

    return list_admin_sessions(repository, current_admin.id, limit=limit, cursor=cursor)


@router.get("/{session_id}/messages", response_model=ChatMessagePage)
def get_admin_chat_messages(
    session_id: UUID,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
    newest_first: Annotated[bool, Query()] = False,
) -> ChatMessagePage:
    """Restore messages without accepting an admin identity from the caller."""

    return list_admin_messages(
        repository,
        current_admin.id,
        session_id,
        limit=limit,
        cursor=cursor,
        newest_first=newest_first,
    )


@router.post("", response_model=AdminChatSessionOut, status_code=status.HTTP_201_CREATED)
def start_admin_chat_session(
    body: AdminChatSessionCreate,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdminChatSessionOut:
    return create_admin_session(
        repository,
        current_admin.id,
        body,
        model_name=settings.chat_model,
        prompt_version=settings.admin_chat_prompt_version,
        prior_session_limit=settings.chat_prior_session_limit,
    )


@router.post("/{session_id}/close", response_model=AdminChatSessionOut)
def end_admin_chat_session(
    session_id: UUID,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    summarizer: Annotated[AdminChatSummarizer, Depends(get_admin_chat_summarizer)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdminChatSessionOut:
    return close_admin_session(
        repository,
        current_admin.id,
        session_id,
        summarizer=summarizer,
        summary_message_limit=settings.chat_summary_message_limit,
        summary_max_chars=settings.chat_summary_max_chars,
    )
