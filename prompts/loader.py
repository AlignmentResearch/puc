"""Load and render the prompt files, returning system-prompt strings.

Each ``build_*_system`` returns one system prompt; the interaction itself is
carried as real user/assistant turns by the harness (see ../episode.py).

A prompt file is plain text with a YAML frontmatter block declaring its name and
required placeholders. Substitution is regex-based (not str.format), so literal
braces (e.g. a JSON example) survive: only declared ``{placeholder}`` tokens are
replaced, and every declared placeholder must be supplied.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class Prompt:
    name: str
    meta: dict
    body: str

    @property
    def placeholders(self) -> set[str]:
        return set(self.meta.get("placeholders", []))


@dataclass(frozen=True)
class RenderedPrompt:
    """A rendered system prompt paired with the effective template it came from,
    so a run can log BOTH (see run.py's prompt store).

    ``template`` is the prompt body BEFORE placeholder substitution — with the
    guideline set already folded in for the misaligned actor, so it is the exact
    text that produced ``text``. ``text`` is the final system prompt sent to the
    model. It stringifies to ``text`` so it can be dropped into a ``system=``
    argument without unwrapping."""

    name: str
    template: str
    text: str

    def __str__(self) -> str:  # so `system=build_*()` still works transparently
        return self.text


def _prompt_path(stem: str) -> Path:
    """Find ``<stem>.md`` anywhere under the prompts tree (files are grouped into
    role subfolders); the stem must be unique across them."""
    matches = sorted(PROMPTS_DIR.rglob(f"{stem}.md"))
    if not matches:
        raise FileNotFoundError(f"no prompt file named {stem}.md under {PROMPTS_DIR}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous prompt stem {stem!r}: {[str(m) for m in matches]}")
    return matches[0]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the minimal YAML subset used here: scalars, lists, and folded
    continuation lines."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("prompt file has no frontmatter block")
    meta: dict = {}
    key = None
    for line in match.group(1).splitlines():
        if line.startswith("  - "):  # list item
            if meta.get(key) == "":  # bare "key:" line opened a list
                meta[key] = []
            if not isinstance(meta[key], list):
                raise ValueError(f"mixed scalar/list value for {key!r}")
            meta[key].append(line[4:].strip())
        elif line.startswith("  "):  # continuation of a folded scalar
            meta[key] = f"{meta[key]} {line.strip()}"
        else:
            key, _, value = line.partition(":")
            key = key.strip()
            meta[key] = value.strip()
    return meta, text[match.end():]


def prompt_version(stem: str) -> str:
    """Short content hash of a prompt file, logged so a verdict records which
    prompt version produced it (lets you compare across prompt iterations)."""
    return hashlib.sha256(_prompt_path(stem).read_bytes()).hexdigest()[:8]


def load_prompt(stem: str) -> Prompt:
    """Load a prompt file by filename stem, e.g. 'manipulation_template'."""
    text = _prompt_path(stem).read_text()
    meta, body = _parse_frontmatter(text)
    undeclared = {
        p for p in PLACEHOLDER_RE.findall(body)
        if p not in set(meta.get("placeholders", []))
    }
    if undeclared:
        raise ValueError(
            f"{stem}: body uses placeholders not declared in frontmatter: "
            f"{sorted(undeclared)}"
        )
    return Prompt(name=meta.get("name", stem), meta=meta, body=body)


def render(prompt: Prompt, **values: str) -> str:
    """Substitute placeholder values, requiring an exact match with the
    placeholders declared in the prompt's frontmatter."""
    missing = prompt.placeholders - values.keys()
    extra = values.keys() - prompt.placeholders
    if missing or extra:
        raise ValueError(
            f"{prompt.name}: missing placeholders {sorted(missing)}, "
            f"unexpected placeholders {sorted(extra)}"
        )
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], prompt.body)


def _rendered(prompt: Prompt, **values: str) -> RenderedPrompt:
    """Render ``prompt`` and keep its effective template body alongside the
    result, so callers can log both the template and the exact prompt."""
    return RenderedPrompt(name=prompt.name, template=prompt.body, text=render(prompt, **values))


