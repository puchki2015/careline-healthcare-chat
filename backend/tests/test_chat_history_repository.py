"""Prove that chat-history reads and writes use the intended Supabase clients."""

from types import SimpleNamespace

from app.repositories.chat_history_repository import ChatHistoryRepository


class RecordingQuery:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> "RecordingQuery":
            self.calls.append((name, args, kwargs))
            return self

        return record

    def execute(self) -> SimpleNamespace:
        self.calls.append(("execute", (), {}))
        return SimpleNamespace(data=self.rows)


class RecordingClient:
    def __init__(self, query: RecordingQuery) -> None:
        self.query = query
        self.tables: list[str] = []
        self.rpcs: list[tuple[str, dict[str, object]]] = []

    def table(self, name: str) -> RecordingQuery:
        self.tables.append(name)
        return self.query

    def rpc(self, name: str, values: dict[str, object]) -> RecordingQuery:
        self.rpcs.append((name, values))
        return self.query


def test_reads_use_patient_client_and_writes_use_service_role_client() -> None:
    read_query = RecordingQuery([])
    write_query = RecordingQuery([{"id": "created"}])
    read_client = RecordingClient(read_query)
    write_client = RecordingClient(write_query)
    repository = ChatHistoryRepository(  # type: ignore[arg-type]
        read_client=read_client,
        write_client=write_client,
    )

    repository.select_sessions("patient-1", "patient", offset=20, row_count=11)
    repository.select_messages("patient-1", "patient", "session-1", offset=0, row_count=31)
    repository.create_session_with_context({"p_chat_initiated_by": "patient-1"})
    repository.update_session(
        "patient-1",
        "patient",
        "session-1",
        {"status": "closed"},
        expected_status="active",
    )

    assert read_client.tables == ["chat_sessions", "chat_messages"]
    assert write_client.tables == ["chat_sessions"]
    assert write_client.rpcs == [
        ("careline_create_chat_session_with_context", {"p_chat_initiated_by": "patient-1"})
    ]
    assert ("range", (20, 30), {}) in read_query.calls
    assert ("eq", ("chat_initiated_by", "patient-1"), {}) in write_query.calls
    assert ("eq", ("chat_initiator_role", "patient"), {}) in write_query.calls
    assert ("eq", ("id", "session-1"), {}) in write_query.calls
    assert ("eq", ("status", "active"), {}) in write_query.calls


def test_turn_commands_use_admin_rpc_but_context_uses_patient_rls() -> None:
    read_query = RecordingQuery([])
    write_query = RecordingQuery([])
    read_client = RecordingClient(read_query)
    write_client = RecordingClient(write_query)
    repository = ChatHistoryRepository(  # type: ignore[arg-type]
        read_client=read_client,
        write_client=write_client,
    )

    repository.start_chat_turn({"p_session_id": "session-1"})
    repository.select_context_messages(
        "patient-1",
        "patient",
        "session-1",
        before_sequence=9,
        row_count=4,
    )
    repository.select_summary_messages("patient-1", "patient", "session-1", row_count=30)
    repository.select_session_memory(
        {"p_chat_initiated_by": "patient-1", "p_session_id": "session-1"}
    )
    repository.complete_chat_turn({"p_run_id": "run-1"})
    repository.consume_pending_action({"p_token_hash": "hash"})
    repository.fail_chat_turn({"p_run_id": "run-1"})

    assert [name for name, _ in write_client.rpcs] == [
        "careline_start_chat_turn",
        "careline_get_session_memory",
        "careline_complete_chat_turn",
        "careline_consume_pending_action",
        "careline_fail_chat_turn",
    ]
    assert read_client.tables == ["chat_messages", "chat_messages"]
    assert ("lt", ("sequence_number", 9), {}) in read_query.calls
    assert ("limit", (4,), {}) in read_query.calls
