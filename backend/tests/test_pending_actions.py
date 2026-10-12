"""Security and lifecycle tests for durable pending update proposals."""

import hashlib
from uuid import uuid4

import pytest

from app.agents.pending_actions import (
    PendingActionNotFoundError,
    consume_pending_action,
    create_pending_action_draft,
)


class FakePendingRepository:
    def __init__(self) -> None:
        self.outcome = "consumed"
        self.payload: dict[str, object] | None = {
            "key": "phone",
            "value": "+16045550999",
        }
        self.values: dict[str, object] | None = None

    def consume_pending_action(self, values: dict[str, object]) -> object:
        self.values = values
        return [{"outcome": self.outcome, "action_payload": self.payload}]


def test_draft_returns_raw_token_but_stores_only_its_hash() -> None:
    action_id, draft = create_pending_action_draft(
        "phone",
        "+16045550999",
        ttl_seconds=600,
    )

    assert len(action_id) >= 32
    assert draft.token_hash == hashlib.sha256(action_id.encode()).hexdigest()
    assert action_id not in draft.model_dump_json()
    assert draft.action_payload == {"key": "phone", "value": "+16045550999"}


def test_consume_hashes_token_and_scopes_database_command() -> None:
    repository = FakePendingRepository()
    patient_id = uuid4()
    session_id = uuid4()
    action_id = "a-secret-confirmation-token-with-enough-length"

    pending = consume_pending_action(
        repository,  # type: ignore[arg-type]
        action_id,
        patient_id,
        session_id,
        decision="approved",
    )

    assert pending.key == "phone"
    assert pending.value == "+16045550999"
    assert repository.values == {
        "p_chat_initiated_by": str(patient_id),
        "p_chat_initiator_role": "patient",
        "p_session_id": str(session_id),
        "p_token_hash": hashlib.sha256(action_id.encode()).hexdigest(),
        "p_decision": "approved",
    }
    assert action_id not in str(repository.values)


def test_unknown_expired_consumed_or_wrongly_owned_action_is_generic() -> None:
    repository = FakePendingRepository()
    repository.outcome = "unavailable"
    repository.payload = None

    with pytest.raises(PendingActionNotFoundError):
        consume_pending_action(
            repository,  # type: ignore[arg-type]
            "unknown-action-token-with-enough-length",
            uuid4(),
            uuid4(),
            decision="rejected",
        )


def test_two_proposals_receive_different_tokens_and_hashes() -> None:
    first_id, first = create_pending_action_draft("gender", "other", ttl_seconds=600)
    second_id, second = create_pending_action_draft("gender", "other", ttl_seconds=600)

    assert first_id != second_id
    assert first.token_hash != second.token_hash
