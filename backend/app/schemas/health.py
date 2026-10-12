"""Schemas returned by health endpoints."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Stable response contract for liveness checks."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
