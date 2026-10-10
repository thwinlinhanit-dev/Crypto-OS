"""M9 — the LLMProvider interface + adapters (spec §9, design note m9_ai
section 4, D-061).

``LLMProvider.stream_chat(messages, tools)`` is the single interface; the
adapter is selected by the ``LLM_PROVIDER`` env only (PD-11 — no other
selection source). Until the owner names the default (OD-4/OQ-25):

- ``EchoProvider`` — deterministic, for tests: replays the tool calls it
  is given, emits placeholders, never touches a network.
- ``OpenAIProvider`` — OpenAI-compatible chat completions with tool
  calling, behind the same interface; the key comes from the env (PD-8:
  no keys in repo).

The provider never resolves placeholders itself: it emits
``{{ref:<ref_id>#<json_path>}}`` and the server resolves them from the
stored tool results (D-059).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]

DEFAULT_ECHO: Final = "echo"
DEFAULT_OPENAI: Final = "openai"


@dataclass(frozen=True)
class Message:
    """One chat message (the OpenAI-compatible shape)."""

    role: Role
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolCall:
    """A requested tool call, parsed from the provider's response."""

    name: str
    arguments: dict[str, Any]


class ProviderUnavailableError(RuntimeError):
    """The configured provider cannot be used (missing key, bad env)."""


class LLMProvider(Protocol):
    """The single provider interface (spec §9)."""

    name: str

    def stream_chat(
        self,
        messages: list[Message],
        tools: Iterable[dict[str, Any]],
    ) -> Iterator[str]:
        """Yield text deltas; tool calls are accumulated and yielded as a
        final JSON chunk tagged ``__tool_calls__``."""
        ...


def _parse_tool_calls(raw: Iterable[dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in raw:
        name = str(item.get("name", ""))
        arguments = item.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        if name:
            calls.append(ToolCall(name=name, arguments=arguments))
    return calls


class EchoProvider:
    """A deterministic provider for tests: replays the tool calls it is
    given (the last user message is a JSON list of calls) and echoes
    placeholders. No network, no key, fully reproducible."""

    name = DEFAULT_ECHO

    def stream_chat(
        self,
        messages: list[Message],
        tools: Iterable[dict[str, Any]],
    ) -> Iterator[str]:
        _ = tools  # the echo provider does not branch on the tool set
        last_user = next(
            (m for m in reversed(messages) if m.role == "user"), None
        )
        if last_user is not None:
            try:
                parsed = json.loads(last_user.content)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            # two accepted shapes: a bare list of calls, or the tagged
            # {"__tool_calls__": [...]} wrapper the provider protocol uses
            if isinstance(parsed, dict) and isinstance(
                parsed.get("__tool_calls__"), list
            ):
                yield json.dumps(parsed)
                return
            if isinstance(parsed, list):
                calls = _parse_tool_calls(parsed)
                if calls:
                    yield json.dumps(
                        {
                            "__tool_calls__": [
                                {"name": c.name, "arguments": c.arguments}
                                for c in calls
                            ]
                        }
                    )
                    return
        yield "echo: " + (last_user.content if last_user else "")


class OpenAIProvider:
    """OpenAI-compatible chat completions with tool calling.

    The key and model come from the env (PD-8); the endpoint is
    ``OPENAI_BASE_URL`` (default the public API). The provider streams
    text deltas and, at the end, emits tool calls as a final tagged chunk.
    """

    name = DEFAULT_OPENAI

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if not self.api_key or not self.model:
            raise ProviderUnavailableError(
                "LLM_PROVIDER=openai needs OPENAI_API_KEY and LLM_MODEL in the env (PD-8)"
            )

    def stream_chat(
        self,
        messages: list[Message],
        tools: Iterable[dict[str, Any]],
    ) -> Iterator[str]:
        import urllib.request

        payload: dict[str, Any] = {
            "model": self.model,
            "stream": True,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **(
                        {"tool_calls": m.tool_calls}
                        if m.tool_calls
                        else {}
                    ),
                    **({"tool_call_id": m.tool_call_id} if m.tool_call_id else {}),
                }
                for m in messages
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t["name"], "parameters": t["schema"]},
                }
                for t in tools
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        collected: list[dict[str, Any]] = []
        with urllib.request.urlopen(request, timeout=60) as response:
            for line in response:
                text = line.decode("utf-8").strip()
                if not text.startswith("data: "):
                    continue
                data = text[len("data: ") :]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        yield str(delta["content"])
                    for call in delta.get("tool_calls", []):
                        collected.append(call)
        if collected:
            calls = _parse_tool_calls(collected)
            yield json.dumps(
                {
                    "__tool_calls__": [
                        {"name": c.name, "arguments": c.arguments} for c in calls
                    ]
                }
            )


_PROVIDERS: Final[dict[str, type[LLMProvider]]] = {
    DEFAULT_ECHO: EchoProvider,
    DEFAULT_OPENAI: OpenAIProvider,
}


def provider(name: str | None = None) -> LLMProvider:
    """The provider named by ``LLM_PROVIDER`` (D-061: env-only selection,
    PD-11); defaults to the echo provider so tests never touch a network."""
    selected = (name or os.environ.get("LLM_PROVIDER") or DEFAULT_ECHO).strip().lower()
    factory = _PROVIDERS.get(selected)
    if factory is None:
        raise ProviderUnavailableError(
            f"unknown LLM_PROVIDER {selected!r}; known: {sorted(_PROVIDERS)}"
        )
    return factory()
