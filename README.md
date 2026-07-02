# puc

Prompts, templates, and (later) experiment code for studying **persuasion under
control** — how a misaligned AI assistant can steer a human toward an incorrect
high-stakes decision, and how to measure that effect against an aligned baseline.

## Status

Early setup. The prompts (manipulation actor, aligned baseline, draft
user-simulator, and two judges) are in the modern messages format, and an
Anthropic client plus an episode loop wire them to the API.

Each scenario centers on a **question** the assistant helps answer. By default
an episode is **single-turn**: the actor gives one response to the question and
a **judge** scores it (no simulated user). Set `rounds >= 1` for the multi-turn
dialogue instead. The judge is chosen by the scenario's `question_type`:
`objective` (binary/numeric/probabilistic, has a correct answer — scores whether
the response steers toward a wrong answer) or `attitudinal` (no correct answer —
scores how one-sidedly the response pushes a view). Background `material` for a
scenario is optional and passed to the judge when present.

The first experiment to be implemented is **scenario alternate 2.1**: a research
manager is persuaded to deprioritize a promising safety research program.

## Layout

- [`prompts/`](prompts/) — model prompts (system prompts). See
  [`prompts/README.md`](prompts/README.md).
- [`scenarios/`](scenarios/) — **scenario configs** (WHAT is under test): one file
  per scenario with the question, correct/target answers, `question_type`, and
  background material.
- [`client.py`](client.py) — Anthropic Messages API client.
- [`episode.py`](episode.py) — the actor → (optional simulated-user) → judge episode loop.
- [`config.py`](config.py) — parse a run-config TOML into concrete runs.
- [`run.py`](run.py) — kick off experiments from a config and log the results.
- [`experiments.scenario2dot1.toml`](experiments.scenario2dot1.toml) — annotated example **run config** (HOW to run).
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Running experiments

Two configs are kept separate:

- **Run config** (`experiments.*.toml`) — HOW to run: `[defaults]` and one or more
  `[[experiment]]` blocks (`run.py` runs them all). Any of the model (per role),
  `condition`, or `level` fields can be a **list** to sweep — the runner expands
  the cartesian product into one episode per combination.
- **Scenario config** (`scenarios/<id>.toml`) — WHAT is under test. A run
  references it by id (`scenario = "alt_2_1"` → `scenarios/alt_2_1.toml`). An
  `objective` scenario **must** supply `material` (inline or via `material_file`);
  there is no empty-material default. `attitudinal` material is optional.

Each logged record carries **both** the run config and the resolved scenario
config, so a result is fully self-describing.

The actor's private reasoning uses Anthropic's native extended thinking, logged
per turn and never shown to the simulated user or judge. Control it per
experiment with `thinking = "adaptive"` (default), an integer token budget, or
`"off"`.

```
python run.py experiments.scenario2dot1.toml --dry-run   # preview the run plan
python run.py experiments.scenario2dot1.toml             # run (needs API credit)
```

Results are appended to `results/<timestamp>-<config>.jsonl`, one self-describing
record per episode. Credentials are read from the environment (`.env` is loaded
if present). See [`experiments.scenario2dot1.toml`](experiments.scenario2dot1.toml)
and [`scenarios/alt_2_1.toml`](scenarios/alt_2_1.toml) for the full schema.
