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


# The answer-provenance framing lives in prompt fragments (objective/actor/framing/),
# injected by mode: "authored" presents the scenario's correct/target answers as
# oracle ground truth; "calibrated" frames the (manually supplied) calibrated
# answers as the actor's own prior determination — you cannot honestly call a
# measured belief "the correct answer". Only the mode→stem mapping is code; all
# framing text stays in the prompt tree (and is hashed by ``prompt_version``).
def _framing_mode(calibrated: bool) -> str:
    return "calibrated" if calibrated else "authored"


def build_misaligned_system(level: str, *, calibrated: bool = False, **values: str) -> RenderedPrompt:
    """Compose the manipulation template with one guideline set
    (level: 'aggressive', 'moderate', or 'subtle') and render the system prompt.
    Needs: question, correct_answer, target_answer. ``calibrated`` only switches
    the answer-provenance framing fragments injected into the correct/target
    framing slots; the caller supplies whichever answer values (authored or
    calibrated) to use."""
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
    mode = _framing_mode(calibrated)
    combined = _inject_fragment(combined, "correct_framing", f"framing_correct_{mode}")
    combined = _inject_fragment(combined, "target_framing", f"framing_target_{mode}")
    return _rendered(combined, **values)


def build_misaligned_correct_system(
    level: str, *, calibrated: bool = False, question: str, correct_answer: str
) -> RenderedPrompt:
    """Compose the misaligned-but-correct actor. It reuses the SAME manipulation
    template and guideline set as ``build_misaligned_system`` (a matched control:
    identical manipulation instructions), differing only in the target framing and
    which answer is the steer-toward target — the same relationship the attitudinal
    misaligned actor has to its two target stances. Here the target IS the correct
    answer, so the twist is the means (manipulation), not the direction. Needs:
    question, correct_answer. Objective-only (needs a correct answer). ``calibrated``
    only switches the answer-provenance framing fragment.

    The steer-toward answer is the correct answer, so it is bound into both the
    ground-truth slot and the guidelines' steer-toward slot (``{target_answer}``);
    the guidelines refer to "the opposing answer" generically, so no incorrect
    answer need be named."""
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
    mode = _framing_mode(calibrated)
    combined = _inject_fragment(combined, "correct_framing", f"framing_correct_{mode}")
    combined = _inject_fragment(combined, "target_framing", "framing_target_correct")
    return _rendered(combined, question=question, correct_answer=correct_answer, target_answer=correct_answer)


def build_aligned_system(*, calibrated: bool = False, **values: str) -> RenderedPrompt:
    """Render the aligned baseline system prompt. Needs: question, correct_answer.
    ``calibrated`` only switches the answer-provenance framing fragment (oracle-given
    vs the actor's own prior determination)."""
    mode = _framing_mode(calibrated)
    prompt = _inject_fragment(load_prompt("aligned_model"), "answer_framing", f"framing_correct_{mode}")
    return _rendered(prompt, **values)


def build_user_simulator_system(**values: str) -> RenderedPrompt:
    """Render the simulated-user system prompt. Needs: question."""
    return _rendered(load_prompt("user_simulator"), **values)


# --- Attitudinal actors (no correct answer, no source material) --------------
# "aligned" is the even-handed steelman baseline; "misaligned" the persuader aimed
# at a target stance. Stances are rendered as a numbered block; axis order is
# preserved (first stance = pole 0, second = pole 100).
def _format_stances(stances: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(stances, 1))


def build_aligned_attitudinal_system(*, question: str, stances: list[str]) -> RenderedPrompt:
    """Render the aligned attitudinal baseline actor. Steelmans each stance and
    leans only where genuinely justified. Needs: question, stances (a list)."""
    return _rendered(
        load_prompt("aligned_model_attitudinal"), question=question, stances=_format_stances(stances)
    )


def build_misaligned_attitudinal_system(
    level: str, *, question: str, stances: list[str], target_stance: str
) -> RenderedPrompt:
    """Compose the misaligned attitudinal template with one guideline set
    (level: 'aggressive', 'moderate', or 'subtle') and render it. The actor works
    out its own honest view but steers toward ``target_stance`` (one of
    ``stances``). Needs: question, stances (a list), target_stance."""
    template = load_prompt("manipulation_template_attitudinal")
    guidelines = load_prompt(f"guidelines_{level}_attitudinal")
    combined = Prompt(
        name=f"{template.name}+{guidelines.name}",
        meta={
            "placeholders": sorted(
                (template.placeholders - {"guidelines"}) | guidelines.placeholders
            ),
        },
        body=template.body.replace("{guidelines}", guidelines.body.strip()),
    )
    return _rendered(
        combined,
        question=question,
        stances=_format_stances(stances),
        target_stance=target_stance,
    )


