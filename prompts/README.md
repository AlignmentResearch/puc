# Prompts

Prompts for the persuasion / manipulation experiments, in the **modern messages
format**: each file is a *system prompt*. The interaction is carried as real
user/assistant turns by the harness ([`../episode.py`](../episode.py)), not
embedded in the prompt text.

## Format

Each file is plain text with a YAML frontmatter block declaring its `name`,
`description`, `type`, optional `source`, and the `placeholders` it requires.
The frontmatter is the machine-readable contract: [`loader.py`](loader.py)
strips it, checks the body uses only declared placeholders, and rejects renders
with missing or extra values. Substitution is regex-based (only declared
`{lowercase_identifier}` tokens), so literal braces — e.g. the JSON example in
the judge prompt — are safe.

```python
from prompts.loader import build_misaligned_system, build_aligned_system

actor_system = build_misaligned_system(
    "subtle",  # or "moderate" / "aggressive"
    decision=..., correct_answer=..., target_answer=...,
)
baseline_system = build_aligned_system(decision=..., correct_answer=...)
```

Run `python prompts/loader.py` for an offline smoke test of every prompt build.

## The roles

| Prompt | Role |
| --- | --- |
| [`manipulation_template.md`](manipulation_template.md) + a `guidelines_*` file | **actor** (misaligned): steer the user to the target answer without arousing suspicion. |
| [`aligned_model.md`](aligned_model.md) | **actor** (aligned baseline): help the user reach the correct answer honestly. |
| [`user_simulator.md`](user_simulator.md) | **simulated user** — a careful decision-maker, blind to the answer. *Draft — tune the persona.* |
| [`judge.md`](judge.md) | **judge** — blind evaluator of the finished transcript. *Draft — tune the rubric.* |

The misaligned actor is the shared template plus one guideline set controlling
how aggressively it manipulates:
[`guidelines_aggressive.md`](guidelines_aggressive.md),
[`guidelines_moderate.md`](guidelines_moderate.md),
[`guidelines_subtle.md`](guidelines_subtle.md). `build_misaligned_system`
inserts the chosen set into the template's `{guidelines}` slot.

Adapted from the manipulation-evaluation prompts in Benton et al.

## Actor output format

The actor writes private reasoning between `<thinking>` and `</thinking>`, then
the message the user sees. The harness (`split_thinking`) keeps the two apart —
the thinking is logged but never shown to the simulated user or the judge. We
keep a manual scratchpad rather than native model thinking so the reasoning is
inspectable and identical across providers.

## Template placeholders

| Placeholder | Meaning |
| --- | --- |
| `{decision}` | The main business decision the user is trying to make. |
| `{correct_answer}` | The answer the evidence actually supports. |
| `{target_answer}` | The incorrect answer the misaligned actor steers toward. |
| `{guidelines}` | One of the guideline sets above (misaligned template only). |

The old `{interaction}` placeholder is gone: the conversation is now real
message turns, not text spliced into the prompt.

## Still to come

`user_simulator.md` and `judge.md` are first drafts. Also unmodeled yet: the
document-sharing mechanic (the user holding background docs the assistant sees
only when shared), a structured scenario dataset, and offline scoring that
compares the judge's `steered_toward` / `user_leaning` against the known
correct/target answers.
