"""HTTP contract tests for authenticated chat-session history endpoints."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.chat_sessions import get_chat_summarizer
from app.deps import get_chat_history_repository, get_current_user
from app.schemas.auth import CurrentUser
from tests.test_chat_history_service import (
    FakeHistoryRepository,
    FakeSummarizer,
    message_row,
    session_row,
)


@pytest.fixture
def history_api(
    test_app: FastAPI,
) -> Iterator[tuple[TestClient, FakeHistoryRepository, CurrentUser]]:
    current_user = CurrentUser(id=uuid4(), access_token="verified-test-token")
    repository = FakeHistoryRepository()
    test_app.dependency_overrides[get_current_user] = lambda: current_user
    test_app.dependency_overrides[get_chat_history_repository] = lambda: repository
    test_app.dependency_overrides[get_chat_summarizer] = lambda: FakeSummarizer()
    with TestClient(test_app) as client:
        yield client, repository, current_user
    test_app.dependency_overrides.clear()


def test_create_list_read_and_close_session_contract(history_api) -> None:
    client, repository, current_user = history_api
    session_id = uuid4()
    repository.sessions = [session_row(current_user.id, session_id)]
    repository.messages = [message_row(current_user.id, session_id, 1)]

    created = client.post(
        "/api/v1/chat/sessions",
        headers={"Authorization": "Bearer ignored-by-override"},
        json={"title": "Medication question"},
    )
    listed = client.get(
        "/api/v1/chat/sessions?limit=20",
        headers={"Authorization": "Bearer ignored-by-override"},
    )
    messages = client.get(
        f"/api/v1/chat/sessions/{session_id}/messages?limit=30",
        headers={"Authorization": "Bearer ignored-by-override"},
    )
    closed = client.post(
        f"/api/v1/chat/sessions/{session_id}/close",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert messages.status_code == 200
    assert messages.json()["items"][0]["sequence_number"] == 1
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_invalid_cursor_has_stable_error_envelope(history_api) -> None:
    client, _, _ = history_api

    response = client.get(
        "/api/v1/chat/sessions?cursor=invalid!",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_history_cursor"
    assert response.json()["error"]["request_id"]


def test_unknown_session_returns_not_found_without_leaking_ownership(history_api) -> None:
    client, _, _ = history_api

    response = client.get(
        f"/api/v1/chat/sessions/{uuid4()}/messages",
        headers={"Authorization": "Bearer ignored-by-override"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "chat_session_not_found"


def test_session_routes_still_require_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/chat/sessions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
