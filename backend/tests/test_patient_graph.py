"""Direct graph tests that do not require an HTTP server or model provider."""

from uuid import uuid4

from app.agents.patient_graph import build_patient_chat_graph
from app.schemas.auth import CurrentUser
from app.schemas.chat import ChatDecision, ChatIntent, ChatRequest
from app.schemas.chat_history import ChatContextMessage, ChatSessionMemoryRecord
from tests.test_chat_api import FakeClassifier, FakeTurnRepository
from tests.test_patient_service import FakePatientClient, FakePatientQuery


def test_graph_passes_bounded_context_and_returns_sanitized_traces() -> None:
    patient_id = uuid4()
    query = FakePatientQuery(
        rows=[
            {
                "email": "patient@example.com",
                "phone": "+16045550123",
            }
        ]
    )
    classifier = FakeClassifier(
        ChatDecision(
            intent=ChatIntent.READ_PROFILE,
            fields=["email", "phone"],
            key=None,
            value=None,
        )
    )
    context = (
        ChatContextMessage(sequence_number=1, role="user", content="Show my profile"),
        ChatContextMessage(
            sequence_number=2,
            role="assistant",
            content="Here are the requested details.",
        ),
    )
    memory = (
        ChatSessionMemoryRecord(
            position=1,
            source_session_id=uuid4(),
            summary="Patient previously reviewed contact information.",
        ),
    )
    graph = build_patient_chat_graph(
        classifier=classifier,
        current_user=CurrentUser(id=patient_id, access_token="test-token"),
        patient_client=FakePatientClient(query),  # type: ignore[arg-type]
        history_repository=FakeTurnRepository(),  # type: ignore[arg-type]
        pending_action_ttl_seconds=600,
        context=context,
        memory=memory,
    )

    result = graph.invoke(
        {
            "request": ChatRequest(
                session_id=uuid4(),
                message="What is my phone number?",
            )
        }
    )

    assert classifier.histories == [list(context)]
    assert classifier.memories == [list(memory)]
    assert [trace.tool_name for trace in result["traces"]] == [
        "chat_model.classify",
        "patient_profile.read",
    ]
    assert result["traces"][1].result == {"field_count": 2}
