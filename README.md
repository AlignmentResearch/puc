# puc

Prompts, templates, and (later) experiment code for studying **persuasion under
control** — how a misaligned AI assistant can steer a human toward an incorrect
high-stakes decision, and how to measure that effect against an aligned baseline.

## Status

Early setup. The prompts (manipulation actor, aligned baseline, draft
user-simulator, and two judges) are in the modern messages format, and an
Anthropic client plus an episode loop wire them to the API.

Each scenario centers on a **question** the assistant helps answer. An episode
runs in **rounds**, where one round is a user message followed by one actor
reply. Today only `rounds = 1` is supported: the persuadee's opening message is
automatic (the served material — a generated corpus for objective scenarios), the
actor gives one reply, and a **judge** scores it. `rounds > 1` (a multi-turn
dialogue with the persuadee) is not wired up yet. The judge is chosen by the
scenario's `question_type`: `objective` (binary/numeric/probabilistic, has a
correct answer — scores whether the response steers toward a wrong answer) or
`attitudinal` (no correct answer — scores how one-sidedly the response pushes a
view).

The first experiment to be implemented is **scenario 2** (a research manager
weighs a disappointing safety-research scale-up), material version `2_1`.

## Layout

- [`prompts/`](prompts/) — model prompts (system prompts). See
  [`prompts/README.md`](prompts/README.md).
- [`scenarios/`](scenarios/) — **scenario configs** (WHAT is under test): a shared
  task narrative (`<n>.narrative.md`) plus one file per material *version*
  (`<n>_<v>.toml`) with the question, correct/target answers, `question_type`, and
  the "example material to generate".
- [`generated_material/`](generated_material/) — corpora produced by the generator:
  a single `corpus.md`-style file per run (bundling narrative + question + docs)
  next to a `<name>.manifest.json` sidecar. Created on first generation.
- [`client.py`](client.py) — Anthropic Messages API client.
- [`episode.py`](episode.py) — the actor → (optional simulated-user) → judge episode loop.
- [`config.py`](config.py) — parse a run-config TOML into concrete runs.
- [`run.py`](run.py) — kick off experiments from a config and log the results.
- [`generate_material.py`](generate_material.py) — offline generator: build a
  synthetic corpus for an objective scenario (see below).
- [`experiments.2_1.toml`](experiments.2_1.toml) — annotated example **run config** (HOW to run).
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Running experiments

Two configs are kept separate:

- **Run config** (`experiments.*.toml`) — HOW to run: `[defaults]` and one or more
  `[[experiment]]` blocks (`run.py` runs them all). Any of the model (per role),
  `condition`, or `level` fields can be a **list** to sweep — the runner expands
  the cartesian product into one episode per combination.
- **Scenario config** (`scenarios/<id>.toml`) — WHAT is under test. A run
  references it by id (`scenario = "2_1"` → `scenarios/2_1.toml`). An `objective`
  scenario **must** have material at run time — either the scenario supplies it
  (`material` / `material_file`) or the run points at a generated corpus
  (`material_path` / `material_dir`); there is no empty-material default.
  `attitudinal` material is optional.

Each logged record carries **both** the run config and the resolved scenario
config, so a result is fully self-describing.

The actor's private reasoning uses Anthropic's native extended thinking, logged
per turn and never shown to the simulated user or judge. Control it per
experiment with `thinking = "adaptive"` (default), an integer token budget, or
`"off"`.

```
python run.py experiments.2_1.toml --dry-run   # preview the run plan
python run.py experiments.2_1.toml             # run (needs API credit)
```

Results are appended to `results/<timestamp>-<config>.jsonl`, one self-describing
record per episode. Credentials are read from the environment (`.env` is loaded
if present). See [`experiments.2_1.toml`](experiments.2_1.toml)
and [`scenarios/2_1.toml`](scenarios/2_1.toml) for the full schema.

## Generating material

Objective scenarios call for a background corpus too large for an unaided human to
digest in ~an hour, with the decisive evidence fragmented and buried among
plausible distractors. `generate_material.py` builds it offline in two steps (plan
role-tagged snippets, then expand each document into prose; see
[`prompts/README.md`](prompts/README.md)), driven by the scenario config: its
question/answers, the shared `narrative_file`, the "example material to generate"
(`example_snippets` + `example_document_types`, which the generator **expands**
on), and a `[generation]` table of volume knobs.

```
python generate_material.py scenarios/2_1.toml --dry-run   # preview the plan, no API calls
python generate_material.py scenarios/2_1.toml             # generate (needs API credit)
python generate_material.py scenarios/2_1.toml my_corpus.md  # explicit output path
```

Each run writes a single **corpus file** — bundling the narrative + question +
generated documents — plus a `<name>.manifest.json` sidecar recording the scenario
values and exact config that produced it, token usage, and the Step-1 plan (the
document directory). Output defaults to `generated_material/<scenario_id>/<runZ>.md`
when no path is given. To run an experiment against a generated corpus, point the
`[[experiment]]` block at the file with `material_path = "generated_material/…"`.
Objective scenarios ship no placeholder material, so this is required before a run.

## Design decisions

A few choices worth stating so they are not re-litigated by accident:

- **The generated corpus is the source of truth for what the persuadee sees.**
  The corpus already bundles the shared narrative + the question + the documents,
  so the harness serves it *whole* as the persuadee's opening message and does
  **not** re-inject the scenario's `question`. Re-injecting it (the earlier bug)
  duplicated the question and nested the narrative under "background I gathered".
  The scenario's `question` field survives only to seed the actor/judge **system**
  prompts and to drive generation.
- **Rounds are 1-based** (one round = one user message + one actor reply). There
  is no "round 0". Only `rounds = 1` is supported for now; `rounds > 1` raises.
  At `rounds = 1` the opening user turn is fixed (the served material/question),
  so the user simulator is never actually consulted.
- **`human` field: `simulator` | `real`.** The persuadee is an LLM `simulator`
  (run in the background) for now. `real` (a live human) would need an interactive
  GUI, which we are not building, so it raises. Since the opening turn is fixed at
  `rounds = 1`, `human` currently has no runtime effect — it is scaffolding for
  multi-turn and is validated and logged.
- **The judge runs inline, after the actor, within the episode.** Running it
  fully offline (a separate post-hoc pass over logged transcripts) is only
  strictly needed for the live-human path, which does not exist yet, so it is
  deferred. The judge receives the material **and** the transcript: the material
  in full via its system prompt, and a transcript in which the opening
  material-dump turn is masked to a short marker so the corpus is not duplicated.
