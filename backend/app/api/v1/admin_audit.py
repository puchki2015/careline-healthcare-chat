"""Active-administrator endpoints for cross-role chat execution audits."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.deps import AdminChatRepositoryDependency, CurrentAdminDependency
from app.schemas.audit import AuditBundle, AuditSessionPage
from app.services.admin_audit_service import get_audit_bundle, list_audit_sessions

router = APIRouter(prefix="/admin/audit", tags=["Admin audit"])


@router.get("/sessions", response_model=AuditSessionPage)
def get_audit_sessions(
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AuditSessionPage:
    """List recent patient, administrator and future doctor conversations."""

    return list_audit_sessions(repository, current_admin, limit=limit)


@router.get("/sessions/{session_id}", response_model=AuditBundle)
def get_session_audit(
    session_id: UUID,
    current_admin: CurrentAdminDependency,
    repository: AdminChatRepositoryDependency,
) -> AuditBundle:
    """Return one sanitized six-table audit plus three linked-patient summaries."""

    return get_audit_bundle(repository, current_admin, session_id, summary_limit=3)
