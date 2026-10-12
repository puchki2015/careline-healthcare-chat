"""Low-level Supabase queries for conversation sessions and messages.

The repository deliberately owns no authorization decisions. Its read client
carries the patient JWT so PostgreSQL RLS applies, while its write client is the
server-only service-role client required by the migration's grants. Service
functions must always add the verified patient UUID to every write/filter.
"""

from typing import Any

from supabase import Client


class ChatHistoryRepository:
    """Execute narrowly scoped chat-history queries and return raw rows."""

    SESSION_COLUMNS = (
        "id,chat_initiated_by,chat_initiator_role,subject_patient_id,title,status,summary,model_name,prompt_version,"
        "started_at,last_message_at,ended_at,created_at,updated_at"
    )
    MESSAGE_COLUMNS = (
        "id,session_id,chat_initiated_by,chat_initiator_role,sequence_number,role,message_type,content,"
        "structured_data,client_message_id,sent_by_user_id,sent_by_role,created_at"
    )
    CONTEXT_COLUMNS = "sequence_number,role,content,structured_data"

    def __init__(self, *, read_client: Client, write_client: Client) -> None:
        self._read_client = read_client
        self._write_client = write_client

    def create_session_with_context(self, values: dict[str, Any]) -> object:
        """Atomically create a session and audit its prior-summary sources."""

        return (
            self._write_client.rpc("careline_create_chat_session_with_context", values)
            .execute()
            .data
        )

    def select_session(self, initiated_by: str, initiator_role: str, session_id: str) -> object:
        """Read one session through the patient-scoped RLS client."""

        return (
            self._read_client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .eq("id", session_id)
            .limit(1)
            .execute()
            .data
        )

    def select_sessions(
        self,
        initiated_by: str,
        initiator_role: str,
        *,
        offset: int,
        row_count: int,
    ) -> object:
        """Return a stable, bounded recent-session page through RLS."""

        return (
            self._read_client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .order("last_message_at", desc=True)
            .order("id", desc=True)
            # Supabase range endpoints are inclusive. row_count includes one
            # look-ahead row used to decide whether a next cursor is needed.
            .range(offset, offset + row_count - 1)
            .execute()
            .data
        )

    def select_messages(
        self,
        initiated_by: str,
        initiator_role: str,
        session_id: str,
        *,
        offset: int,
        row_count: int,
        newest_first: bool = False,
    ) -> object:
        """Return an ordered, bounded message page through RLS."""

        return (
            self._read_client.table("chat_messages")
            .select(self.MESSAGE_COLUMNS)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .eq("session_id", session_id)
            # History exports keep the original oldest-first behavior. The
            # live chat workspace asks for newest_first so its first page
            # always includes the turn that just completed.
            .order("sequence_number", desc=newest_first)
            .range(offset, offset + row_count - 1)
            .execute()
            .data
        )

    def update_session(
        self,
        initiated_by: str,
        initiator_role: str,
        session_id: str,
        values: dict[str, Any],
        *,
        expected_status: str | None = None,
    ) -> object:
        """Update one backend-owned session with ownership and state guards."""

        query = (
            self._write_client.table("chat_sessions")
            .update(values)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .eq("id", session_id)
        )
        # This optional compare-and-set guard prevents a concurrent archive or
        # close operation from being overwritten after the service read.
        if expected_status is not None:
            query = query.eq("status", expected_status)
        return query.execute().data

    def start_chat_turn(self, values: dict[str, Any]) -> object:
        """Atomically store one user message and its started run."""

        return self._write_client.rpc("careline_start_chat_turn", values).execute().data

    def select_context_messages(
        self,
        initiated_by: str,
        initiator_role: str,
        session_id: str,
        *,
        before_sequence: int,
        row_count: int,
    ) -> object:
        """Load a bounded prior-message window through patient RLS."""

        if row_count == 0:
            return []
        return (
            self._read_client.table("chat_messages")
            .select(self.CONTEXT_COLUMNS)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .eq("session_id", session_id)
            .lt("sequence_number", before_sequence)
            .order("sequence_number", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    def select_summary_messages(
        self,
        initiated_by: str,
        initiator_role: str,
        session_id: str,
        *,
        row_count: int,
    ) -> object:
        """Load the newest bounded messages used to summarize a session."""

        return (
            self._read_client.table("chat_messages")
            .select(self.CONTEXT_COLUMNS)
            .eq("chat_initiated_by", initiated_by)
            .eq("chat_initiator_role", initiator_role)
            .eq("session_id", session_id)
            .order("sequence_number", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    def select_session_memory(self, values: dict[str, Any]) -> object:
        """Read audited prior summaries through a server-only database function."""

        return self._write_client.rpc("careline_get_session_memory", values).execute().data

    def complete_chat_turn(self, values: dict[str, Any]) -> object:
        """Atomically finish a run and store response plus tool events."""

        return self._write_client.rpc("careline_complete_chat_turn", values).execute().data

    def consume_pending_action(self, values: dict[str, Any]) -> object:
        """Atomically verify and consume one hashed confirmation token."""

        return self._write_client.rpc("careline_consume_pending_action", values).execute().data

    def fail_chat_turn(self, values: dict[str, Any]) -> object:
        """Mark a started run failed using only a safe application error code."""

        return self._write_client.rpc("careline_fail_chat_turn", values).execute().data
