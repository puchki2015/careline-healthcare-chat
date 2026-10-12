"""Unit tests for atomic chat-turn persistence orchestration."""

from time import perf_counter
from uuid import uuid4

import pytest

from app.agents.pending_actions import create_pending_action_draft
from app.schemas.chat import (
    ChatAction,
    ChatConfirmation,
    ChatDecision,
    ChatIntent,
    ChatRequest,
    ChatResponse,
    ChatToolTrace,
)
from app.services.chat_history_service import (
    ChatSessionNotFoundError,
    ChatSessionStateError,
)
from app.services.chat_turn_service import (
    ChatTurnAlreadyProcessedError,
    StartedChatTurn,
    complete_chat_turn,
    fail_chat_turn,
    start_chat_turn,
)


class FakeTurnRepository:
    def __init__(self) -> None:
        self.start_outcome = "started"
        self.run_id = uuid4()
        self.input_id = uuid4()
        self.output_id = uuid4()
        self.start_values: dict[str, object] | None = None
        self.complete_values: dict[str, object] | None = None
        self.fail_values: dict[str, object] | None = None
        self.context_rows: list[dict[str, object]] = []
        self.memory_rows: list[dict[str, object]] = []

    def start_chat_turn(self, values: dict[str, object]) -> object:
        self.start_values = values
        return [
            {
                "outcome": self.start_outcome,
                "run_id": str(self.run_id) if self.start_outcome == "started" else None,
                "input_message_id": (
                    str(self.input_id) if self.start_outcome in {"started", "duplicate"} else None
                ),
                "input_sequence": 7 if self.start_outcome in {"started", "duplicate"} else None,
            }
        ]

    def select_context_messages(self, *args, **kwargs) -> object:
        return self.context_rows

    def select_session_memory(self, values: dict[str, object]) -> object:
        return self.memory_rows

    def complete_chat_turn(self, values: dict[str, object]) -> object:
        self.complete_values = values
        return [{"outcome": "completed", "output_message_id": str(self.output_id)}]

    def fail_chat_turn(self, values: dict[str, object]) -> object:
        self.fail_values = values
        return True


def test_start_turn_persists_input_and_reverses_recent_context() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    client_message_id = uuid4()
    repository = FakeTurnRepository()
    repository.context_rows = [
        {"sequence_number": 6, "role": "assistant", "content": "newer"},
        {"sequence_number": 5, "role": "user", "content": "older"},
    ]
    source_session_id = uuid4()
    repository.memory_rows = [
        {
            "position": 1,
            "source_session_id": str(source_session_id),
            "summary": "Patient previously reviewed contact information.",
        }
    ]

    turn = start_chat_turn(
        repository,  # type: ignore[arg-type]
        patient_id,
        uuid4(),
        ChatRequest(
            session_id=session_id,
            client_message_id=client_message_id,
            message="Show my phone number",
        ),
        model_name="gpt-5.4-mini",
        prompt_version="patient-router-v1",
        context_limit=20,
    )

    assert repository.start_values is not None
    assert repository.start_values["p_chat_initiated_by"] == str(patient_id)
    assert repository.start_values["p_chat_initiator_role"] == "patient"
    assert repository.start_values["p_client_message_id"] == str(client_message_id)
    assert [item.content for item in turn.context] == ["older", "newer"]
    assert turn.memory[0].source_session_id == source_session_id


@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        ("not_found", ChatSessionNotFoundError),
        ("not_active", ChatSessionStateError),
        ("in_progress", ChatSessionStateError),
        ("duplicate", ChatTurnAlreadyProcessedError),
    ],
)
def test_start_turn_maps_database_outcomes(outcome, error_type) -> None:
    repository = FakeTurnRepository()
    repository.start_outcome = outcome

    with pytest.raises(error_type):
        start_chat_turn(
            repository,  # type: ignore[arg-type]
            uuid4(),
            uuid4(),
            ChatRequest(session_id=uuid4(), message="Show my profile"),
            model_name="gpt-5.4-mini",
            prompt_version="patient-router-v1",
            context_limit=20,
        )


def test_confirmation_input_never_persists_the_action_token() -> None:
    repository = FakeTurnRepository()
    secret_action_id = "x" * 43

    start_chat_turn(
        repository,  # type: ignore[arg-type]
        uuid4(),
        uuid4(),
        ChatRequest(
            session_id=uuid4(),
            confirmation=ChatConfirmation(action_id=secret_action_id, approved=True),
        ),
        model_name="gpt-5.4-mini",
        prompt_version="patient-router-v1",
        context_limit=0,
    )

    assert repository.start_values is not None
    assert repository.start_values["p_content"] == "Confirmed the proposed profile change."
    assert secret_action_id not in str(repository.start_values)


def test_complete_turn_persists_sanitized_output_and_returns_ids() -> None:
    patient_id = uuid4()
    session_id = uuid4()
    repository = FakeTurnRepository()
    turn = StartedChatTurn(
        session_id=session_id,
        run_id=repository.run_id,
        input_message_id=repository.input_id,
        input_sequence=1,
        context=(),
        memory=(),
        started_at=perf_counter(),
    )
    raw_action_id, pending_action = create_pending_action_draft(
        "phone",
        "+16045550123",
        ttl_seconds=600,
    )

    result = complete_chat_turn(
        repository,  # type: ignore[arg-type]
        patient_id,
        turn,
        ChatResponse(
            action=ChatAction.CONFIRMATION_REQUIRED,
            message="Please confirm.",
            requires_confirmation=True,
            pending_action_id=raw_action_id,
            data={"proposed_change": {"key": "phone", "value": "+16045550123"}},
        ),
        ChatDecision(
            intent=ChatIntent.UPDATE_PROFILE,
            fields=[],
            key="phone",
            value="+16045550123",
        ),
        [
            ChatToolTrace(
                node_name="propose_update",
                tool_name="pending_action.create",
                duration_ms=1.5,
                arguments={"attribute_key": "phone"},
            )
        ],
        pending_action,
    )

    assert repository.complete_values is not None
    assert raw_action_id not in str(repository.complete_values)
    assert repository.complete_values["p_pending_action"]["token_hash"] == (
        pending_action.token_hash
    )
    assert repository.complete_values["p_message_type"] == "confirmation_request"
    assert result.pending_action_id == raw_action_id
    assert result.turn is not None
    assert result.turn.output_message_id == repository.output_id


def test_fail_turn_stores_only_safe_code_and_latency() -> None:
    repository = FakeTurnRepository()

    fail_chat_turn(
        repository,  # type: ignore[arg-type]
        uuid4(),
        uuid4(),
        uuid4(),
        started_at=perf_counter(),
        error_code="ChatModelUnavailableError",
    )

    assert repository.fail_values is not None
    assert repository.fail_values["p_error_code"] == "ChatModelUnavailableError"
    assert repository.fail_values["p_latency_ms"] >= 0
