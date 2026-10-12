"""Server-only persistence gateway for administrator chat and invitations."""

from typing import Any

from supabase import Client


class AdminChatRepository:
    """Keep every privileged admin query behind the service-role client."""

    SESSION_COLUMNS = (
        "id,chat_initiated_by,chat_initiator_role,subject_patient_id,title,status,summary,model_name,prompt_version,"
        "started_at,last_message_at,ended_at,created_at,updated_at"
    )
    MESSAGE_COLUMNS = (
        "id,session_id,chat_initiated_by,chat_initiator_role,sequence_number,role,message_type,content,"
        "structured_data,client_message_id,sent_by_user_id,sent_by_role,created_at"
    )
    CONTEXT_COLUMNS = "sequence_number,role,content,structured_data"

    def __init__(self, client: Client) -> None:
        self._client = client

    def select_admin_profile(self, admin_id: str) -> object:
        return (
            self._client.table("admin_profiles")
            .select("id,email,display_name,is_active")
            .eq("id", admin_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
            .data
        )

    def select_active_role(self, user_id: str, role: str) -> object:
        """Confirm authorization independently of model output or request data."""

        return (
            self._client.table("user_roles")
            .select("user_id,role,is_active")
            .eq("user_id", user_id)
            .eq("role", role)
            .eq("is_active", True)
            .limit(1)
            .execute()
            .data
        )

    def create_session(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_create_chat_session_with_context", values).execute().data

    def select_session(self, admin_id: str, session_id: str) -> object:
        return (
            self._client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .eq("id", session_id)
            .limit(1)
            .execute()
            .data
        )

    def update_session(
        self,
        admin_id: str,
        session_id: str,
        values: dict[str, Any],
        *,
        expected_status: str,
    ) -> object:
        return (
            self._client.table("chat_sessions")
            .update(values)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .eq("id", session_id)
            .eq("status", expected_status)
            .execute()
            .data
        )

    def select_summary_messages(self, admin_id: str, session_id: str, *, row_count: int) -> object:
        return (
            self._client.table("chat_messages")
            .select(self.CONTEXT_COLUMNS)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .eq("session_id", session_id)
            .order("sequence_number", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    def start_turn(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_start_chat_turn", values).execute().data

    def select_context_messages(
        self,
        admin_id: str,
        session_id: str,
        *,
        before_sequence: int,
        row_count: int,
    ) -> object:
        if row_count == 0:
            return []
        return (
            self._client.table("chat_messages")
            .select(self.CONTEXT_COLUMNS)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .eq("session_id", session_id)
            .lt("sequence_number", before_sequence)
            .order("sequence_number", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    def select_memory(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_get_session_memory", values).execute().data

    def complete_turn(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_complete_chat_turn", values).execute().data

    def fail_turn(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_fail_chat_turn", values).execute().data

    def consume_pending_action(self, values: dict[str, Any]) -> object:
        return self._client.rpc("careline_consume_pending_action", values).execute().data

    def select_sessions(self, admin_id: str, *, offset: int, row_count: int) -> object:
        """List only conversations initiated by this authenticated admin."""

        return (
            self._client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .order("last_message_at", desc=True)
            .order("id", desc=True)
            .range(offset, offset + row_count - 1)
            .execute()
            .data
        )

    def select_messages(
        self,
        admin_id: str,
        session_id: str,
        *,
        offset: int,
        row_count: int,
        newest_first: bool = False,
    ) -> object:
        """List messages for an admin-owned shared session."""

        return (
            self._client.table("chat_messages")
            .select(self.MESSAGE_COLUMNS)
            .eq("chat_initiated_by", admin_id)
            .eq("chat_initiator_role", "admin")
            .eq("session_id", session_id)
            .order("sequence_number", desc=newest_first)
            .range(offset, offset + row_count - 1)
            .execute()
            .data
        )

    # Administrative audit queries intentionally use the service-role client.
    # Authorization is enforced before this repository is called.
    def select_audit_sessions(self, *, row_count: int) -> object:
        return (
            self._client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .order("last_message_at", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    def select_audit_session(self, session_id: str) -> object:
        return (
            self._client.table("chat_sessions")
            .select(self.SESSION_COLUMNS)
            .eq("id", session_id)
            .limit(1)
            .execute()
            .data
        )

    def select_audit_table(self, table: str, session_id: str, *, order: str) -> object:
        return (
            self._client.table(table)
            .select("*")
            .eq("session_id", session_id)
            .order(order)
            .execute()
            .data
        )

    def select_sessions_by_ids(self, session_ids: list[str]) -> object:
        if not session_ids:
            return []
        return (
            self._client.table("chat_sessions")
            .select("id,title,summary,status,ended_at,last_message_at")
            .in_("id", session_ids)
            .execute()
            .data
        )

    def select_patient_summaries(self, patient_id: str, *, row_count: int) -> object:
        return (
            self._client.table("chat_sessions")
            .select("id,title,summary,status,ended_at,last_message_at")
            .eq("subject_patient_id", patient_id)
            .eq("chat_initiated_by", patient_id)
            .eq("chat_initiator_role", "patient")
            .in_("status", ["closed", "archived"])
            .not_.is_("summary", "null")
            .order("ended_at", desc=True)
            .limit(row_count)
            .execute()
            .data
        )

    @property
    def client(self) -> Client:
        """Expose the privileged client only to the invitation service."""

        return self._client
