"""Unit tests for bounded session-summary model behavior."""

import pytest

from app.agents.classifier import ChatModelUnavailableError
from app.agents.summarizer import OpenAIChatSummarizer
from app.schemas.chat_history import ChatContextMessage, ChatSummaryResult


class FakeStructuredModel:
    def __init__(self, result: object) -> None:
        self.result = result
        self.messages: list[object] = []

    def invoke(self, messages: list[object]) -> object:
        self.messages = messages
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _summarizer_with(result: object) -> tuple[OpenAIChatSummarizer, FakeStructuredModel]:
    model = FakeStructuredModel(result)
    summarizer = object.__new__(OpenAIChatSummarizer)
    summarizer._structured_model = model  # type: ignore[attr-defined]
    return summarizer, model


def test_summarizer_preserves_roles_and_returns_structured_summary() -> None:
    summarizer, model = _summarizer_with(
        ChatSummaryResult(summary="Patient reviewed contact information.")
    )

    result = summarizer.summarize(
        [
            ChatContextMessage(sequence_number=1, role="user", content="Show my phone."),
            ChatContextMessage(sequence_number=2, role="assistant", content="Profile shown."),
        ]
    )

    assert result == "Patient reviewed contact information."
    # Static system instruction plus one user and one assistant message.
    assert len(model.messages) == 3


def test_summarizer_hides_provider_failures_behind_safe_error() -> None:
    summarizer, _ = _summarizer_with(RuntimeError("sensitive provider body"))

    with pytest.raises(ChatModelUnavailableError, match="Chat summary request failed"):
        summarizer.summarize(
            [ChatContextMessage(sequence_number=1, role="user", content="Private text")]
        )
