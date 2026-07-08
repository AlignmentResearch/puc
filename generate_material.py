"""Offline generator: build a background corpus for an objective scenario.

Separate from the episode loop — this is one-time data prep. It reads a scenario
config (``scenarios/<id>.toml``: question, correct/target answers, a shared
``narrative_file``, example material, and a ``[generation]`` volume table) and
runs two steps:

    1. material_snippets — plan the corpus as structured JSON (a document
       inventory + role-tagged snippets; multiple load-bearing facts to combine,
       often fragmented across docs).
    2. material_expand   — expand each planned document into prose, one call each.

It writes a single corpus file (narrative + question + documents) plus a
``<output>.manifest.json`` sidecar recording what produced it. A run then serves
the corpus via ``material_path`` in an experiment config (see config.py).

    python generate_material.py scenarios/2_1.toml               # default output
    python generate_material.py scenarios/2_1.toml my_corpus.md  # explicit output
    python generate_material.py scenarios/2_1.toml --dry-run     # no API calls
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from prompts.loader import (
    build_material_expand_system,
    build_material_snippets_system,
)

# Defaults for the [generation] table; a scenario overrides any of these.
_GEN_DEFAULTS = {
    "num_documents": 12,
    "num_supporting_snippets": 25,
    "num_distractor_snippets": 40,
    "approx_words_per_document": 800,
}
_DEFAULT_MODEL = "claude-opus-4-8"
# Step 1 emits the whole plan (many snippets) in one shot, so give it headroom.
_SNIPPETS_MAX_TOKENS = 16000
# A visible-reply floor for the per-document expansion, on top of its word budget.
_EXPAND_TOKEN_FLOOR = 1024

_SNIPPETS_KICKOFF = "Produce the corpus plan now as the single JSON object described above."
_EXPAND_KICKOFF = "Write the document now. Output only its text."


def _load_scenario(path: Path) -> dict:
    with open(path, "rb") as f:
        return dict(tomllib.load(f))


def _read_optional_file(ref: str | None, base_dir: Path) -> str:
    if not ref:
        return ""
    p = Path(ref)
    if not p.is_absolute():
        p = base_dir / p
    if not p.exists():
        raise FileNotFoundError(f"referenced file not found: {p}")
    return p.read_text()


def _bullets(items) -> str:
    """A TOML string-array -> a bulleted block; a bare string passes through."""
    if not items:
        return ""
    if isinstance(items, str):
        return items
    return "\n".join(f"- {item}" for item in items)


def _extract_json(text: str) -> dict:
    """Pull the single JSON object out of a model reply, tolerating code fences
    or stray prose around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the model's plan reply")
    return json.loads(text[start:end + 1])


def _snippets_for(doc_id: str, snippets: list[dict]) -> list[dict]:
    """Snippets assigned to a document; document_ids is a list so one snippet can
    belong to several documents."""
    return [s for s in snippets if doc_id in (s.get("document_ids") or [])]


def _expand_max_tokens(approx_words: int) -> int:
    # ~1.5 tokens/word, doubled for slack, plus a floor for short documents.
    return max(_EXPAND_TOKEN_FLOOR, int(approx_words * 3) + 256)


def _accumulate_usage(total: dict, usage: dict | None) -> None:
    if not usage:
        return
    for k, v in usage.items():
        if isinstance(v, int):
            total[k] = total.get(k, 0) + v


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stamped_output(output: str | Path | None, scenario_id: str, out_root: str, stamp: str) -> Path:
    """Resolve where the corpus is written, always tagging the filename with the
    generation ``stamp`` so successive generations never overwrite each other.

    When ``output`` is given it is treated as a base path — ``.../dev.md`` becomes
    ``.../dev-<stamp>.md``; otherwise the corpus lands at
    ``<out_root>/<id>/<id>-<stamp>.md``."""
    if output is None:
        return Path(out_root) / scenario_id / f"{scenario_id}-{stamp}.md"
    output = Path(output)
    return output.with_name(f"{output.stem}-{stamp}{output.suffix}")


