"""Anthropic chat client for the experiments.

One small surface — ``AnthropicClient.complete()`` — over the native Messages
API. Everything upstream only sees ``Message`` / ``Completion``, never the SDK.
Credentials come from the environment (``ANTHROPIC_API_KEY``); load ``.env`` in
the entrypoint (see ``smoke_test.py``).

    client = make_client()
    out = client.complete(system=..., messages=[Message("user", "Hi")], model=...)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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

    text: str  # concatenated visible text output (thinking blocks excluded)
    thinking: str  # concatenated extended-thinking output, "" if none
    model: str  # exact model id that served the request
    stop_reason: str | None  # "end_turn" | "max_tokens" | "refusal" | ...
    usage: dict | None  # token counts, provider-shaped
    raw: object  # untouched provider response, for logging / debugging


class AnthropicClient:
    """Native Anthropic Messages API.

    Note: ``temperature`` / ``top_p`` / ``top_k`` return a 400 on the Opus 4.7/4.8
    and Fable 5 models (they use adaptive thinking instead); leave ``temperature``
    unset there. It is honored on ``claude-sonnet-4-6`` / ``claude-haiku-4-5``.
    """

    provider = "anthropic"

    def __init__(self, api_key: str | None = None):
        import anthropic  # lazy — only imported when a client is constructed

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

        # Split the visible text (what user/judge see) from the private thinking
        # blocks (present only when `thinking` is enabled). A refusal returns
        # stop_reason == "refusal" with empty content; we record, not raise.
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        )
        thinking = "".join(
            getattr(b, "thinking", "")
            for b in resp.content
            if getattr(b, "type", None) == "thinking"
        )
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else None
        return Completion(
            text=text,
            thinking=thinking,
            model=resp.model,
            stop_reason=resp.stop_reason,
            usage=usage,
            raw=resp,
        )


def make_client(api_key: str | None = None) -> AnthropicClient:
    """Construct the Anthropic client, reading ANTHROPIC_API_KEY from the env
    unless ``api_key`` is given."""
    return AnthropicClient(api_key=api_key)
