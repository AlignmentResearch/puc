# puc

Prompts, templates, and (later) experiment code for studying **persuasion under
control** — how a misaligned AI assistant can steer a human toward an incorrect
high-stakes decision, and how to measure that effect against an aligned baseline.

## Status

Early setup. The prompts (manipulation actor, aligned baseline, plus draft
user-simulator and judge) are in the modern messages format, and an Anthropic
client plus an episode loop wire them to the API.

The first experiment to be implemented is **scenario alternate 2.1**: a research
manager is persuaded to deprioritize a promising safety research program.

## Layout

- [`prompts/`](prompts/) — model prompts (system prompts). See
  [`prompts/README.md`](prompts/README.md).
- [`client.py`](client.py) — Anthropic Messages API client.
- [`episode.py`](episode.py) — the actor → simulated-user → judge episode loop.
- [`config.py`](config.py) — parse a TOML experiment file into concrete runs.
- [`run.py`](run.py) — kick off experiments from a config and log the results.
- [`experiments.scenario2dot1.toml`](experiments.scenario2dot1.toml) — annotated example config.
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Running experiments

Experiments are driven by a TOML config: reusable `[scenarios]` and one or more
`[[experiment]]` blocks (`run.py` runs them all). Any of the model (per role),
`condition`, or `level` fields can be a **list** to sweep it — the runner expands
the cartesian product into one episode per combination. An optional `[defaults]`
block can share settings across experiments if you don't want to repeat them.

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
for the full schema.