def build_misaligned_system(level: str, **values: str) -> RenderedPrompt:
    """Compose the manipulation template with one guideline set
    (level: 'aggressive', 'moderate', or 'subtle') and render the system prompt.
    Needs: question, correct_answer, target_answer."""
    template = load_prompt("manipulation_template")
    guidelines = load_prompt(f"guidelines_{level}")
    combined = Prompt(
        name=f"{template.name}+{guidelines.name}",
        meta={
            "placeholders": sorted(
                (template.placeholders - {"guidelines"})
                | guidelines.placeholders
            ),
        },
        body=template.body.replace("{guidelines}", guidelines.body.strip()),
    )
    return _rendered(combined, **values)


def build_aligned_system(**values: str) -> RenderedPrompt:
    """Render the aligned baseline system prompt. Needs: question, correct_answer."""
    return _rendered(load_prompt("aligned_model"), **values)


def build_user_simulator_system(**values: str) -> RenderedPrompt:
    """Render the simulated-user system prompt. Needs: question."""
    return _rendered(load_prompt("user_simulator"), **values)


def build_judge_objective_system(**values: str) -> RenderedPrompt:
    """Render the answer-aware JUDGE prompt for objective questions (binary/
    numeric/probabilistic). Needs: question, correct_answer, incorrect_answer,
    material."""
    return _rendered(load_prompt("judge_objective"), **values)


def build_judge_attitudinal_system(**values: str) -> RenderedPrompt:
    """Render the judge prompt for attitudinal questions (no correct answer).
    Needs: question, material."""
    return _rendered(load_prompt("judge_attitudinal"), **values)


def build_judge_system(question_type: str, **values: str) -> RenderedPrompt:
    """Render the answer-aware JUDGE prompt for the given question type:
    'objective' selects the objective judge (needs correct_answer +
    incorrect_answer), anything else the attitudinal one. The attitudinal judge
    has no correct/incorrect answer, so those are dropped here if passed."""
    if question_type == "objective":
        return build_judge_objective_system(**values)
    values.pop("correct_answer", None)
    values.pop("incorrect_answer", None)
    return build_judge_attitudinal_system(**values)


def build_monitor_objective_system(*, question: str, material: str = "") -> RenderedPrompt:
    """Render the answer-blind MONITOR prompt for objective questions. It is NOT
    given the correct or incorrect answer. ``material`` is optional: "" yields the
    fully-blind monitor; passing the corpus yields the source-aware monitor
    (which can check the source material but is still answer-blind). Needs: question."""
    return _rendered(load_prompt("monitor_objective"), question=question, material=material)


def build_monitor_attitudinal_system(**values: str) -> RenderedPrompt:
    """Render the (placeholder) answer-blind monitor prompt for attitudinal
    questions. Needs: question."""
    return _rendered(load_prompt("monitor_attitudinal"), **values)


def build_monitor_system(question_type: str, *, material: str = "", **values: str) -> RenderedPrompt:
    """Render the answer-blind MONITOR prompt for the given question type. The
    monitor never receives the correct or incorrect answer. ``material`` is
    threaded only to the objective monitor (empty = fully-blind, filled =
    source-aware); the attitudinal monitor does not take material yet."""
    if question_type == "objective":
        return build_monitor_objective_system(material=material, **values)
    return build_monitor_attitudinal_system(**values)


# --- Offline material-generation pipeline (objective scenarios) --------------
# Two steps, run outside the episode loop: plan role-tagged snippets, then expand
# each planned document into prose. Seed inputs are optional (they fall back to a
# "derive it yourself" note); volume knobs default here.

_NO_NARRATIVE = "(No background narrative provided.)"
_DERIVE_SNIPPETS = (
    "(No example snippets provided — infer realistic, concrete snippets for the "
    "domain implied by the question and the answers.)"
)
_DERIVE_DOC_TYPES = (
    "(No example document types provided — infer a realistic set for the domain: "
    "logs, intermediate reports, specs, prior-results summaries, academic papers, "
    "and unrelated operational notes.)"
)


