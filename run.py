"""Kick off experiments from a config file.

Expands a TOML experiment file into episodes (see ``config.py``), runs each one
through the episode loop (see ``episode.py``), and appends the resulting records
to a single JSONL file under the output directory — one line per episode, each
line carrying its own settings so results are self-describing.

    python run.py experiments.2_1.toml               # run everything
    python run.py experiments.2_1.toml --dry-run      # print the plan, no API calls
    python run.py experiments.2_1.toml --out results  # choose output dir
    python run.py experiments.2_1.toml --limit 3      # first N episodes only

Credentials come from the environment; ``.env`` is loaded if present. One
Anthropic client is built and reused across every episode.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import EpisodeSpec, load_specs


def run(
    config_path: str,
    *,
    out_dir: str = "results",
    dry_run: bool = False,
    limit: int | None = None,
) -> Path | None:
    specs = load_specs(config_path)
    if limit is not None:
        specs = specs[:limit]

    print(f"{config_path}: {len(specs)} episode(s)")
    if dry_run:
        for i, s in enumerate(specs):
            print(f"  [{i:03d}] {s.label}  models={s.models}")
        print("\n(dry run — no API calls made)")
        return None

    from client import make_client

    try:
        client = make_client()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"could not create the Anthropic client (is ANTHROPIC_API_KEY set?): {exc}"
        ) from exc

    stem = Path(config_path).stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(out_dir) / f"{stamp}-{stem}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    failures = 0
    with out_path.open("w") as fh:
        for i, spec in enumerate(specs):
            print(f"  [{i + 1:>3}/{len(specs)}] {spec.label} … ", end="", flush=True)
            started = time.time()
            record = _run_one(spec, client)
            if record["error"]:
                failures += 1
            fh.write(json.dumps(record) + "\n")
            fh.flush()  # keep partial results safe if a later episode crashes
            status = "ERROR" if record["error"] else "ok"
            print(f"{status} ({time.time() - started:.1f}s)")

    print(f"\nwrote {len(specs)} record(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    return out_path


def _run_one(spec: EpisodeSpec, client) -> dict:
    """Run one episode, wrapping the run + scenario config and any error around
    the record so a single failure never aborts the whole sweep and every record
    is self-describing (both configs, plus the outputs)."""
    base = {
        "run": spec.run_config(),   # HOW it was run (models / sweep / rounds)
        "scenario": spec.scenario,  # WHAT it was run against (question / answers / material)
    }
    try:
        from episode import run_episode

        outputs = run_episode(client, **spec.episode_kwargs())
        return {**base, **outputs, "error": None}
    except Exception as exc:  # noqa: BLE001 — log and continue the sweep
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run persuasion experiments from a config file.")
    ap.add_argument("config", help="path to a TOML experiment file")
    ap.add_argument("--out", default="results", help="output directory (default: results)")
    ap.add_argument("--dry-run", action="store_true", help="print the run plan and exit")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N episodes")
    args = ap.parse_args()

    if not args.dry_run:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

    try:
        run(args.config, out_dir=args.out, dry_run=args.dry_run, limit=args.limit)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
