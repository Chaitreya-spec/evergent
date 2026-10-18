"""Provider abstraction.

The orchestration engine talks only to this interface. No provider-specific
branching is allowed above this layer — that is what lets a new provider be
added without touching the router, planner or execution engine.
"""

from __future__ import annotations

import abc
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ProviderError


@dataclass(slots=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class ToolInvocation:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(slots=True)
class CompletionRequest:
    model: str
    messages: list[ChatMessage]
    system: str | None = None
    max_tokens: int = 8192
    temperature: float | None = None
    # Ask for JSON. Providers express this natively where they can; callers
    # must still parse defensively because JSON mode is a hint, not a promise.
    json_mode: bool = False
    json_schema: dict[str, Any] | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    # Invoked with each chunk of generated text, for live progress.
    on_delta: Callable[[str], None] | None = None


@dataclass(slots=True)
class CompletionResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    latency_ms: int = 0
    raw: Any = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AIProvider(abc.ABC):
    """Common surface every provider implements."""

    name: str

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """False when the credential is missing, so the router can skip it."""

    @abc.abstractmethod
    async def generate(self, request: CompletionRequest) -> CompletionResult:
        """One completion, awaited to the end."""

    async def verify(self) -> list[str]:
        """Prove the credential works: list the models this account can use.

        Costs no tokens. Raises when the provider rejects the call.
        """
        raise NotImplementedError(f"{self.name} cannot be verified")

    @abc.abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncIterator[str]:
        """Yield text deltas as they arrive."""

    async def analyze(
        self, request: CompletionRequest, instruction: str
    ) -> CompletionResult:
        """Generate with an analysis instruction prepended to the system prompt."""
        system = f"{request.system}\n\n{instruction}" if request.system else instruction
        return await self.generate(
            CompletionRequest(
                model=request.model,
                messages=request.messages,
                system=system,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        )

    async def structured_output(
        self, request: CompletionRequest, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate and return parsed JSON validated against `schema` shape."""
        request.json_mode = True
        request.json_schema = schema
        result = await self.generate(request)
        return extract_json(result.text)

    async def tool_call(
        self, request: CompletionRequest, tools: list[ToolSpec]
    ) -> CompletionResult:
        """Generate with tools available. Returns any requested invocations."""
        request.tools = tools
        return await self.generate(request)


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(raw: str) -> Any:
    """Pull a JSON value out of a model response.

    Even in JSON mode a model may wrap output in prose or a fenced block, so
    this tries the whole string, then a fenced block, then the outermost
    balanced brackets. Raising is correct: the caller retries or fails the task
    rather than proceeding on a half-parsed plan.
    """
    text = raw.strip()
    attempts: list[str] = [text]

    fenced = _FENCE_RE.search(text)
    if fenced:
        attempts.append(fenced.group(1).strip())

    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        closer = "}" if text[start] == "{" else "]"
        end = text.rfind(closer)
        if end > start:
            attempts.append(text[start : end + 1])

    for candidate in attempts:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ProviderError(
        "Model did not return parsable JSON",
        details={"preview": text[:300]},
    )
