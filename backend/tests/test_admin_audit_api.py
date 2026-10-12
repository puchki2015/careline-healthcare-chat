"""Authorization and response contract tests for administrator chat audits."""

from uuid import uuid4

from app.deps import get_admin_chat_repository, get_current_admin
from app.schemas.admin import CurrentAdmin


class AuditRepository:
    def select_audit_sessions(self, *, row_count):
        return []


def test_admin_audit_requires_authentication(client) -> None:
    response = client.get("/api/v1/admin/audit/sessions")
    assert response.status_code == 401


def test_active_admin_can_list_cross_role_audit_sessions(client, test_app) -> None:
    admin = CurrentAdmin(id=uuid4(), display_name="Audit Admin")
    test_app.dependency_overrides[get_current_admin] = lambda: admin
    test_app.dependency_overrides[get_admin_chat_repository] = lambda: AuditRepository()

    response = client.get("/api/v1/admin/audit/sessions?limit=100")

    assert response.status_code == 200
    assert response.json() == {"items": []}
