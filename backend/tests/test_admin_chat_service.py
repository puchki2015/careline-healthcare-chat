"""Shared-table persistence contracts for administrator conversations."""

from datetime import UTC, datetime
from uuid import uuid4

from app.schemas.admin import AdminChatSessionCreate
from app.services.admin_chat_service import create_admin_session


class Repository:
    def __init__(self, admin_id) -> None:
        self.admin_id = admin_id
        self.values = None

    def create_session(self, values):
        self.values = values
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


def test_admin_session_uses_shared_role_aware_rpc_contract() -> None:
    admin_id = uuid4()
    repository = Repository(admin_id)

    session = create_admin_session(
        repository,  # type: ignore[arg-type]
        admin_id,
        AdminChatSessionCreate(title="Invite a patient"),
        model_name="gpt-5.4-mini",
        prompt_version="admin-patient-invitation-v1",
        prior_session_limit=3,
    )

    assert repository.values == {
        "p_chat_initiated_by": str(admin_id),
        "p_chat_initiator_role": "admin",
        "p_subject_patient_id": None,
        "p_title": "Invite a patient",
        "p_model_name": "gpt-5.4-mini",
        "p_prompt_version": "admin-patient-invitation-v1",
        "p_prior_session_limit": 3,
    }
    assert session.subject_patient_id is None
