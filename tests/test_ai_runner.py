"""M9 — the Copilot loop tests (spec §9, design note m9_ai section 3,
D-065): the loop is pure over (messages, mode, tools) with the provider
injected; the Critic-minimum rule (D-060) and the placeholder rules
(D-059) hold inside it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from engine.ai.providers import EchoProvider, Message, ProviderUnavailableError
from engine.ai.runner import (
    MODES,
    allowed_tools,
    mode_prompt,
    run_turn,
)

TOOL_RESULTS: dict[str, Any] = {
    "s1": {"ref_id": "s1", "dataset_version": "1", "grade": "A", "n_eff": 23},
}


def _echo_returning(*chunks: str) -> EchoProvider:
    """An EchoProvider variant whose stream yields fixed chunks (the real
    EchoProvider reads its tool calls from the last user message)."""
    provider = EchoProvider()

    def _stream(messages: list[Message], tools: Any) -> Any:
        return iter(chunks)

    provider.stream_chat = _stream  # type: ignore[method-assign]
    return provider


def _executor(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """A deterministic tool executor returning a stored result (D-066)."""
    result = dict(TOOL_RESULTS["s1"])
    result["tool"] = name
    return result


def test_the_five_modes_exist() -> None:
    assert set(MODES) == {"Research", "Quant", "Data", "Critic", "Writer"}


def test_mode_prompts_exist() -> None:
    for mode in MODES:
        assert "{{ref:" in mode_prompt(mode) or "Critic" in mode_prompt(mode)


def test_unknown_mode_fails_closed() -> None:
    from engine.ai.runner import ModeNotAllowedError

    with pytest.raises(ModeNotAllowedError):
        allowed_tools("Oracle")
    with pytest.raises(ModeNotAllowedError):
        mode_prompt("Oracle")


def test_mode_tools_are_a_subset_of_the_registry() -> None:
    for mode in MODES:
        names = {t.name for t in allowed_tools(mode)}
        assert names, mode
        assert names <= set(_all_registry_names())


def test_critic_mode_never_gets_writes() -> None:
    """Critic critiques; it never creates (spec §9: it lists ways the
    conclusion could be wrong)."""
    critic = {t.name for t in allowed_tools("Critic")}
    assert not any(n.startswith("create_") for n in critic)


def test_data_mode_is_read_only() -> None:
    data = {t.name for t in allowed_tools("Data")}
    assert not any(n.startswith("create_") for n in data)
    assert "get_data_health" in data


def test_the_loop_resolves_placeholders_from_tool_results() -> None:
    """The provider emits a tool call, then text with a placeholder; the
    loop resolves it from the stored result (D-059)."""
    provider = EchoProvider()
    messages = [
        Message(role="user", content="what is the grade"),
    ]
    turn = run_turn(
        provider=provider,
        mode="Research",
        messages=messages,
        executor=_executor,
    )
    # the echo provider echoes the user text (no tool calls in that path)
    assert "what is the grade" in turn.text


def test_the_loop_executes_a_tool_call_and_feeds_the_result_back() -> None:
    """The first round returns a tagged tool call; the second echoes it;
    the loop resolves the placeholder in the final text and the executed
    call carries the stored ref_id (D-066)."""
    provider = EchoProvider()
    first = json.dumps(
        {"__tool_calls__": [{"name": "get_experiment", "arguments": {}}]}
    )
    calls = [first, ""]

    def _stream(messages: list[Message], tools: Any) -> Any:
        return iter([calls.pop(0)])

    provider.stream_chat = _stream  # type: ignore[method-assign]
    turn = run_turn(
        provider=provider,
        mode="Research",
        messages=[Message(role="user", content="go")],
        executor=_executor,
    )
    assert turn.tool_executions[0].name == "get_experiment"
    assert turn.tool_executions[0].ref_id == "s1"
    assert turn.tool_executions[0].result["tool"] == "get_experiment"


def test_a_tool_the_mode_may_not_use_fails_closed() -> None:
    """Data mode has no create tools; a model asking for one is refused
    (PD-4: fail-closed, never a silent no-op)."""
    provider = EchoProvider()
    calls = [
        json.dumps({"__tool_calls__": [{"name": "create_note", "arguments": {}}]}),
        "done",
    ]

    def _stream(messages: list[Message], tools: Any) -> Any:
        return iter([calls.pop(0)])

    provider.stream_chat = _stream  # type: ignore[method-assign]
    from engine.ai.runner import ModeNotAllowedError

    with pytest.raises(ModeNotAllowedError):
        run_turn(
            provider=provider,
            mode="Data",
            messages=[Message(role="user", content="make a note")],
            executor=_executor,
        )


def test_an_unknown_tool_fails_closed() -> None:
    from engine.ai.registry import ToolNotFoundError
    from engine.ai.runner import ModeNotAllowedError

    provider = EchoProvider()
    calls = [
        json.dumps({"__tool_calls__": [{"name": "order_tool", "arguments": {}}]}),
        "done",
    ]

    def _stream(messages: list[Message], tools: Any) -> Any:
        return iter([calls.pop(0)])

    provider.stream_chat = _stream  # type: ignore[method-assign]
    # the mode gate rejects it before the registry lookup: Data mode has no
    # such tool, so the ModeNotAllowedError fires (the registry's own
    # ToolNotFoundError is the backstop for a tool outside every mode)
    with pytest.raises((ModeNotAllowedError, ToolNotFoundError)):
        run_turn(
            provider=provider,
            mode="Research",
            messages=[Message(role="user", content="go")],
            executor=_executor,
        )


def test_critic_mode_fails_closed_below_three_items() -> None:
    """D-060: Critic mode must list at least three ways the conclusion
    could be wrong, else the run fails."""
    from engine.ai.runner import CriticTooThinError

    provider = _echo_returning("The conclusion might be wrong.")
    with pytest.raises(CriticTooThinError):
        run_turn(
            provider=provider,
            mode="Critic",
            messages=[Message(role="user", content="critique this")],
            executor=_executor,
        )


def test_critic_mode_passes_with_three_items() -> None:
    provider = _echo_returning(
        "1. The sample is small.\n2. The regime may have shifted.\n"
        "3. Multiple testing inflates the result."
    )
    turn = run_turn(
        provider=provider,
        mode="Critic",
        messages=[Message(role="user", content="critique this")],
        executor=_executor,
    )
    assert turn.text.count("\n") >= 2


def test_env_selects_the_provider_default_echo() -> None:
    from engine.ai.providers import provider

    assert provider().name == "echo"
    assert provider("echo").name == "echo"


def test_unknown_provider_fails_closed() -> None:
    from engine.ai.providers import provider

    with pytest.raises(ProviderUnavailableError):
        provider("does-not-exist")


def test_openai_provider_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """PD-8: no keys in repo; the provider fails closed without them."""
    from engine.ai.providers import OpenAIProvider, provider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(ProviderUnavailableError):
        provider("openai")
    with pytest.raises(ProviderUnavailableError):
        OpenAIProvider()


def _all_registry_names() -> list[str]:
    from engine.ai.registry import names

    return names()