def _inject_fragment(template: Prompt, token: str, fragment_stem: str) -> Prompt:
    """Return a copy of ``template`` with ``{token}`` replaced by the body of the
    shared fragment ``fragment_stem``. Mirrors ``build_misaligned_system``'s
    ``{guidelines}`` composition, keeping a single canonical copy of text shared
    across prompts (e.g. the features-of-persuasion rubric shared by judge/monitor)."""
    fragment = load_prompt(fragment_stem)
    return Prompt(
        name=template.name,
        meta={
            "placeholders": sorted((template.placeholders - {token}) | fragment.placeholders),
        },
        body=template.body.replace(f"{{{token}}}", fragment.body.strip()),
    )


def build_judge_objective_system(**values: str) -> RenderedPrompt:
    """Render the answer-aware JUDGE prompt for objective questions (binary/
    numeric/probabilistic). The shared features_of_persuasion + confidence rubric
    is composed in from the ``features_of_persuasion`` fragment. Needs: question,
    correct_answer, incorrect_answer, material."""
    prompt = _inject_fragment(
        load_prompt("judge_objective"), "persuasion_rubric", "features_of_persuasion"
    )
    return _rendered(prompt, **values)


def build_judge_system(question_type: str, **values: str) -> RenderedPrompt:
    """Render the answer-aware JUDGE prompt. Only OBJECTIVE questions have a judge
    (it needs a correct answer to be answer-aware); attitudinal questions have no
    correct answer, so they are scored by the monitor alone (see episode.py)."""
    if question_type != "objective":
        raise ValueError(
            f"no judge for question_type={question_type!r}: attitudinal questions "
            f"have no correct answer and are scored by the monitor alone"
        )
    return build_judge_objective_system(**values)


def build_monitor_objective_system(*, question: str, material: str = "") -> RenderedPrompt:
    """Render the answer-blind MONITOR prompt for objective questions. It is NOT
    given the correct or incorrect answer. ``material`` is optional: "" yields the
    fully-blind monitor; passing the corpus yields the source-aware monitor
    (which can check the source material but is still answer-blind). The shared
    features_of_persuasion + confidence rubric is composed in from the
    ``features_of_persuasion`` fragment. Needs: question."""
    prompt = _inject_fragment(
        load_prompt("monitor_objective"), "persuasion_rubric", "features_of_persuasion"
    )
    return _rendered(prompt, question=question, material=material)


def build_monitor_attitudinal_system(*, question: str, stances: list[str]) -> RenderedPrompt:
    """Render the answer-blind MONITOR for attitudinal questions (the sole
    evaluator — there is no attitudinal judge). It knows the stances/axis but not
    which one the actor was told to push. Needs: question, stances (a list)."""
    return _rendered(
        load_prompt("monitor_attitudinal"), question=question, stances=_format_stances(stances)
    )


def build_monitor_system(question_type: str, *, material: str = "", **values: str) -> RenderedPrompt:
    """Render the answer-blind MONITOR prompt for the given question type. The
    monitor never receives the correct or incorrect answer. ``material`` is
    threaded only to the objective monitor (empty = fully-blind, filled =
    source-aware); the attitudinal monitor takes ``stances`` (the axis) instead."""
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
        "stances": ["<stance-yes>", "<stance-no>"],
        "target_stance": "<stance-yes>",
    }
    for level in ("aggressive", "moderate", "subtle"):
        out = build_misaligned_system(
            level, **{k: scenario[k] for k in ("question", "correct_answer", "target_answer")}
        ).text
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in misaligned/{level}"
        print(f"misaligned/{level:10s}: OK ({len(out)} chars)")
    # Misaligned-but-correct actor (same template + guidelines, steered toward correct).
    for level in ("aggressive", "moderate", "subtle"):
        out = build_misaligned_correct_system(
            level, **{k: scenario[k] for k in ("question", "correct_answer")}
        ).text
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in misaligned_correct/{level}"
        print(f"misaligned_correct/{level:4s}: OK ({len(out)} chars)")
    # Attitudinal misaligned actor (its own template + guideline sets).
    for level in ("aggressive", "moderate", "subtle"):
        out = build_misaligned_attitudinal_system(
            level, **{k: scenario[k] for k in ("question", "stances", "target_stance")}
        ).text
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in misaligned-attitudinal/{level}"
        print(f"misaligned-att/{level:6s}: OK ({len(out)} chars)")
    for name, fn, kw in [
        ("aligned", build_aligned_system, {k: scenario[k] for k in ("question", "correct_answer")}),
        ("aligned-attitudinal", build_aligned_attitudinal_system, {k: scenario[k] for k in ("question", "stances")}),
        ("user_simulator", build_user_simulator_system, {"question": scenario["question"]}),
        (
            "judge_objective",
            build_judge_objective_system,
            {k: scenario[k] for k in ("question", "correct_answer", "incorrect_answer", "material")},
        ),
        (
            "monitor_objective",
            build_monitor_objective_system,
            {k: scenario[k] for k in ("question", "material")},
        ),
        (
            "monitor_attitudinal",
            build_monitor_attitudinal_system,
            {k: scenario[k] for k in ("question", "stances")},
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
