"""Administrator-only sanitized chat audit response contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditSessionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]


class AuditBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: dict[str, Any]
    messages: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    tool_events: list[dict[str, Any]]
    pending_actions: list[dict[str, Any]]
    context_sources: list[dict[str, Any]]
    patient_summaries: list[dict[str, Any]]