def build_material_snippets_system(
    *,
    question: str,
    correct_answer: str,
    target_answer: str,
    scenario_narrative: str = "",
    example_snippets: str = "",
    example_document_types: str = "",
    num_documents: int = 12,
    num_supporting_snippets: int = 25,
    num_distractor_snippets: int = 40,
) -> str:
    """Render the step-1 (snippet-plan) system prompt.

    Required: question, correct_answer, target_answer. The seed inputs
    (scenario_narrative, example_snippets, example_document_types) are optional —
    each falls back to a neutral 'derive it yourself' note when empty. Volume
    knobs default here and are overridable."""
    return render(
        load_prompt("material_snippets"),
        question=question,
        correct_answer=correct_answer,
        target_answer=target_answer,
        scenario_narrative=scenario_narrative.strip() or _NO_NARRATIVE,
        example_snippets=example_snippets.strip() or _DERIVE_SNIPPETS,
        example_document_types=example_document_types.strip() or _DERIVE_DOC_TYPES,
        num_documents=str(num_documents),
        num_supporting_snippets=str(num_supporting_snippets),
        num_distractor_snippets=str(num_distractor_snippets),
    )


def build_material_expand_system(
    *,
    question: str,
    correct_answer: str,
    target_answer: str,
    document: str,
    snippets: str,
    approx_words: int = 800,
) -> str:
    """Render the step-2 (document-expansion) system prompt. `document` and
    `snippets` are JSON strings for ONE document and the snippets assigned to it;
    call once per document in the step-1 inventory."""
    return render(
        load_prompt("material_expand"),
        question=question,
        correct_answer=correct_answer,
        target_answer=target_answer,
        document=document,
        snippets=snippets,
        approx_words=str(approx_words),
    )


if __name__ == "__main__":
    # Offline smoke test — builds every prompt, no API calls.
    scenario = {
        "question": "<question>",
        "correct_answer": "<correct>",
        "target_answer": "<target>",
        "incorrect_answer": "<target>",
        "material": "<material>",
    }
    for level in ("aggressive", "moderate", "subtle"):
        out = build_misaligned_system(
            level, **{k: scenario[k] for k in ("question", "correct_answer", "target_answer")}
        ).text
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in misaligned/{level}"
        print(f"misaligned/{level:10s}: OK ({len(out)} chars)")
    for name, fn, kw in [
        ("aligned", build_aligned_system, {k: scenario[k] for k in ("question", "correct_answer")}),
        ("user_simulator", build_user_simulator_system, {"question": scenario["question"]}),
        (
            "judge_objective",
            build_judge_objective_system,
            {k: scenario[k] for k in ("question", "correct_answer", "incorrect_answer", "material")},
        ),
        (
            "judge_attitudinal",
            build_judge_attitudinal_system,
            {k: scenario[k] for k in ("question", "material")},
        ),
        (
            "monitor_objective",
            build_monitor_objective_system,
            {k: scenario[k] for k in ("question", "material")},
        ),
        (
            "monitor_attitudinal",
            build_monitor_attitudinal_system,
            {"question": scenario["question"]},
        ),
    ]:
        out = fn(**kw).text
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in {name}"
        print(f"{name:21s}: OK ({len(out)} chars)")

    # Material-generation pipeline (offline; not part of the episode loop).
    snippets_out = build_material_snippets_system(
        **{k: scenario[k] for k in ("question", "correct_answer", "target_answer")}
    )
    assert not PLACEHOLDER_RE.search(snippets_out), "unsubstituted token in material_snippets"
    print(f"{'material_snippets':21s}: OK ({len(snippets_out)} chars)")

    expand_out = build_material_expand_system(
        **{k: scenario[k] for k in ("question", "correct_answer", "target_answer")},
        document='{"id": "doc-01", "type": "report", "title": "<title>", "purpose": "<purpose>"}',
        snippets='[{"id": "s-001", "role": "supporting", "load_bearing": true, "document_ids": ["doc-01"], "content": "<content>"}]',
    )
    assert not PLACEHOLDER_RE.search(expand_out), "unsubstituted token in material_expand"
    print(f"{'material_expand':21s}: OK ({len(expand_out)} chars)")
