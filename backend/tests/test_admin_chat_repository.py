"""Ensure administrator chat reuses the six shared chat tables and RPCs."""

from types import SimpleNamespace

from app.repositories.admin_chat_repository import AdminChatRepository


class Query:
    def __getattr__(self, _):
        return lambda *args, **kwargs: self

    def execute(self):
        return SimpleNamespace(data=[])


class Client:
    def __init__(self) -> None:
        self.query = Query()
        self.tables = []
        self.rpcs = []

    def table(self, name):
        self.tables.append(name)
        return self.query

    def rpc(self, name, values):
        self.rpcs.append((name, values))
        return self.query


def test_admin_repository_uses_shared_tables_and_functions() -> None:
    client = Client()
    repository = AdminChatRepository(client)  # type: ignore[arg-type]

    repository.select_session("admin-1", "session-1")
    repository.select_context_messages("admin-1", "session-1", before_sequence=4, row_count=3)
    repository.start_turn({"p_chat_initiator_role": "admin"})
    repository.complete_turn({"p_chat_initiator_role": "admin"})
    repository.consume_pending_action({"p_chat_initiator_role": "admin"})

    assert client.tables == ["chat_sessions", "chat_messages"]
    assert [name for name, _ in client.rpcs] == [
        "careline_start_chat_turn",
        "careline_complete_chat_turn",
        "careline_consume_pending_action",
    ]
    assert not any(name.startswith("admin_chat_") for name in client.tables)
