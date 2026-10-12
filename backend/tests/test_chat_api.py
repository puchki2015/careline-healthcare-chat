"""End-to-end HTTP tests for the LangGraph patient chat workflow."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.chat import get_appointment_classifier, get_chat_classifier
from app.deps import (
    get_appointment_repository,
    get_chat_history_repository,
    get_current_user,
    get_user_supabase_client,
)
from app.schemas.auth import CurrentUser
from app.schemas.chat import ChatDecision, ChatIntent
from app.schemas.chat_history import ChatContextMessage, ChatSessionMemoryRecord
from tests.test_patient_service import FakePatientClient, FakePatientQuery


class FakeClassifier:
    """Return a configured structured decision without calling OpenAI."""

    def __init__(self, decision: ChatDecision) -> None:
        self.decision = decision
        self.messages: list[str] = []
        self.histories: list[list[ChatContextMessage]] = []
        self.memories: list[list[ChatSessionMemoryRecord]] = []

    def classify(
        self,
        message: str,
        history: list[ChatContextMessage] | tuple[ChatContextMessage, ...] = (),
        memory: list[ChatSessionMemoryRecord] | tuple[ChatSessionMemoryRecord, ...] = (),
    ) -> ChatDecision:
        self.messages.append(message)
        self.histories.append(list(history))
        self.memories.append(list(memory))
        return self.decision


class FakeTurnRepository:
    """Return valid transactional-RPC shapes without a live Supabase project."""

    def __init__(self) -> None:
        self.run_id = uuid4()
        self.input_message_id = uuid4()
        self.output_message_id = uuid4()
        self.started: list[dict[str, object]] = []
        self.completed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.context_rows: list[dict[str, object]] = []
        self.memory_rows: list[dict[str, object]] = []
        self.start_outcome = "started"
        self.pending_payload: dict[str, object] | None = None

    def start_chat_turn(self, values: dict[str, object]) -> object:
        self.started.append(values)
        return [
            {
                "outcome": self.start_outcome,
                "run_id": str(self.run_id) if self.start_outcome == "started" else None,
                "input_message_id": (
                    str(self.input_message_id)
                    if self.start_outcome in {"started", "duplicate"}
                    else None
                ),
                "input_sequence": 3 if self.start_outcome in {"started", "duplicate"} else None,
            }
        ]

    def select_context_messages(self, *args, **kwargs) -> object:
        return self.context_rows

    def select_session_memory(self, values: dict[str, object]) -> object:
        return self.memory_rows

    def complete_chat_turn(self, values: dict[str, object]) -> object:
        self.completed.append(values)
        pending = values.get("p_pending_action")
        if isinstance(pending, dict):
            payload = pending.get("action_payload")
            if isinstance(payload, dict):
                self.pending_payload = payload
        return [{"outcome": "completed", "output_message_id": str(self.output_message_id)}]

    def consume_pending_action(self, values: dict[str, object]) -> object:
        # Mirror the database function's one-time-consumption behavior. The first
        # confirmation receives the proposal; every replay receives the same
        # deliberately generic unavailable result as an unknown/expired token.
        if self.pending_payload is None:
            return [{"outcome": "unavailable", "action_payload": None}]

        payload = self.pending_payload
        self.pending_payload = None
        return [{"outcome": "consumed", "action_payload": payload}]

    def fail_chat_turn(self, values: dict[str, object]) -> object:
        self.failed.append(values)
        return True


def patient_row(patient_id) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(patient_id),
        "email": "patient@example.com",
        "full_name": "Alice Patient",
        "phone": "+16045550123",
        "date_of_birth": "1990-04-12",
        "gender": "female",
        "address": {},
        "emergency_contact": {},
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def chat_api(
    test_app: FastAPI,
) -> Iterator[tuple[TestClient, FakePatientQuery, FakeClassifier, FakeTurnRepository, object]]:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[patient_row(patient_id)])
    patient_client = FakePatientClient(query)
    current_user = CurrentUser(id=patient_id, access_token="verified-test-token")
    classifier = FakeClassifier(
        ChatDecision(
            intent=ChatIntent.READ_PROFILE,
            fields=["email", "phone"],
            key=None,
            value=None,
        )
    )
    history_repository = FakeTurnRepository()
    session_id = uuid4()

    test_app.dependency_overrides[get_current_user] = lambda: current_user
    test_app.dependency_overrides[get_user_supabase_client] = lambda: patient_client
    test_app.dependency_overrides[get_chat_classifier] = lambda: classifier
    # Profile-only tests do not invoke the specialist; these overrides prevent
    # creation of real OpenAI/Supabase clients while preserving graph wiring.
    test_app.dependency_overrides[get_appointment_classifier] = lambda: None
    test_app.dependency_overrides[get_appointment_repository] = lambda: None
    test_app.dependency_overrides[get_chat_history_repository] = lambda: history_repository
    with TestClient(test_app) as client:
        yield client, query, classifier, history_repository, session_id
    test_app.dependency_overrides.clear()


def test_chat_reads_only_model_selected_whitelisted_fields(chat_api) -> None:
    client, query, classifier, history, session_id = chat_api
    query.rows = [{"email": "patient@example.com", "phone": "+16045550123"}]
    history.context_rows = [
        {"sequence_number": 2, "role": "assistant", "content": "Earlier response"},
        {"sequence_number": 1, "role": "user", "content": "Earlier question"},
    ]
    source_session_id = uuid4()
    history.memory_rows = [
        {
            "position": 1,
            "source_session_id": str(source_session_id),
            "summary": "Patient previously reviewed contact information.",
        }
    ]

    response = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "What is my email and phone?",
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "answer"
    assert response.json()["data"] == query.rows[0]
    assert query.selected == "email,phone"
    assert classifier.messages == ["What is my email and phone?"]
    assert [item.content for item in classifier.histories[0]] == [
        "Earlier question",
        "Earlier response",
    ]
    assert classifier.memories[0][0].source_session_id == source_session_id
    assert response.json()["turn"]["run_id"] == str(history.run_id)
    assert history.completed[0]["p_intent"] == "read_profile"
    assert history.completed[0]["p_tool_events"][1]["tool_name"] == "patient_profile.read"


def test_chat_update_requires_separate_confirmation_before_database_write(chat_api) -> None:
    client, query, classifier, history, session_id = chat_api
    classifier.decision = ChatDecision(
        intent=ChatIntent.UPDATE_PROFILE,
        fields=[],
        key="phone",
        value="  +16045550999  ",
    )

    proposal = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Change my phone number to +16045550999",
        },
    )

    assert proposal.status_code == 200
    assert proposal.json()["action"] == "confirmation_required"
    assert proposal.json()["requires_confirmation"] is True
    assert query.updated is None

    approved = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "confirmation": {
                "action_id": proposal.json()["pending_action_id"],
                "approved": True,
            },
        },
    )

    assert approved.status_code == 200
    assert approved.json()["action"] == "updated"
    assert query.updated == {"phone": "+16045550999"}
    # Confirmations bypass the model entirely.
    assert len(classifier.messages) == 1
    # The raw confirmation token is returned over HTTPS but excluded from the
    # durable assistant-message JSON.
    assert "pending_action_id" not in history.completed[0]["p_structured_data"]


def test_chat_rejection_consumes_proposal_without_database_write(chat_api) -> None:
    client, query, classifier, _, session_id = chat_api
    classifier.decision = ChatDecision(
        intent=ChatIntent.UPDATE_PROFILE,
        fields=[],
        key="gender",
        value="other",
    )
    proposal = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Change my gender to other",
        },
    )

    rejected = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "confirmation": {
                "action_id": proposal.json()["pending_action_id"],
                "approved": False,
            },
        },
    )

    assert rejected.status_code == 200
    assert rejected.json()["action"] == "cancelled"
    assert query.updated is None


def test_chat_parses_structured_address_before_creating_proposal(chat_api) -> None:
    client, query, classifier, _, session_id = chat_api
    classifier.decision = ChatDecision(
        intent=ChatIntent.UPDATE_PROFILE,
        fields=[],
        key="address",
        value='{"line1":"123 Main Street","city":"Vancouver"}',
    )

    proposal = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Change my address to 123 Main Street in Vancouver",
        },
    )

    assert proposal.status_code == 200
    assert proposal.json()["data"]["proposed_change"] == {
        "key": "address",
        "value": {
            "line1": "123 Main Street",
            "line2": None,
            "city": "Vancouver",
            "region": None,
            "postal_code": None,
            "country": None,
        },
    }
    assert query.updated is None


def test_chat_rejects_replayed_confirmation(chat_api) -> None:
    client, query, classifier, _, session_id = chat_api
    classifier.decision = ChatDecision(
        intent=ChatIntent.UPDATE_PROFILE,
        fields=[],
        key="gender",
        value="other",
    )
    proposal = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Change my gender to other",
        },
    )
    confirmation = {
        "session_id": str(session_id),
        "client_message_id": str(uuid4()),
        "confirmation": {
            "action_id": proposal.json()["pending_action_id"],
            "approved": True,
        },
    }

    assert client.post("/api/v1/chat", json=confirmation).status_code == 200
    confirmation["client_message_id"] = str(uuid4())
    replay = client.post("/api/v1/chat", json=confirmation)

    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "chat_action_invalid"


def test_chat_returns_canned_boundary_for_unsupported_request(chat_api) -> None:
    client, query, classifier, _, session_id = chat_api
    classifier.decision = ChatDecision(
        intent=ChatIntent.UNSUPPORTED,
        fields=[],
        key=None,
        value=None,
    )

    response = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Diagnose my headache",
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "unsupported"
    assert query.selected is None
    assert query.updated is None


def test_chat_requires_exactly_one_input(chat_api) -> None:
    client, _, _, _, session_id = chat_api

    response = client.post("/api/v1/chat", json={"session_id": str(session_id)})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"


def test_chat_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        json={"session_id": str(uuid4()), "message": "Show my profile"},
    )

    assert response.status_code == 401


def test_chat_rejects_reused_client_message_id_before_model_call(chat_api) -> None:
    client, _, classifier, history, session_id = chat_api
    history.start_outcome = "duplicate"

    response = client.post(
        "/api/v1/chat",
        json={
            "session_id": str(session_id),
            "client_message_id": str(uuid4()),
            "message": "Show my profile",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "chat_turn_already_processed"
    assert classifier.messages == []


def test_chat_openapi_documents_security_and_errors(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/chat"]["post"]

    assert operation["security"] == [{"Supabase access token": []}]
    assert {"200", "400", "401", "422", "500", "503"}.issubset(operation["responses"])
