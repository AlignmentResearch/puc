"""Provider-agnostic chat client for the persuasion experiments.

One small interface — ``LLMClient.complete()`` — with two implementations:

* ``AnthropicClient``    — native Anthropic Messages API. Used now.
* ``OpenAICompatClient`` — any OpenAI-compatible endpoint: OpenRouter, OpenAI,
  or Anthropic's own compatibility layer. Used once OpenRouter access lands.

Swapping providers is a one-line change: pick the client, pick the model
string. Everything upstream only ever sees ``Message`` / ``Completion`` — the
transcript-continuation vs. messages decision, the actor/simulated-user/judge
roles, and the scenario data all sit on top of this and never import an SDK.

Credentials come from the environment (never hard-code a key). Load ``.env``
in the entrypoint (see ``smoke_test.py``) or export the vars in your shell.

Usage:
    from client import make_client, Message

    client = make_client("anthropic")          # reads ANTHROPIC_API_KEY
    out = client.complete(
        system="You are a helpful assistant.",
        messages=[Message("user", "Hello")],
        model="claude-opus-4-8",
    )
    print(out.text)

    # Later, same call site, different provider:
    client = make_client("openrouter")         # reads OPENROUTER_API_KEY
    out = client.complete(..., model="anthropic/claude-opus-4-8")
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    """One conversation turn. The system prompt is passed separately to
    ``complete()``, not as a Message."""

    role: Role
    content: str


@dataclass(frozen=True)
class Completion:
    """The result of one model call, with everything the harness needs to log
    a trial reproducibly."""

    text: str  # concatenated text output (native thinking blocks excluded)
    model: str  # exact model id that served the request
    stop_reason: str | None  # "end_turn" | "max_tokens" | "refusal" | ...
    usage: dict | None  # token counts, provider-shaped
    raw: object  # untouched provider response, for logging / debugging


@runtime_checkable
class LLMClient(Protocol):
    """The contract every provider adapter satisfies. Keep this the only shape
    the rest of the harness depends on."""

    provider: str

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
        stop: list[str] | None = None,
        thinking: dict | None = None,
    ) -> Completion: ...


class AnthropicClient:
    """Native Anthropic Messages API.

    Note: on ``claude-opus-4-8`` / ``claude-opus-4-7`` (and Fable 5), the
    ``temperature`` / ``top_p`` / ``top_k`` sampling params return a 400 — they
    are removed in favor of adaptive thinking. Leave ``temperature`` unset for
    those models; it is honored on ``claude-sonnet-4-6`` / ``claude-haiku-4-5``.
    """

    provider = "anthropic"

    def __init__(self, api_key: str | None = None):
        import anthropic  # lazy — only this provider needs the SDK installed

        # api_key=None → the SDK reads ANTHROPIC_API_KEY from the environment.
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
        stop: list[str] | None = None,
        thinking: dict | None = None,
    ) -> Completion:
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if stop is not None:
            kwargs["stop_sequences"] = stop  # Anthropic's name for `stop`
        if thinking is not None:
            kwargs["thinking"] = thinking  # e.g. {"type": "adaptive"}
        if temperature is not None:
            kwargs["temperature"] = temperature  # see class note — 400s on Opus 4.8/4.7

        resp = self._client.messages.create(**kwargs)

        # Skip thinking/other blocks; concatenate visible text. A refusal comes
        # back with stop_reason == "refusal" and (usually) empty content — the
        # caller decides how to record it; we don't raise.
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else None
        return Completion(
            text=text,
            model=resp.model,
            stop_reason=resp.stop_reason,
            usage=usage,
            raw=resp,
        )


class OpenAICompatClient:
    """Any OpenAI-compatible chat endpoint — OpenRouter, OpenAI, or Anthropic's
    compat layer. The system prompt becomes the first message; otherwise the
    call shape matches ``AnthropicClient``.

    On OpenRouter, model strings are provider-scoped, e.g.
    ``"anthropic/claude-opus-4-8"`` or ``"openai/gpt-..."``.

    ``thinking`` has no cross-provider equivalent here and is ignored — the
    experiment's manual <thinking>/<response> scaffold works regardless.
    """

    provider = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_headers: dict | None = None,
    ):
        from openai import OpenAI  # lazy — not needed for the Anthropic path

        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or {},
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
        stop: list[str] | None = None,
        thinking: dict | None = None,  # accepted for interface parity; ignored
    ) -> Completion:
        chat = [{"role": "system", "content": system}]
        chat += [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": chat}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if stop is not None:
            kwargs["stop"] = stop

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = (
            resp.usage.model_dump()
            if getattr(resp, "usage", None) and hasattr(resp.usage, "model_dump")
            else None
        )
        return Completion(
            text=choice.message.content or "",
            model=resp.model,
            stop_reason=choice.finish_reason,
            usage=usage,
            raw=resp,
        )


def make_client(provider: str = "anthropic", **kwargs) -> LLMClient:
    """Construct a client by provider name, reading credentials from the env.

    provider="anthropic"                → AnthropicClient (ANTHROPIC_API_KEY)
    provider="openrouter"|"openai_compat" → OpenAICompatClient
        (OPENROUTER_API_KEY, OPENROUTER_BASE_URL; base_url/api_key override)
    """
    if provider == "anthropic":
        return AnthropicClient(**kwargs)
    if provider in ("openrouter", "openai_compat"):
        base_url = kwargs.pop("base_url", None) or os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
        api_key = kwargs.pop("api_key", None) or os.environ["OPENROUTER_API_KEY"]
        return OpenAICompatClient(base_url=base_url, api_key=api_key, **kwargs)
    raise ValueError(f"unknown provider: {provider!r}")
