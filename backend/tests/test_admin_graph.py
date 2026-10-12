"""LangGraph routing tests for the createPatient administrator node."""

from types import SimpleNamespace
from uuid import uuid4

from app.agents.admin_graph import build_admin_chat_graph
from app.schemas.admin import (
    AdminChatAction,
    AdminChatConfirmation,
    AdminChatDecision,
    AdminChatIntent,
    AdminChatRequest,
    CurrentAdmin,
)


class Classifier:
    def __init__(self, decision: AdminChatDecision) -> None:
        self.decision = decision

    def classify(self, *_):
        return self.decision


class PendingRepository:
    def __init__(self) -> None:
        self.values = None

    def consume_pending_action(self, values):
        self.values = values
        return [
            {
                "outcome": "consumed",
                "action_payload": {
                    "full_name": "Jamie Patient",
                    "date_of_birth": "1990-05-12",
                    "phone": "+16045550123",
                    "email": "jamie@example.com",
                },
            }
        ]


def graph(decision, repository=None):
    return build_admin_chat_graph(
        classifier=Classifier(decision),  # type: ignore[arg-type]
        current_admin=CurrentAdmin(id=uuid4(), display_name="Admin"),
        repository=repository or PendingRepository(),  # type: ignore[arg-type]
        invitation_redirect_url="http://localhost:5173/set-password",
        pending_action_ttl_seconds=600,
    )


def test_create_patient_collects_missing_fields_before_proposal() -> None:
    workflow = graph(
        AdminChatDecision(
            intent=AdminChatIntent.CREATE_PATIENT,
            full_name="Jamie Patient",
            date_of_birth=None,
            phone=None,
            email=None,
            missing_fields=["date_of_birth", "phone", "email"],
        )
    )

    result = workflow.invoke(
        {"request": AdminChatRequest(session_id=uuid4(), message="Create Jamie")}
    )

    assert result["response"].action == AdminChatAction.INFORMATION_REQUIRED
    assert result["response"].data["missing_fields"] == [
        "date_of_birth",
        "phone",
        "email",
    ]


def test_create_patient_proposes_hash_only_pending_action() -> None:
    workflow = graph(
        AdminChatDecision(
            intent=AdminChatIntent.CREATE_PATIENT,
            full_name="Jamie Patient",
            date_of_birth="1990-05-12",
            phone="+16045550123",
            email="jamie@example.com",
            missing_fields=[],
        )
    )

    result = workflow.invoke(
        {"request": AdminChatRequest(session_id=uuid4(), message="Invite Jamie")}
    )

    response = result["response"]
    assert response.action == AdminChatAction.CONFIRMATION_REQUIRED
    assert response.requires_confirmation is True
    assert response.pending_action_id not in result["pending_action"].model_dump_json()
    assert result["pending_action"].action_type == "create_patient"


def test_rejected_confirmation_consumes_action_without_invitation() -> None:
    repository = PendingRepository()
    workflow = graph(
        AdminChatDecision(
            intent=AdminChatIntent.UNSUPPORTED,
            full_name=None,
            date_of_birth=None,
            phone=None,
            email=None,
            missing_fields=[],
        ),
        repository,
    )

    result = workflow.invoke(
        {
            "request": AdminChatRequest(
                session_id=uuid4(),
                confirmation=AdminChatConfirmation(action_id="a" * 43, approved=False),
            )
        }
    )

    assert result["response"].action == AdminChatAction.CANCELLED
    assert repository.values["p_chat_initiator_role"] == "admin"
    assert repository.values["p_decision"] == "rejected"


def test_approved_confirmation_invites_and_links_subject(monkeypatch) -> None:
    patient_id = uuid4()

    class LinkingRepository(PendingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.link = None

        def update_session(self, admin_id, session_id, values, *, expected_status):
            self.link = (admin_id, session_id, values, expected_status)
            return [{"id": session_id}]

    repository = LinkingRepository()
    invited = []

    def fake_invite(repo, details, *, redirect_to):
        invited.append((repo, details.email, redirect_to))
        return patient_id

    monkeypatch.setattr("app.agents.admin_graph.invite_patient", fake_invite)
    workflow = build_admin_chat_graph(
        classifier=SimpleNamespace(),  # not called for confirmation turns
        current_admin=CurrentAdmin(id=uuid4(), display_name="Admin"),
        repository=repository,  # type: ignore[arg-type]
        invitation_redirect_url="http://localhost:5173/set-password",
        pending_action_ttl_seconds=600,
    )
    session_id = uuid4()

    result = workflow.invoke(
        {
            "request": AdminChatRequest(
                session_id=session_id,
                confirmation=AdminChatConfirmation(action_id="b" * 43, approved=True),
            )
        }
    )

    assert result["response"].action == AdminChatAction.PATIENT_INVITED
    assert invited[0][1:] == (
        "jamie@example.com",
        "http://localhost:5173/set-password",
    )
    assert repository.link[2] == {"subject_patient_id": str(patient_id)}
    assert repository.link[3] == "active"
