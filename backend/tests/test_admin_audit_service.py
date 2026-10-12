"""Sanitization and linked-patient summary tests for administrator audits."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.schemas.admin import CurrentAdmin
from app.services.admin_audit_service import get_audit_bundle


class AuditRepository:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.patient_id = uuid4()
        self.summary_limit = None

    def select_audit_session(self, session_id):
        return [
            {
                "id": session_id,
                "chat_initiated_by": str(uuid4()),
                "chat_initiator_role": "admin",
                "subject_patient_id": str(self.patient_id),
                "title": "Patient invitation",
                "status": "closed",
            }
        ]

    def select_audit_table(self, table, session_id, *, order):
        if table == "chat_messages":
            return [{"id": str(uuid4()), "session_id": session_id, "sequence_number": 1}]
        if table == "chat_runs":
            return [{"id": str(uuid4()), "session_id": session_id, "intent": "create_patient"}]
        if table == "chat_tool_events":
            return [{"id": str(uuid4()), "session_id": session_id, "sequence_number": 1}]
        if table == "chat_pending_actions":
            return [
                {
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "token_hash": "secret-hash-must-not-leave-service",
                    "action_type": "create_patient",
                    "action_payload": {"field_count": 4},
                    "decision": None,
                    "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                }
            ]
        if table == "chat_session_context_sources":
            return [
                {
                    "id": str(uuid4()),
                    "session_id": session_id,
                    "source_session_id": "source-1",
                    "position": 1,
                }
            ]
        raise AssertionError(table)

    def select_sessions_by_ids(self, session_ids):
        return [{"id": "source-1", "title": "Earlier chat", "summary": "Earlier summary"}]

    def select_patient_summaries(self, patient_id, *, row_count):
        self.summary_limit = row_count
        assert patient_id == str(self.patient_id)
        return [{"id": str(uuid4()), "title": "Closed chat", "summary": "Patient summary"}]


def test_audit_bundle_removes_token_hash_and_includes_three_patient_summaries() -> None:
    repository = AuditRepository()
    admin = CurrentAdmin(id=uuid4(), display_name="Audit Admin")

    result = get_audit_bundle(
        repository,  # type: ignore[arg-type]
        admin,
        repository.session_id,
        summary_limit=3,
    )

    assert repository.summary_limit == 3
    assert result.patient_summaries[0]["summary"] == "Patient summary"
    assert "token_hash" not in result.pending_actions[0]
    assert result.pending_actions[0]["status"] == "expired"
    assert result.context_sources[0]["source_summary"] == "Earlier summary"
