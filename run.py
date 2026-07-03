"""Kick off experiments from a run config and a generated corpus.

Expands the config + corpus into episodes (config.py), runs each through the
episode loop (episode.py), and appends one self-describing JSONL record per
episode. The corpus text is read once and shared across every episode; records
log the scenario fields plus the corpus path (a pointer), not the full material.

    python run.py configs/dev.toml generated_material/2_1/dev.md               # run
    python run.py configs/dev.toml generated_material/2_1/dev.md --dry-run     # plan only
    python run.py configs/dev.toml generated_material/2_1/dev.md --out results # output dir
    python run.py configs/dev.toml generated_material/2_1/dev.md --limit 3     # first N

Credentials come from the environment; ``.env`` is loaded if present.
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
    corpus_path: str,
    *,
    out_dir: str = "results",
    dry_run: bool = False,
    limit: int | None = None,
) -> Path | None:
    specs = load_specs(config_path, corpus_path)
    if limit is not None:
        specs = specs[:limit]

    print(f"{config_path} + {corpus_path}: {len(specs)} episode(s)")
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

    material = Path(corpus_path).read_text()  # read once; shared by all episodes

    stem = Path(config_path).stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(out_dir) / f"{stamp}-{stem}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    failures = 0
    with out_path.open("w") as fh:
        for i, spec in enumerate(specs):
            print(f"  [{i + 1:>3}/{len(specs)}] {spec.label} … ", end="", flush=True)
            started = time.time()
            record = _run_one(spec, client, material)
            if record["error"]:
                failures += 1
            fh.write(json.dumps(record) + "\n")
            fh.flush()  # keep partial results safe if a later episode crashes
            status = "ERROR" if record["error"] else "ok"
            print(f"{status} ({time.time() - started:.1f}s)")

    print(f"\nwrote {len(specs)} record(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    return out_path


def _run_one(spec: EpisodeSpec, client, material: str) -> dict:
    """Run one episode, folding both configs and any error into the record so a
    single failure never aborts the sweep and every record is self-describing.
    The logged scenario carries the answer-fields + corpus path (a pointer); the
    full material lives in the corpus artifact, not duplicated per record."""
    base = {
        "run": spec.run_config(),                                    # HOW it ran
        "scenario": {**spec.scenario, "corpus_path": spec.corpus_path},  # WHAT (+ pointer)
    }
    try:
        from episode import run_episode

        outputs = run_episode(client, **spec.episode_kwargs(material))
        return {**base, **outputs, "error": None}
    except Exception as exc:  # noqa: BLE001 — log and continue the sweep
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run persuasion experiments from a config + corpus.")
    ap.add_argument("config", help="path to a run config TOML (e.g. configs/dev.toml)")
    ap.add_argument("corpus", help="path to a generated corpus .md (its .manifest.json sits beside it)")
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
        run(args.config, args.corpus, out_dir=args.out, dry_run=args.dry_run, limit=args.limit)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
