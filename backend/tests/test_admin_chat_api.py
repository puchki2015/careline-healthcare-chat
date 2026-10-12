"""HTTP contract tests for administrator session creation."""

from datetime import UTC, datetime
from uuid import uuid4

from app.deps import get_admin_chat_repository, get_current_admin
from app.schemas.admin import CurrentAdmin


class Repository:
    def __init__(self, admin_id) -> None:
        self.admin_id = admin_id

    def create_session(self, values):
        now = datetime.now(UTC).isoformat()
        return [
            {
                "id": str(uuid4()),
                "chat_initiated_by": str(self.admin_id),
                "chat_initiator_role": "admin",
                "subject_patient_id": None,
                "title": values["p_title"],
                "status": "active",
                "summary": None,
                "model_name": values["p_model_name"],
                "prompt_version": values["p_prompt_version"],
                "started_at": now,
                "last_message_at": now,
                "ended_at": None,
                "created_at": now,
                "updated_at": now,
            }
        ]


def test_admin_session_accepts_documented_json(client, test_app) -> None:
    admin = CurrentAdmin(id=uuid4(), display_name="Admin")
    repository = Repository(admin.id)
    test_app.dependency_overrides[get_current_admin] = lambda: admin
    test_app.dependency_overrides[get_admin_chat_repository] = lambda: repository

    response = client.post(
        "/api/v1/admin/chat/sessions",
        json={"title": "Create a new patient"},
    )

    assert response.status_code == 201
    assert response.json()["title"] == "Create a new patient"
