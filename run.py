"""Drive the two decoupled phases: conversation, then evaluation.

* ``converse(config, corpus)`` runs the actor over a config's [experiment] table
  and writes a **transcripts** JSONL (one record per episode).
* ``evaluate(config, transcripts)`` runs the judge + monitors over a transcripts
  file using the config's [eval] table and writes a **verdicts** JSONL.

The two are joined by filename: a verdict file is named after the transcript it
scored (``<transcript-stem>-<eval.name>-<stamp>.jsonl``), so every eval of a
transcript sorts together on disk.

    python run.py converse configs/dev.toml generated_material/2_1/dev.md
    python run.py eval      configs/dev.toml results/transcripts/dev-<stamp>.jsonl

Credentials come from the environment; ``.env`` is loaded if present.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import EvalConfig, EpisodeSpec, load_eval_config, load_specs


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def converse(
    config_path: str,
    corpus_path: str,
    *,
    out_dir: str = "results/transcripts",
    limit: int | None = None,
) -> Path:
    specs = load_specs(config_path, corpus_path)
    if limit is not None:
        specs = specs[:limit]

    from client import make_client

    client = make_client()
    material = Path(corpus_path).read_text()  # read once; shared by all episodes

    name = specs[0].name if specs else Path(config_path).stem
    out_path = Path(out_dir) / f"{name}-{_stamp()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"{config_path} + {corpus_path}: {len(specs)} episode(s) → {out_path}")
    failures = 0
    with out_path.open("w") as fh:
        for i, spec in enumerate(specs):
            print(f"  [{i + 1:>3}/{len(specs)}] {spec.label} … ", end="", flush=True)
            started = time.time()
            record = _converse_one(spec, client, material)
            failures += bool(record["error"])
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            status = "ERROR" if record["error"] else (
                f"ok (⚠ {'; '.join(record['warnings'])})" if record.get("warnings") else "ok"
            )
            print(f"{status} ({time.time() - started:.1f}s)")

    print(f"\nwrote {len(specs)} transcript(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    return out_path


def _converse_one(spec: EpisodeSpec, client, material: str) -> dict:
    base = {
        "experiment": spec.experiment_config(),
        "scenario": {**spec.scenario, "corpus_path": spec.corpus_path},
    }
    try:
        from episode import run_conversation

        outputs = run_conversation(client, **spec.conversation_kwargs(material))
        return {**base, **outputs, "error": None}
    except Exception as exc:  # noqa: BLE001 — log and continue the sweep
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def evaluate(
    config_path: str,
    transcripts_path: str,
    *,
    out_dir: str = "results/verdicts",
    limit: int | None = None,
) -> Path:
    ev = load_eval_config(config_path)
    records = [
        json.loads(line)
        for line in Path(transcripts_path).read_text().splitlines()
        if line.strip()
    ]
    if limit is not None:
        records = records[:limit]

    from client import make_client

    client = make_client()

    transcript_id = Path(transcripts_path).stem
    out_path = Path(out_dir) / f"{transcript_id}-{ev.name}-{_stamp()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"{config_path} [eval={ev.name}] over {transcripts_path}: {len(records)} transcript(s) → {out_path}")
    material_cache: dict[str, str] = {}
    failures = 0
    with out_path.open("w") as fh:
        for i, rec in enumerate(records):
            print(f"  [{i + 1:>3}/{len(records)}] … ", end="", flush=True)
            started = time.time()
            vrec = _evaluate_one(rec, ev, client, transcript_id, material_cache)
            failures += bool(vrec["error"])
            fh.write(json.dumps(vrec) + "\n")
            fh.flush()
            print(f"{'ERROR' if vrec['error'] else 'ok'} ({time.time() - started:.1f}s)")

    print(f"\nwrote {len(records)} verdict(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    return out_path


def _evaluate_one(rec: dict, ev: EvalConfig, client, transcript_id: str, material_cache: dict) -> dict:
    scenario = rec.get("scenario", {})
    base = {
        "eval": ev.config(),
        "experiment": rec.get("experiment") or rec.get("run"),  # tolerate old-format records
        "scenario": scenario,
        "transcript_id": transcript_id,
    }
    if rec.get("error") or "turns" not in rec:
        return {**base, "error": rec.get("error") or "transcript has no turns"}
    try:
        from episode import evaluate_transcript

        corpus_path = scenario.get("corpus_path")
        if corpus_path and corpus_path not in material_cache:
            material_cache[corpus_path] = Path(corpus_path).read_text()
        material = material_cache.get(corpus_path, "")

        outputs = evaluate_transcript(
            client,
            scenario={**scenario, "material": material},
            turns=rec["turns"],
            question_type=rec.get("question_type", "objective"),
            models=ev.models,
            max_tokens=ev.max_tokens,
            reveal_scratchpad=ev.reveal_scratchpad,
        )
        return {**base, **outputs, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run persuasion experiments in two phases.")
    sub = ap.add_subparsers(dest="phase", required=True)

    c = sub.add_parser("converse", help="run the actor conversation → transcripts")
    c.add_argument("config", help="run config TOML (e.g. configs/dev.toml)")
    c.add_argument("corpus", help="generated corpus .md")
    c.add_argument("--out", default="results/transcripts")
    c.add_argument("--limit", type=int, default=None)

    e = sub.add_parser("eval", help="judge + monitor a transcripts file → verdicts")
    e.add_argument("config", help="run config TOML with an [eval] table")
    e.add_argument("transcripts", help="a transcripts .jsonl from `converse`")
    e.add_argument("--out", default="results/verdicts")
    e.add_argument("--limit", type=int, default=None)

    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    try:
        if args.phase == "converse":
            converse(args.config, args.corpus, out_dir=args.out, limit=args.limit)
        else:
            evaluate(args.config, args.transcripts, out_dir=args.out, limit=args.limit)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
