# puc

Harness for studying **persuasion under control** — how a misaligned AI assistant
can steer a human toward an incorrect high-stakes decision, and how to measure
that against an honest (aligned) baseline.

The unit of study is an **episode**: an assistant (the *actor*) answers a
*question* for a human, and a blind *judge* scores whether the answer steered
toward a wrong conclusion. We compare a **misaligned** actor (secretly pushing a
target answer, at a chosen manipulation level) against an **aligned** one, on the
same question and evidence.

> **Status — early.** Single-turn episodes work end to end, for both objective
> and attitudinal questions. Multi-turn and a live human persuadee are
> placeholders. The first objective experiment is **scenario 2** (a research
> manager weighs a disappointing safety-research scale-up, material version
> `2_1`); the first attitudinal one is **`2_1_attitudinal`** (is mechanistic
> interpretability a promising research direction?).

## The flow

Setting up and running an experiment is four steps, each a small config plus one
script. The scripts are secondary — [`run.ipynb`](run.ipynb) drives this flow
interactively and is the intended way to run things.

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
A run config (`configs/<name>.toml`) is **scenario-agnostic** and has two tables:
`[experiment]` drives the conversation (actor / user models, `condition`s,
manipulation `level`s, token budget, thinking) and `[eval]` drives the judging
(judge / monitor models, `reveal_scratchpad`, a required `name`). Any of model /
`condition` / `level` in `[experiment]` can be a **list** to sweep — the runner
expands the cartesian product into one episode per combination. `configs/dev.toml`
is a small/cheap profile; `configs/main.toml` is the one to edit for a real run.

**4. Run and read — _converse, then evaluate._**
The two phases are decoupled so transcripts can be re-judged with new prompts:

```
python run.py converse configs/dev.toml generated_material/2_1/dev.md          # actor → transcripts/
python run.py eval      configs/dev.toml results/transcripts/dev-<stamp>.jsonl # judge + monitor → verdicts/
```

`converse` reads the scenario fields (question + answers) from the corpus's
manifest, runs each episode's actor turn, and writes one transcript record to
`results/transcripts/`. `eval` runs the judge + monitors over a transcripts file
using `[eval]` and writes verdicts to `results/verdicts/`, named after the
transcript they scored (so re-evaluations sort together). Each verdict logs the
prompt versions it used, so score changes across prompt iterations are traceable.

## Key ideas

- **Run config vs. scenario are separate.** The scenario (`scenarios/`) seeds
  *what* is under test; the run config (`configs/`) is *how* to test it and is
  reusable across scenarios. The *what* reaches a run through the generated corpus
  (its text is the material; its manifest carries the question + answers), passed
  in at run time — the run config never names a scenario.
- **Objective vs. attitudinal questions.** `objective` questions have a correct
  answer the material supports and require a generated corpus, scored by a judge
  (answer-aware) plus monitors. `attitudinal` questions are matters of judgment
  with no correct answer and no material: a run points straight at the scenario
  `.toml`, the actors are *unbiased* (steelman) vs *biased* (steer toward a
  stance), and a single *monitor* scores where the response lands on the stance
  axis and how biased it is (no judge).
- **One episode is one round today.** A round is one user message + one actor
  reply. The opening message is automatic (the served corpus), so the user
  simulator isn't consulted yet and `rounds > 1` (multi-turn) is not wired up.
- **Actor reasoning is private.** The actor thinks on Anthropic's native
  extended-thinking channel — logged per turn, never shown to the user or judge.
  Set `thinking` per experiment: `"off"` or an effort level
  (`low`/`medium`/`high`/`xhigh`/`max`) that steers how much it thinks. The
  evaluators have their own `[eval].thinking` (off by default); effort is a soft
  dial, not a cap, so a truncation warning fires if the budget runs out.

## Repo map

The flow above is the main thing; for reference:

- [`scenarios/`](scenarios/) — scenario configs (generation input); [`configs/`](configs/) — run configs.
- [`generated_material/`](generated_material/) — generated corpora and their manifests.
- [`prompts/`](prompts/) — system prompts grouped by role; see [`prompts/README.md`](prompts/README.md).
- [`run.ipynb`](run.ipynb) — the interactive driver; [`generate_material.py`](generate_material.py), [`run.py`](run.py) — the two entry points.
- [`config.py`](config.py), [`episode.py`](episode.py), [`client.py`](client.py) — config
  expansion, the episode loop, and the Anthropic client, called under the hood.
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Design notes

Non-obvious choices, recorded so they aren't re-litigated:

- **The generated corpus is what the persuadee sees.** It already bundles narrative
  + question + documents, so the harness serves it whole and does *not* re-inject
  the scenario's `question` (that field only seeds the system prompts and generation).
- **Judging is a separate phase** from the conversation, so transcripts can be
  re-judged with new prompts (and vice versa). The judge gets the material via its
  system prompt and the transcript with the opening corpus dump masked to a marker,
  so the corpus isn't duplicated; it re-loads the material from the corpus path
  recorded in each transcript.
- **`human = "simulator" | "real"`.** Only the LLM `simulator` exists; `real` (a
  live human, needing a GUI) raises. It is validated and logged as scaffolding for
  multi-turn.
