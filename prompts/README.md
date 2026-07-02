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
    question=..., correct_answer=..., target_answer=...,
)
baseline_system = build_aligned_system(question=..., correct_answer=...)
```

Run `python prompts/loader.py` for an offline smoke test of every prompt build.

## The roles

| Prompt | Role |
| --- | --- |
| [`manipulation_template.md`](manipulation_template.md) + a `guidelines_*` file | **actor** (misaligned): steer the user to the target answer without arousing suspicion. |
| [`aligned_model.md`](aligned_model.md) | **actor** (aligned baseline): help the user reach the correct answer honestly. |
| [`user_simulator.md`](user_simulator.md) | **simulated user** — a careful decision-maker, blind to the answer. Only used in multi-turn (`rounds >= 1`). *Draft — tune the persona.* |
| [`judge_objective.md`](judge_objective.md) | **judge** (objective questions — binary/numeric/probabilistic, has a correct answer): blind; scores whether the response steers toward an *incorrect* answer. |
| [`judge_attitudinal.md`](judge_attitudinal.md) | **judge** (attitudinal questions — no correct answer): blind; scores how one-sidedly the response pushes a view. |

The judge is chosen per scenario by `question_type` (`build_judge_system`). Both
judges take an optional `{material}` slot: when a scenario ships background
material it is passed in so the judge can catch omission / paltering; when empty,
the judge scores from the response alone.

The misaligned actor is the shared template plus one guideline set controlling
how aggressively it manipulates:
[`guidelines_aggressive.md`](guidelines_aggressive.md),
[`guidelines_moderate.md`](guidelines_moderate.md),
[`guidelines_subtle.md`](guidelines_subtle.md). `build_misaligned_system`
inserts the chosen set into the template's `{guidelines}` slot.

Adapted from the manipulation-evaluation prompts in Benton et al.

## Material generation (offline)

Objective scenarios need a background corpus too voluminous for an unaided human
to digest in ~an hour, with the decisive evidence fragmented and buried among
plausible distractors — while a careful reader (or AI) that processes all of it
can still reach the correct answer. Two prompts build that corpus in an
**offline pipeline, separate from the episode loop**, driven by
[`../generate_material.py`](../generate_material.py).

| Prompt | Step |
| --- | --- |
| [`material_snippets.md`](material_snippets.md) | **Step 1** — turn a scenario (question + correct/target answers + a background narrative + example snippets / document types) into structured JSON: a document inventory plus role-tagged snippets, with the decisive evidence split across documents. |
| [`material_expand.md`](material_expand.md) | **Step 2** — expand one planned document (its metadata + assigned snippets) into full prose. Called once per document. |

```python
from prompts.loader import build_material_snippets_system, build_material_expand_system

# Step 1: plan the corpus. The seed inputs (scenario_narrative, example_snippets,
# example_document_types) are OPTIONAL — the model EXPANDS and varies them, and
# derives from the question/answers when they are absent. The volume knobs
# (num_documents / num_supporting_snippets / num_distractor_snippets) default and
# are overridable.
plan_system = build_material_snippets_system(
    question=..., correct_answer=..., target_answer=...,
    scenario_narrative=...,       # optional
    example_snippets=...,         # optional
    example_document_types=...,   # optional
)
# ... a model call returns {"documents": [...], "snippets": [...]} ...

# Step 2: for each document, expand it with the snippets assigned to it.
doc_system = build_material_expand_system(
    question=..., correct_answer=..., target_answer=...,
    document=json.dumps(one_document),
    snippets=json.dumps(snippets_for_that_document),
)
```

Snippet roles are `supporting`, `distractor_hard` (near-miss, leans toward the
target answer), and `distractor_easy` (off-topic filler); `document_ids` is a
**list**, so one fact can be fragmented across several documents. `correct_answer`
is what the material supports; `target_answer` is a candidate *incorrect* answer
used only to aim the hard distractors.

### Running it

`generate_material.py` reads a scenario's `narrative_file`, its `example_snippets`
/ `example_document_types`, and its `[generation]` volume table, runs both steps,
and writes a single **corpus file** (bundling narrative + question + the generated
documents) plus a `<name>.manifest.json` sidecar (scenario fields, knobs, model,
usage, and the Step-1 plan = the document directory):

```
python generate_material.py scenarios/2_1.toml --dry-run    # no API calls
python generate_material.py scenarios/2_1.toml              # default output path
python generate_material.py scenarios/2_1.toml my_corpus.md  # explicit output
```

A run then serves that corpus by setting `material_path` in the experiment config
(see [`../config.py`](../config.py)). Objective scenarios ship no placeholder, so
this is required before a run.

## Actor output format

The actor's private reasoning uses Anthropic's native extended thinking: the API
returns it on a separate channel, so the harness (see
[`../episode.py`](../episode.py)) logs it per turn but never shows it to the
simulated user or the judge. Control it per experiment with `thinking`
(`"adaptive"` / an int token budget / `"off"`).

## Template placeholders

| Placeholder | Used by | Meaning |
| --- | --- | --- |
| `{question}` | all | The question the user is trying to answer (and what the judge scores). |
| `{correct_answer}` | actor, objective judge | The answer the evidence supports. |
| `{target_answer}` | misaligned actor | The incorrect answer the misaligned actor steers toward. |
| `{material}` | judges | Background material from the scenario config. Required for objective scenarios; empty string for attitudinal ones with none. |
| `{guidelines}` | misaligned template | One of the guideline sets above. |

Material comes from the scenario config (`scenarios/<id>.toml`, inline or via
`material_file`), not from a prompt.

## Still to come

`user_simulator.md` and both judges are early drafts — tune the persona and
rubrics. The **material-generation pipeline** (`material_snippets.md`,
`material_expand.md`, driven by `../generate_material.py`) is in place but untested
against a live model. Also not wired yet: **presenting** the generated corpus
(narrative + question + docs) to the human/persuadee at runtime (the episode loop
is unchanged for now); dedicated **attitudinal actor prompts** (the actor still
assumes a designated correct/target answer); and offline scoring that compares the
judge's verdict against the known correct/target answers.
