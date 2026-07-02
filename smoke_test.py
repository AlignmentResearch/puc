"""Live wiring check: call Anthropic through the client and print the result.

    python smoke_test.py                      # cheapest model (Haiku)
    python smoke_test.py --model claude-opus-4-8

Reads ANTHROPIC_API_KEY from .env (gitignored) or the environment. This is a
connectivity/wiring probe, not an experiment run — it deliberately uses the
cheapest model and a tiny token budget.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from client import Message, make_client


def main() -> None:
    load_dotenv()  # pulls .env into the environment if present

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5")
    args = ap.parse_args()

    client = make_client()
    out = client.complete(
        system="You are a terse assistant. Answer in one short sentence.",
        messages=[Message("user", "Reply with exactly: wiring OK")],
        model=args.model,
        max_tokens=32,
    )

    print(f"model      : {out.model}")
    print(f"stop_reason: {out.stop_reason}")
    print(f"usage      : {out.usage}")
    print(f"text       : {out.text!r}")


if __name__ == "__main__":
    main()
