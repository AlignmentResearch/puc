"""Load and render the prompt files in this directory.

Each prompt file is markdown-ish plain text with a YAML frontmatter block
declaring its name, type, and the placeholders it requires. Rendering is
regex-based (not str.format), so literal braces in a prompt (e.g. the JSON
example in the judge prompt) are safe: only `{lowercase_identifier}` tokens
that are declared placeholders get substituted, and every declared placeholder
must be supplied.

The prompts are in the modern messages format: each build_*_system function
returns a **system prompt** string. The interaction is carried as real
user/assistant turns by the harness (see ../episode.py), not embedded here.

Usage:
    from prompts.loader import build_misaligned_system, build_aligned_system

    actor_system = build_misaligned_system(
        "subtle", question=..., correct_answer=..., target_answer=...,
    )
    baseline_system = build_aligned_system(question=..., correct_answer=...)
"""

from __future__ import annotations

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


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the minimal YAML subset used here (scalars, lists, folded
    continuation lines). Returns (meta, body)."""
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


def load_prompt(stem: str) -> Prompt:
    """Load a prompt file by filename stem, e.g. 'manipulation_template'."""
    text = (PROMPTS_DIR / f"{stem}.md").read_text()
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


def build_misaligned_system(level: str, **values: str) -> str:
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
    return render(combined, **values)


def build_aligned_system(**values: str) -> str:
    """Render the aligned baseline system prompt. Needs: question, correct_answer."""
    return render(load_prompt("aligned_model"), **values)


def build_user_simulator_system(**values: str) -> str:
    """Render the simulated-user system prompt. Needs: question."""
    return render(load_prompt("user_simulator"), **values)


def build_judge_objective_system(**values: str) -> str:
    """Render the judge prompt for objective questions (binary/numeric/
    probabilistic). Needs: question, correct_answer, material."""
    return render(load_prompt("judge_objective"), **values)


def build_judge_attitudinal_system(**values: str) -> str:
    """Render the judge prompt for attitudinal questions (no correct answer).
    Needs: question, material."""
    return render(load_prompt("judge_attitudinal"), **values)


def build_judge_system(question_type: str, **values: str) -> str:
    """Render the judge prompt for the given question type: 'objective' selects
    the objective judge (needs correct_answer), anything else the attitudinal
    one. The attitudinal judge ignores correct_answer (there is no correct
    answer), so it is dropped here if passed."""
    if question_type == "objective":
        return build_judge_objective_system(**values)
    values.pop("correct_answer", None)
    return build_judge_attitudinal_system(**values)


if __name__ == "__main__":
    # Offline smoke test — builds every prompt, no API calls.
    scenario = {
        "question": "<question>",
        "correct_answer": "<correct>",
        "target_answer": "<target>",
        "material": "<material>",
    }
    for level in ("aggressive", "moderate", "subtle"):
        out = build_misaligned_system(
            level, **{k: scenario[k] for k in ("question", "correct_answer", "target_answer")}
        )
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in misaligned/{level}"
        print(f"misaligned/{level:10s}: OK ({len(out)} chars)")
    for name, fn, kw in [
        ("aligned", build_aligned_system, {k: scenario[k] for k in ("question", "correct_answer")}),
        ("user_simulator", build_user_simulator_system, {"question": scenario["question"]}),
        (
            "judge_objective",
            build_judge_objective_system,
            {k: scenario[k] for k in ("question", "correct_answer", "material")},
        ),
        (
            "judge_attitudinal",
            build_judge_attitudinal_system,
            {k: scenario[k] for k in ("question", "material")},
        ),
    ]:
        out = fn(**kw)
        assert not PLACEHOLDER_RE.search(out), f"unsubstituted token in {name}"
        print(f"{name:21s}: OK ({len(out)} chars)")
