"""Provider and model payloads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    # Accepted on write, encrypted at rest, never returned.
    api_key: str | None = None
    base_url: str | None = None
    is_enabled: bool = True


class ProviderUpdate(BaseModel):
    """Every field optional. An empty api_key removes the stored key."""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: str | None = None
    base_url: str | None = None
    is_enabled: bool | None = None


class ProviderOut(ORMModel):
    id: uuid.UUID
    name: str
    display_name: str
    base_url: str | None
    is_enabled: bool
    # Masked, e.g. "sk-••••1234". The real credential never leaves the server.
    api_key_preview: str | None = None
    model_count: int = 0
    # A credential exists (stored here or in the server environment).
    configured: bool = False
    credential_source: str | None = None
    # A real call proved it works — distinct from merely being configured.
    verified_at: datetime | None = None
    verification_ok: bool | None = None
    verification_error: str | None = None
    verified_model_count: int = 0


class ModelOut(ORMModel):
    id: uuid.UUID
    name: str
    display_name: str
    remote_id: str
    provider_name: str
    context_window: int
    max_output_tokens: int
    cost_per_input_token: float
    cost_per_output_token: float
    capabilities: dict[str, float]
    avg_latency_ms: int
    reliability: float
    is_enabled: bool
    # True/False once its provider is verified: whether the account lists it.
    listed_by_provider: bool | None = None


class ModelStatsOut(BaseModel):
    """Routing inputs recomputed from the execution log."""

    name: str
    avg_latency_ms: int
    reliability: float


class UsageDay(BaseModel):
    day: str
    cost_usd: float
    tokens: int
    calls: int


class UsageOut(BaseModel):
    """Spend over the last `days` days (all figures cover the same window)."""

    days: int = 30
    total_cost_usd: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    call_count: int
    by_model: dict[str, float]
    by_provider: dict[str, float]
    by_role: dict[str, float] = {}
    failed_calls: int = 0
    # One entry per UTC day with any activity, oldest first.
    daily: list[UsageDay] = []
    spent_today_usd: float = 0.0
    daily_limit_usd: float = 0.0
    # Everything here is estimated from configured per-token prices.
    estimated: bool = True


class ExecutionOut(ORMModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    task_id: uuid.UUID | None
    provider_name: str
    model_name: str
    role: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: int
    succeeded: bool
    error: str | None
    created_at: datetime
