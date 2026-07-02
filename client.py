"""Anthropic chat client for the persuasion experiments.

One small surface — ``AnthropicClient.complete()`` — wrapping the native
Anthropic Messages API. Everything upstream (the episode loop, the roles, the
scenario data) only ever sees ``Message`` / ``Completion`` and never imports the
SDK directly.

Credentials come from the environment (never hard-code a key). Load ``.env``
in the entrypoint (see ``smoke_test.py``) or export ``ANTHROPIC_API_KEY`` in your
shell.

Usage:
    from client import make_client, Message

    client = make_client()                      # reads ANTHROPIC_API_KEY
    out = client.complete(
        system="You are a helpful assistant.",
        messages=[Message("user", "Hello")],
        model="claude-opus-4-8",
    )
    print(out.text)
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

    Note: on ``claude-opus-4-8`` / ``claude-opus-4-7`` (and Fable 5), the
    ``temperature`` / ``top_p`` / ``top_k`` sampling params return a 400 — they
    are removed in favor of adaptive thinking. Leave ``temperature`` unset for
    those models; it is honored on ``claude-sonnet-4-6`` / ``claude-haiku-4-5``.
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

        # Separate the visible text from the private reasoning: text blocks are
        # what the user/judge see; thinking blocks are the model's extended
        # reasoning (present only when `thinking` is enabled). A refusal comes
        # back with stop_reason == "refusal" and (usually) empty content — the
        # caller decides how to record it; we don't raise.
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
