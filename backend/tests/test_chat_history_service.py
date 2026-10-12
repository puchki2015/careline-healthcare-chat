"""Unit tests for deterministic chat-session and message-history behavior."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.schemas.chat_history import ChatSessionCreate, ChatSessionUpdate
from app.services.chat_history_service import (
    ChatHistoryCursorError,
    ChatSessionNotFoundError,
    ChatSessionStateError,
    close_chat_session,
    create_chat_session,
    list_chat_messages,
    list_chat_sessions,
    rename_chat_session,
    set_chat_session_archived,
)


def session_row(
    patient_id: UUID,
    session_id: UUID | None = None,
    *,
    status: str = "active",
) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(session_id or uuid4()),
        "chat_initiated_by": str(patient_id),
        "chat_initiator_role": "patient",
        "subject_patient_id": str(patient_id),
        "title": "Medication question",
        "status": status,
        "summary": None,
        "model_name": "gpt-5.4-mini",
        "prompt_version": "patient-router-v1",
        "started_at": now,
        "last_message_at": now,
        "ended_at": now if status == "closed" else None,
        "created_at": now,
        "updated_at": now,
    }


def message_row(patient_id: UUID, session_id: UUID, sequence: int) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "session_id": str(session_id),
        "chat_initiated_by": str(patient_id),
        "chat_initiator_role": "patient",
        "sequence_number": sequence,
        "role": "user" if sequence % 2 else "assistant",
        "message_type": "text",
        "content": f"message {sequence}",
        "structured_data": None,
        "client_message_id": None,
        "sent_by_user_id": str(patient_id) if sequence % 2 else None,
        "sent_by_role": "patient" if sequence % 2 else None,
        "created_at": datetime.now(UTC).isoformat(),
    }


class FakeHistoryRepository:
    """In-memory repository double that also records service call arguments."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.created: dict[str, object] | None = None
        self.updated: tuple[str, str, dict[str, object]] | None = None
        self.session_query: tuple[str, str] | None = None
        self.session_page_query: tuple[str, int, int] | None = None
        self.message_page_query: tuple[str, str, int, int] | None = None
        self.message_newest_first = False

    def create_session_with_context(self, values: dict[str, object]) -> object:
        self.created = values
        return self.sessions

    def select_session(self, patient_id: str, role: str, session_id: str) -> object:
        self.session_query = (patient_id, session_id)
        return [
            row
            for row in self.sessions
            if row["id"] == session_id
            and row["chat_initiated_by"] == patient_id
            and row["chat_initiator_role"] == role
        ]

    def select_sessions(self, patient_id: str, role: str, *, offset: int, row_count: int) -> object:
        self.session_page_query = (patient_id, offset, row_count)
        return self.sessions[offset : offset + row_count]

    def select_messages(
        self,
        patient_id: str,
        role: str,
        session_id: str,
        *,
        offset: int,
        row_count: int,
        newest_first: bool = False,
    ) -> object:
        self.message_page_query = (patient_id, session_id, offset, row_count)
        self.message_newest_first = newest_first
        rows = list(reversed(self.messages)) if newest_first else self.messages
        return rows[offset : offset + row_count]

    def select_summary_messages(
        self,
        patient_id: str,
        role: str,
        session_id: str,
        *,
        row_count: int,
    ) -> object:
        matching = [
            row
            for row in self.messages
            if row["chat_initiated_by"] == patient_id
            and row["chat_initiator_role"] == role
            and row["session_id"] == session_id
        ]
        return [
            {
                "sequence_number": row["sequence_number"],
                "role": row["role"],
                "content": row["content"],
            }
            for row in reversed(matching[-row_count:])
        ]

    def update_session(
        self,
        patient_id: str,
        role: str,
        session_id: str,
        values: dict[str, object],
        *,
        expected_status: str | None = None,
    ) -> object:
        if expected_status is not None and self.sessions[0]["status"] != expected_status:
            return []
        self.updated = (patient_id, session_id, values)
        row = {**self.sessions[0], **values}
        self.sessions[0] = row
        return [row]


class FakeSummarizer:
    def __init__(self, summary: str = "Patient reviewed profile information.") -> None:
        self.summary = summary
        self.messages: list[object] = []

    def summarize(self, messages) -> str:
        self.messages = list(messages)
        return self.summary


def test_create_session_sets_server_owned_fields() -> None:
    patient_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id)]

    result = create_chat_session(
        repository,  # type: ignore[arg-type]
        patient_id,
        ChatSessionCreate(title="Medication question"),
        model_name="gpt-5.4-mini",
        prompt_version="patient-router-v1",
        prior_session_limit=3,
    )

    assert repository.created == {
        "p_chat_initiated_by": str(patient_id),
        "p_chat_initiator_role": "patient",
        "p_subject_patient_id": str(patient_id),
        "p_title": "Medication question",
        "p_model_name": "gpt-5.4-mini",
        "p_prompt_version": "patient-router-v1",
        "p_prior_session_limit": 3,
    }
    assert result.title == "Medication question"
    assert "patient_id" not in result.model_dump()


