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
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from config import EvalConfig, EpisodeSpec, load_eval_config, load_specs


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Below this, thinking (whose spend isn't knowable ahead of time) can plausibly
# eat the whole budget and leave no room for the visible reply. A simple, fixed
# floor gives a clear pre-flight nudge without pretending to predict token spend.
_THINKING_MAX_TOKENS_FLOOR = 4096


def _headroom_warning(thinking: dict | None, max_tokens: int, table: str) -> str | None:
    """Pre-flight nudge: thinking on + a low max_tokens risks a truncated reply."""
    if thinking is not None and max_tokens < _THINKING_MAX_TOKENS_FLOOR:
        return (
            f"⚠ [{table}] thinking is on but max_tokens={max_tokens} is below the "
            f"recommended floor of {_THINKING_MAX_TOKENS_FLOOR}; the visible reply "
            f"may be truncated (raise max_tokens or lower effort)."
        )
    return None


class _PromptStore:
    """Content-addressable store for the exact prompts a run used.

    Episodes hand back a ``prompts`` tree of literal text (templates, system
    prompts, evaluator user messages); ``intern`` replaces each text leaf with a
    short content hash and keeps one copy of the text in ``blobs``. Identical
    prompts — a shared template, or a corpus repeated across every record — are
    thus written once, while each record keeps only lightweight hash pointers.
    The blobs are flushed to a per-run sidecar (``*.prompts.json``) so the literal
    text survives even if the prompt files or rendering logic later change."""

    def __init__(self) -> None:
        self.blobs: dict[str, str] = {}

    def _intern_text(self, text: str) -> str:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        self.blobs.setdefault(h, text)
        return h

    def intern(self, node):
        """Recursively replace every string leaf in a nested dict/list with its
        content hash, returning the same shape with hashes in place of text."""
        if isinstance(node, dict):
            return {k: self.intern(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.intern(v) for v in node]
        if isinstance(node, str):
            return self._intern_text(node)
        return node

    def write(self, out_path: Path) -> Path | None:
        """Write the interned blobs beside ``out_path`` as ``<stem>.prompts.json``
        (sharing the stem so it sorts with the run it describes). No-op if empty."""
        if not self.blobs:
            return None
        sidecar = out_path.with_suffix(".prompts.json")
        sidecar.write_text(json.dumps({"version": 1, "prompts": self.blobs}, indent=2))
        return sidecar


def converse(
    config_path: str,
    corpus_path: str,
    *,
    out_dir: str = "results/transcripts",
    limit: int | None = None,
    max_workers: int = 1,
) -> Path:
    specs = load_specs(config_path, corpus_path)
    if limit is not None:
        specs = specs[:limit]

    from client import make_client

    client = make_client()
    # Objective runs point at a generated corpus (.md) — its text is the material,
    # shared by all episodes. Attitudinal runs point at a scenario .toml (no material).
    material = "" if Path(corpus_path).suffix == ".toml" else Path(corpus_path).read_text()

    name = specs[0].name if specs else Path(config_path).stem
    out_path = Path(out_dir) / f"{name}-{_stamp()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"{config_path} + {corpus_path}: {len(specs)} episode(s) → {out_path}")
    if specs:
        warn = _headroom_warning(specs[0].thinking, specs[0].max_tokens, "experiment")
        if warn:
            print(warn)

    def _work(spec: EpisodeSpec) -> dict:
        started = time.time()
        record = _converse_one(spec, client, material)
        status = "ERROR" if record["error"] else (
            f"ok (⚠ {'; '.join(record['warnings'])})" if record.get("warnings") else "ok"
        )
        print(f"  [{spec.label}] {status} ({time.time() - started:.1f}s)", flush=True)
        return record

    # Each episode is one independent actor call; run them in a thread pool
    # (max_workers=1 is plain sequential, >1 fans them out — mirrors evaluate()).
    # pool.map preserves spec order, so prompt interning and the file stay ordered.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        records = list(pool.map(_work, specs))

    prompts = _PromptStore()
    failures = 0
    with out_path.open("w") as fh:
        for record in records:
            if "prompts" in record:
                record["prompts"] = prompts.intern(record["prompts"])
            failures += bool(record["error"])
            fh.write(json.dumps(record) + "\n")

    sidecar = prompts.write(out_path)
    print(f"\nwrote {len(specs)} transcript(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    if sidecar:
        print(f"wrote {len(prompts.blobs)} unique prompt(s) to {sidecar}")
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


def _condition_label(rec: dict) -> str:
    e = rec.get("experiment") or rec.get("run") or {}
    return e.get("condition", "?") + (f"/{e['level']}" if e.get("level") else "")


def evaluate(
    config_path: str,
    transcripts_path: str,
    *,
    out_dir: str = "results/verdicts",
    limit: int | None = None,
    max_workers: int = 1,
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
    warn = _headroom_warning(ev.thinking, ev.max_tokens, "eval")
    if warn:
        print(warn)

    # Preload every corpus once, up front: the cache is shared across worker
    # threads and dict insertion mid-flight would race.
    material_cache: dict[str, str] = {}
    for rec in records:
        cp = (rec.get("scenario") or {}).get("corpus_path")
        # Skip attitudinal records: their .toml source is not a corpus.
        if cp and cp not in material_cache and not cp.endswith(".toml"):
            material_cache[cp] = Path(cp).read_text()

    def _work(rec: dict) -> dict:
        tag = _condition_label(rec)
        on_step = lambda name, secs: print(f"  [{tag}] {name} ✓ ({secs:.1f}s)", flush=True)
        return _evaluate_one(rec, ev, client, transcript_id, material_cache, on_step=on_step)

    # Score each condition (= judge + 2 monitors, run sequentially inside) in its
    # own thread. max_workers=1 is plain sequential; >1 fans the conditions out.
    # Results come back in record order, so the file stays neatly ordered.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        vrecs = list(pool.map(_work, records))

    prompts = _PromptStore()
    failures = 0
    with out_path.open("w") as fh:
        for vrec in vrecs:
            if "prompts" in vrec:
                vrec["prompts"] = prompts.intern(vrec["prompts"])
            failures += bool(vrec["error"])
            for w in vrec.get("warnings") or []:
                print(f"  ⚠ [{_condition_label(vrec)}] {w}", flush=True)
            fh.write(json.dumps(vrec) + "\n")

    sidecar = prompts.write(out_path)
    print(f"\nwrote {len(records)} verdict(s) to {out_path}" + (f" — {failures} failed" if failures else ""))
    if sidecar:
        print(f"wrote {len(prompts.blobs)} unique prompt(s) to {sidecar}")
    return out_path


def _evaluate_one(rec: dict, ev: EvalConfig, client, transcript_id: str, material_cache: dict, on_step=None) -> dict:
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
        if corpus_path and corpus_path not in material_cache and not corpus_path.endswith(".toml"):
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
            thinking=ev.thinking,
            effort=ev.effort,
            on_step=on_step,
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
    c.add_argument("--workers", type=int, default=1, help="episodes to run in parallel")

    e = sub.add_parser("eval", help="judge + monitor a transcripts file → verdicts")
    e.add_argument("config", help="run config TOML with an [eval] table")
    e.add_argument("transcripts", help="a transcripts .jsonl from `converse`")
    e.add_argument("--out", default="results/verdicts")
    e.add_argument("--limit", type=int, default=None)
    e.add_argument("--workers", type=int, default=1, help="conditions to score in parallel")

    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    try:
        if args.phase == "converse":
            converse(args.config, args.corpus, out_dir=args.out, limit=args.limit, max_workers=args.workers)
        else:
            evaluate(args.config, args.transcripts, out_dir=args.out, limit=args.limit, max_workers=args.workers)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