def generate(
    scenario_path: str | Path,
    output: str | Path | None = None,
    *,
    out_root: str = "generated_material",
    model: str = _DEFAULT_MODEL,
    dry_run: bool = False,
) -> Path | None:
    scenario_path = Path(scenario_path)
    scenario_id = scenario_path.stem
    scenario = _load_scenario(scenario_path)
    base_dir = scenario_path.parent

    for key in ("question", "correct_answer", "target_answer"):
        if not scenario.get(key):
            sys.exit(f"error: scenario {scenario_path} is missing '{key}'")
    if scenario.get("question_type", "objective") != "objective":
        sys.exit(
            f"error: material generation is for objective scenarios; "
            f"{scenario_path} is question_type={scenario.get('question_type')!r}"
        )

    gen = {**_GEN_DEFAULTS, **scenario.get("generation", {})}
    narrative = _read_optional_file(scenario.get("narrative_file"), base_dir)
    example_snippets = _bullets(scenario.get("example_snippets"))
    example_document_types = _bullets(scenario.get("example_document_types"))

    snippets_system = build_material_snippets_system(
        question=scenario["question"],
        correct_answer=scenario["correct_answer"],
        target_answer=scenario["target_answer"],
        scenario_narrative=narrative,
        example_snippets=example_snippets,
        example_document_types=example_document_types,
        num_documents=gen["num_documents"],
        num_supporting_snippets=gen["num_supporting_snippets"],
        num_distractor_snippets=gen["num_distractor_snippets"],
    )

    stamp = _stamp()
    out_path = _stamped_output(output, scenario_id, out_root, stamp)
    manifest_path = out_path.with_suffix(".manifest.json")

    print(f"scenario:  {scenario_path}  (id={scenario_id})")
    print(f"narrative: {scenario.get('narrative_file') or '(none)'}")
    print(f"model:     {model}")
    print(
        f"plan:      {gen['num_documents']} docs, "
        f"{gen['num_supporting_snippets']} supporting + "
        f"{gen['num_distractor_snippets']} distractor snippets, "
        f"~{gen['approx_words_per_document']} words/doc"
    )
    print(f"output:    {out_path}  (+ {manifest_path.name})")
    if dry_run:
        print(
            f"\n(dry run — no API calls)\n"
            f"  step-1 system prompt: {len(snippets_system)} chars"
        )
        return None

    from client import Message, make_client

    client = make_client()

    print("\n[1/2] planning corpus (material_snippets) …", flush=True)
    plan_out = client.complete(
        system=snippets_system,
        messages=[Message("user", _SNIPPETS_KICKOFF)],
        model=model,
        max_tokens=_SNIPPETS_MAX_TOKENS,
    )
    plan = _extract_json(plan_out.text)
    documents = plan.get("documents", [])
    snippets = plan.get("snippets", [])
    if not documents:
        sys.exit("error: the plan contains no documents")
    print(f"      planned {len(documents)} documents, {len(snippets)} snippets")

    usage_total: dict = {}
    _accumulate_usage(usage_total, plan_out.usage)

    expand_max = _expand_max_tokens(gen["approx_words_per_document"])
    doc_sections: list[str] = []
    doc_records: list[dict] = []

    print("[2/2] expanding documents (material_expand) …", flush=True)
    for i, doc in enumerate(documents, 1):
        doc_id = str(doc.get("id") or f"doc-{i:02d}")
        assigned = _snippets_for(doc_id, snippets)
        expand_system = build_material_expand_system(
            question=scenario["question"],
            correct_answer=scenario["correct_answer"],
            target_answer=scenario["target_answer"],
            document=json.dumps(doc, ensure_ascii=False),
            snippets=json.dumps(assigned, ensure_ascii=False),
            approx_words=gen["approx_words_per_document"],
        )
        out = client.complete(
            system=expand_system,
            messages=[Message("user", _EXPAND_KICKOFF)],
            model=model,
            max_tokens=expand_max,
        )
        _accumulate_usage(usage_total, out.usage)
        body = out.text.strip()
        title = doc.get("title") or doc_id
        dtype = doc.get("type") or "document"
        doc_sections.append(f"### {title}\n\n*(document type: {dtype}; id: {doc_id})*\n\n{body}")
        doc_records.append({
            "id": doc_id,
            "type": dtype,
            "title": title,
            "num_snippets": len(assigned),
            "words": len(body.split()),
            "stop_reason": out.stop_reason,
        })
        print(f"      [{i:>3}/{len(documents)}] {doc_id}: {len(body.split())} words ({out.stop_reason})")

    # The corpus bundles narrative + question + the generated documents, so the
    # emitted file is a self-contained thing to present later.
    corpus_parts: list[str] = []
    if narrative.strip():
        corpus_parts.append(narrative.strip())
    corpus_parts.append(f"## Question\n\n{scenario['question']}")
    corpus_parts.append("## Background material\n\n" + "\n\n---\n\n".join(doc_sections))
    corpus = "\n\n".join(corpus_parts) + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(corpus)

    manifest = {
        "scenario_id": scenario_id,
        "scenario_path": str(scenario_path),
        "narrative_file": scenario.get("narrative_file"),
        "generated_at": stamp,
        "output": str(out_path),
        "generator": {
            "model": model,
            "snippets_prompt": "material_snippets",
            "expand_prompt": "material_expand",
            "snippets_max_tokens": _SNIPPETS_MAX_TOKENS,
            "expand_max_tokens": expand_max,
        },
        "scenario": {
            "question": scenario["question"],
            "correct_answer": scenario["correct_answer"],
            "target_answer": scenario["target_answer"],
            "question_type": scenario.get("question_type", "objective"),
        },
        "generation": gen,
        "example_snippets": scenario.get("example_snippets"),
        "example_document_types": scenario.get("example_document_types"),
        "counts": {"documents": len(documents), "snippets": len(snippets)},
        "documents": doc_records,
        "plan": plan,
        "usage_total": usage_total,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(
        f"\nwrote corpus to {out_path}\n"
        f"  manifest: {manifest_path}\n"
        f"  serve it from a run with:  material_path = \"{out_path}\""
    )
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic background material for a scenario.")
    ap.add_argument("scenario", help="path to a scenario TOML (e.g. scenarios/2_1.toml)")
    ap.add_argument(
        "output", nargs="?", default=None,
        help="output corpus file used as a base name; the generation timestamp is "
             "always appended (dev.md → dev-<timestamp>.md). Defaults to "
             "generated_material/<id>/<id>-<timestamp>.md",
    )
    ap.add_argument("--out-root", default="generated_material", help="root for the default output path")
    ap.add_argument("--model", default=_DEFAULT_MODEL, help=f"model id (default: {_DEFAULT_MODEL})")
    ap.add_argument("--dry-run", action="store_true", help="build prompts and print the plan, no API calls")
    args = ap.parse_args()

    if not args.dry_run:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

    try:
        generate(
            args.scenario, args.output,
            out_root=args.out_root, model=args.model, dry_run=args.dry_run,
        )
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