def test_session_cursor_returns_the_next_bounded_page() -> None:
    patient_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id) for _ in range(3)]

    first = list_chat_sessions(
        repository,
        patient_id,
        limit=2,
        cursor=None,  # type: ignore[arg-type]
    )
    second = list_chat_sessions(
        repository,
        patient_id,
        limit=2,
        cursor=first.next_cursor,  # type: ignore[arg-type]
    )

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert repository.session_page_query == (str(patient_id), 2, 3)


def test_invalid_cursor_is_rejected_before_database_query() -> None:
    repository = FakeHistoryRepository()

    with pytest.raises(ChatHistoryCursorError):
        list_chat_sessions(
            repository,
            uuid4(),
            limit=20,
            cursor="not-a-cursor!",  # type: ignore[arg-type]
        )

    assert repository.session_page_query is None


def test_messages_require_an_owned_session_and_keep_sequence_order() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id)]
    repository.messages = [
        message_row(patient_id, session_id, 1),
        message_row(patient_id, session_id, 2),
    ]

    result = list_chat_messages(
        repository,
        patient_id,
        session_id,
        limit=30,
        cursor=None,  # type: ignore[arg-type]
    )

    assert [item.sequence_number for item in result.items] == [1, 2]
    assert repository.session_query == (str(patient_id), str(session_id))
    assert repository.message_page_query == (str(patient_id), str(session_id), 0, 31)


def test_unknown_or_unowned_session_looks_the_same_to_the_client() -> None:
    repository = FakeHistoryRepository()
    other_patient_id = uuid4()
    session_id = uuid4()
    repository.sessions = [session_row(other_patient_id, session_id)]

    with pytest.raises(ChatSessionNotFoundError):
        list_chat_messages(
            repository,
            uuid4(),
            session_id,
            limit=30,
            cursor=None,  # type: ignore[arg-type]
        )


def test_newest_message_page_contains_latest_turn_in_display_order() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id)]
    repository.messages = [
        message_row(patient_id, session_id, sequence) for sequence in range(1, 5)
    ]

    result = list_chat_messages(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        limit=2,
        cursor=None,
        newest_first=True,
    )

    assert repository.message_newest_first is True
    assert [item.sequence_number for item in result.items] == [3, 4]
    assert result.next_cursor is not None


def test_session_can_be_renamed_archived_and_restored() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id, status="closed")]

    renamed = rename_chat_session(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        ChatSessionUpdate(title="Phone number update"),
    )
    archived = set_chat_session_archived(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        archived=True,
    )
    restored = set_chat_session_archived(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        archived=False,
    )

    assert renamed.title == "Phone number update"
    assert archived.status == "archived"
    assert restored.status == "closed"


def test_active_session_must_be_closed_before_archive() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id, status="active")]

    with pytest.raises(ChatSessionStateError):
        set_chat_session_archived(
            repository,  # type: ignore[arg-type]
            patient_id,
            session_id,
            archived=True,
        )


def test_close_active_session_is_scoped_and_sets_end_time() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id)]
    repository.messages = [
        message_row(patient_id, session_id, 1),
        message_row(patient_id, session_id, 2),
    ]
    summarizer = FakeSummarizer("Patient asked to review profile information.")

    result = close_chat_session(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        summarizer=summarizer,
        summary_message_limit=30,
        summary_max_chars=2000,
    )

    assert result.status.value == "closed"
    assert result.ended_at is not None
    assert repository.updated is not None
    assert repository.updated[0:2] == (str(patient_id), str(session_id))
    assert repository.updated[2]["status"] == "closed"
    assert repository.updated[2]["summary"] == ("Patient asked to review profile information.")
    assert [message.sequence_number for message in summarizer.messages] == [1, 2]


def test_close_is_idempotent_but_archived_session_is_not_changed() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeHistoryRepository()
    repository.sessions = [session_row(patient_id, session_id, status="closed")]

    summarizer = FakeSummarizer()
    repeated = close_chat_session(
        repository,  # type: ignore[arg-type]
        patient_id,
        session_id,
        summarizer=summarizer,
        summary_message_limit=30,
        summary_max_chars=2000,
    )

    assert repeated.status.value == "closed"
    assert repository.updated is None

    repository.sessions = [session_row(patient_id, session_id, status="archived")]
    with pytest.raises(ChatSessionStateError):
        close_chat_session(
            repository,  # type: ignore[arg-type]
            patient_id,
            session_id,
            summarizer=summarizer,
            summary_message_limit=30,
            summary_max_chars=2000,
        )
