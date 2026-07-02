# puc

Harness for studying **persuasion under control** — how a misaligned AI assistant
can steer a human toward an incorrect high-stakes decision, and how to measure
that against an honest (aligned) baseline.

The unit of study is an **episode**: an assistant (the *actor*) answers a
*question* for a human, and a blind *judge* scores whether the answer steered
toward a wrong conclusion. We compare a **misaligned** actor (secretly pushing a
target answer, at a chosen manipulation level) against an **aligned** one, on the
same question and evidence.

> **Status — early.** Single-turn episodes work end to end. Multi-turn, a live
> human persuadee, and the attitudinal judge are placeholders. The first
> experiment is **scenario 2**: a research manager weighs a disappointing
> safety-research scale-up (material version `2_1`).

## The flow

Setting up and running an experiment is four steps, each a small config plus one
script. The scripts are secondary — a Jupyter notebook (added next) will drive
this flow interactively and is the intended way to run things.

Setup once: `pip install -r requirements.txt` and set `ANTHROPIC_API_KEY` (a
`.env` file is loaded if present).

**1. Define a scenario — _what is under test._**
A scenario is a shared narrative (`scenarios/<n>.narrative.md`) plus one TOML per
material *version* (`scenarios/<n>_<v>.toml`): the `question`, the `correct_answer`
the evidence supports, a candidate `target_answer` for the misaligned actor to
push, and seed hints for material generation.

**2. Generate material — _the evidence the human reads._**
An objective question needs a background corpus too large to skim in an hour, with
multiple load-bearing facts (no one of them decisive) that must be combined to
reach the answer — buried among plausible distractors and often fragmented across
documents.
`generate_material.py` reads the scenario and builds one: a single corpus file
(narrative + question + documents) plus a manifest recording how it was made.

```
python generate_material.py scenarios/2_1.toml
```

**3. Configure a run — _how to run it._**
A run config (`experiments/<name>.toml`) lists one or more `[[experiment]]` blocks:
which models play each role (actor / user / judge), which `condition`s and
manipulation `level`s to test, token budgets, and the generated corpus to serve.
Any of model / `condition` / `level` can be a **list** to sweep — the runner
expands the cartesian product into one episode per combination.

**4. Run and read — _execute and score._**
`run.py` expands the config, runs each episode (the actor answers; the judge scores
the transcript), and appends one self-describing JSONL record per episode to
`results/`.

```
python run.py experiments/2_1.toml --dry-run   # preview the plan, no API calls
python run.py experiments/2_1.toml             # run (needs API credit)
```

Every record carries **both** the run config and the resolved scenario, so a
result stands on its own.

## Key ideas

- **Run config vs. scenario config are separate.** The scenario (`scenarios/`) is
  *what* is under test; the run (`experiments/`) is *how* to test it. A run
  references a scenario by id (`scenario = "2_1"`); both are logged per episode.
- **Objective vs. attitudinal questions.** `objective` questions have a correct
  answer the material supports and require a generated corpus; `attitudinal`
  questions have none (their judge is still a placeholder).
- **One episode is one round today.** A round is one user message + one actor
  reply. The opening message is automatic (the served corpus), so the user
  simulator isn't consulted yet and `rounds > 1` (multi-turn) is not wired up.
- **Actor reasoning is private.** The actor thinks on Anthropic's native
  extended-thinking channel — logged per turn, never shown to the user or judge.
  Set `thinking` per experiment (`"adaptive"`, an int token budget, or `"off"`).

## Repo map

The flow above is the main thing; for reference:

- [`scenarios/`](scenarios/), [`experiments/`](experiments/) — scenario and run configs.
- [`generated_material/`](generated_material/) — generated corpora and their manifests.
- [`prompts/`](prompts/) — system prompts grouped by role; see [`prompts/README.md`](prompts/README.md).
- [`generate_material.py`](generate_material.py), [`run.py`](run.py) — the two entry points.
- [`config.py`](config.py), [`episode.py`](episode.py), [`client.py`](client.py) — config
  expansion, the episode loop, and the Anthropic client, called under the hood.
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Design notes

Non-obvious choices, recorded so they aren't re-litigated:

- **The generated corpus is what the persuadee sees.** It already bundles narrative
  + question + documents, so the harness serves it whole and does *not* re-inject
  the scenario's `question` (that field only seeds the system prompts and generation).
- **The judge runs inline** after the actor. It gets the material via its system
  prompt and the transcript with the opening corpus dump masked to a marker, so the
  corpus isn't duplicated.
- **`human = "simulator" | "real"`.** Only the LLM `simulator` exists; `real` (a
  live human, needing a GUI) raises. It is validated and logged as scaffolding for
  multi-turn.
