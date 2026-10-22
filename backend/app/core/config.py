"""Application settings, loaded from the environment.

Secrets are never hard-coded: every credential arrives through the environment
or a .env file that is git-ignored.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # EVERGENT_ENV_FILE="" reads no file at all — the test suite uses it so
        # a developer's own .env cannot change test results.
        env_file=os.environ.get("EVERGENT_ENV_FILE", ".env") or None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Evergent Engineering Room"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database ----------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://evergent:evergent@localhost:5432/evergent"
    )
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Redis / queue -----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Security ----------------------------------------------------------
    secret_key: str = Field(
        default="dev-only-insecure-change-me",
        description="Signs access and refresh tokens.",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    # Fernet key used to encrypt provider API keys at rest. Generated on first
    # boot in development; required explicitly in production.
    encryption_key: str | None = None

    # Where links in emails point (password reset, email verification).
    frontend_url: str = "http://localhost:3000"
    # Email delivery. Without SMTP_HOST, nothing is sent: password reset and
    # verification emails are unavailable (in development the link is logged).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Evergent <no-reply@localhost>"
    smtp_starttls: bool = True
    # Refuse sign-in until the address is verified. Only meaningful with SMTP.
    require_email_verification: bool = False

    # Comma-separated in the environment (or a JSON list).
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Rate limiting -----------------------------------------------------
    # General allowance per client, sized for a busy workspace tab (live
    # refreshes during a run, polling, page loads).
    rate_limit_per_minute: int = 600
    # Sign-in, sign-up, password reset and verification: where guessing happens.
    auth_rate_limit_per_minute: int = 20
    # Peers whose X-Forwarded-For is believed: the frontend server and any
    # reverse proxy in front of the API. Comma-separated IPs or CIDRs.
    trusted_proxies: Annotated[list[str], NoDecode] = ["127.0.0.1/32", "::1/128"]
    # For a frontend hosted apart from the API (on Vercel, say): its outbound
    # address changes, so TRUSTED_PROXIES cannot list it. It sends this shared
    # secret instead, and the client address it reports is then believed.
    frontend_proxy_secret: str | None = None

    # --- Execution limits --------------------------------------------------
    # Safety rails: a runaway workflow is stopped rather than billed.
    max_workflow_cost_usd: float = 5.0
    # Across all of one user's workflows, per UTC day.
    max_daily_cost_usd: float = 20.0
    max_workflow_tokens: int = 2_000_000
    max_workflow_seconds: int = 1800
    max_tasks_per_workflow: int = 40
    max_task_retries: int = 3
    max_parallel_tasks: int = 4
    task_timeout_seconds: int = 900
    # The engine marks a workflow alive this often; one silent for
    # orphan_after_seconds was abandoned by a restart and is recovered.
    heartbeat_seconds: int = 20
    orphan_after_seconds: int = 120

    # --- Execution infrastructure -------------------------------------------
    # "inline" runs workflows inside the API process: fine for development,
    # but a restart loses whatever was running. "celery" hands them to the
    # worker over Redis, so the API can restart without dropping work.
    execution_backend: Literal["inline", "celery"] = "inline"

    # Model-written code only ever runs inside a throwaway container: no
    # network, no capabilities, capped memory/CPU/processes, non-root.
    sandbox_enabled: bool = True
    sandbox_docker_bin: str = "docker"
    sandbox_timeout_seconds: int = 300
    sandbox_memory: str = "1g"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256
    # The install step is the only one that may reach the network, because
    # packages have to come from somewhere. Tests always run offline.
    sandbox_install_network: bool = True
    # Where run workspaces are created. It must be a directory the container
    # runtime shares with its VM: Colima and Docker Desktop share the home
    # directory by default but NOT the system temp dir, and an unshared
    # directory mounts as an empty folder.
    sandbox_workdir_root: str = "~/.evergent/sandbox"
    sandbox_images: dict[str, str] = {
        "python": "python:3.13-slim",
        "node": "node:22-slim",
        # Throwaway database for the run_sql tool; same image Compose uses.
        "postgres": "pgvector/pgvector:pg17",
    }

    # --- File storage ------------------------------------------------------
    # Uploaded attachments. "local" for development, "s3" for any
    # S3-compatible bucket in production, "database" for hosts with neither a
    # persistent disk nor an object store (most platforms-as-a-service).
    storage_backend: Literal["local", "s3", "database"] = "local"
    storage_local_root: str = "~/.evergent/storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    # Asks the store to encrypt each object (AES256 = SSE-S3). Empty for stores
    # that encrypt anyway or reject the header (e.g. MinIO without a KMS).
    s3_server_side_encryption: str | None = "AES256"
    max_attachment_bytes: int = 2_000_000

    # --- Agent tools ---------------------------------------------------------
    # Rounds of tool use an agent gets per attempt before it must answer, and
    # calls per round. Each round is a model call, so these bound cost too.
    max_tool_rounds: int = 6
    max_tool_calls_per_round: int = 4
    # A tool result becomes prompt context; this keeps one from eating it.
    tool_result_chars: int = 6000
    # Code and SQL an agent runs mid-task get less time than a full test run.
    tool_exec_timeout_seconds: int = 120
    # Web search needs one of these. Without either, the tool is not offered.
    tavily_api_key: str | None = None
    brave_search_api_key: str | None = None
    web_fetch_enabled: bool = True
    web_fetch_max_bytes: int = 2_000_000
    web_fetch_timeout_seconds: float = 15.0

    # --- Long-term memory ---------------------------------------------------
    # "auto" uses Google if GOOGLE_API_KEY is set, else OpenAI if
    # OPENAI_API_KEY is. "none" keeps memory on keyword search only.
    embedding_provider: Literal["auto", "google", "openai", "none"] = "auto"
    google_embedding_model: str = "gemini-embedding-001"
    openai_embedding_model: str = "text-embedding-3-small"
    # How many past memories the Commander is shown when planning.
    memory_recall_limit: int = 3

    # --- AI providers ------------------------------------------------------
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    openrouter_api_key: str | None = None
    # A provider or gateway that stops answering must fail the call, not hang
    # the run: the SDK defaults are ten minutes per attempt, three attempts.
    # Generous enough for a long non-streamed answer.
    model_timeout_seconds: float = 300.0
    model_max_retries: int = 1

    # Any OpenAI-compatible gateway (OmniRoute, OpenRouter, vLLM, ...).
    compatible_base_url: str | None = None
    compatible_api_key: str | None = None

    @field_validator("cors_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Allow list settings to be comma-separated strings in .env."""
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url", mode="after")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        """Accept the plain postgres:// URLs that hosting platforms hand out.

        Render, Railway, Heroku and Neon give postgres:// or postgresql://, but
        the app talks to Postgres through asyncpg, which SQLAlchemy only picks
        for postgresql+asyncpg://.
        """
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+asyncpg://" + value[len(scheme) :]
        return value

    @model_validator(mode="after")
    def _safe_for_production(self) -> Settings:
        """Refuse to start in production with development defaults."""
        if self.environment != "production":
            return self
        problems = []
        if len(self.secret_key) < 32 or "change-me" in self.secret_key or "dev-only" in self.secret_key:
            problems.append("SECRET_KEY must be a random value of at least 32 characters")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY is required (it encrypts stored provider keys)")
        if self.debug:
            problems.append("DEBUG must be false")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS cannot be '*'")
        if self.frontend_proxy_secret and len(self.frontend_proxy_secret) < 32:
            problems.append("FRONTEND_PROXY_SECRET must be a random value of at least 32 characters")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic and other sync tooling cannot use the asyncpg driver."""
        return self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()


settings = get_settings()
