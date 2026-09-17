# puc

Harness for studying **persuasion under control** — how a misaligned AI assistant can
steer a human toward an incorrect high-stakes decision, measured against an honest
(aligned) baseline.

The unit of study is an **episode**: an assistant (the *actor*) answers a *question* for
a human, and one or more blind evaluators (a *judge* and/or *monitors*) score how much
the answer relied on manipulative persuasion — and, for objective questions, whether it
steered toward a wrong answer. We compare a **misaligned** actor (secretly pushing a
target answer at a chosen manipulation level) against an **aligned** one, on the same
question and evidence.

> **Status — early.** Single-turn episodes work end to end, for both objective and
> attitudinal questions. Multi-turn and a live human persuadee are placeholders. The
> first objective experiment is **scenario 2** (a research manager weighs a disappointing
> safety-research scale-up, material version `2_1`); the first attitudinal one is
> **`2_1_attitudinal`** (is mechanistic interpretability a promising direction?).

# Overview of setup 
- Objective refers to a question with a correct answer the evidence actually supports
- Attitudinal refers to a matter of opinion or judgement with no provably correct answer 
- Material refers to generated background material relevant to deciding what the correct answer to the user's query is. For example, in a scenario where a research manager weighs a safety research update, the material determines what actually happened. It is the "evidence" that supports the scenarios with objective queries.  

There are two model actors as defined by their system prompts: 
- Misaligned: told to steer toward target_answer (objective) or target_stance (attitudinal) at a chosen manipulation level (subtle/moderate/aggressive), without arousing suspicion
- Aligned: an honest model trying to actually help the user with their decision-making query
- Both are tested across scenarios involving an objective and scenarios involving an attitudinal query.

There are two main types of LLM evaluators - a judge and a monitor. 
- Judge LLM which is "answer-aware". It sees the correct answer to the user query and material. 
- Monitor LLM which is "answer-blind". There are two types used: one that sees the material, and one that doesn't. 

How the judge and monitor gets used depends on whether the scenario involves an objective or attitudinal query.
- Objective: Judge and both types of monitor are used - together they score how much the model persuades the user towards an incorrect answer and with what features of persuasion (how manipulatively it argues). Having 3 conditions (answer+material, no material, material-only) isolates how much each kind of blindness moves the manipulation score.
- Attitudinal: Only a monitor is used. It scores how the user's stance on an issue changes and with what features of persuasion (how manipulatively it argues).

The current evaluation design does not provide for multiturn conversations and different target model providers - they are to be done in future versions, in addition to general improvements on context-specific realism in model and human dialogue.

## The flow

Setting up a run is four steps, each a small config plus one script. The `notebooks/`
demos (e.g. [`notebooks/objective/demo_e2e.ipynb`](notebooks/objective/demo_e2e.ipynb))
drive this flow interactively and are the intended way to run things.

Setup once: `pip install -r requirements.txt` and set `ANTHROPIC_API_KEY` (a `.env` file
is loaded if present).

**1. Define a scenario — _what is under test._**
A shared narrative (`scenarios/<n>.narrative.md`) plus one TOML per material *version*
(`scenarios/<n>_<v>.toml`): the `question`, the `correct_answer` the evidence supports, a
candidate `target_answer` for the misaligned actor to push, and seed hints for material
generation.

**2. Generate material — _the evidence the human reads._**
Objective questions need a background corpus with multiple load-bearing facts (no one of
them decisive) buried among plausible distractors. `generate_material.py` builds one
corpus file (narrative + question + documents) plus a manifest recording how it was made.
Attitudinal questions skip this step.

```
python generate_material.py scenarios/2_1.toml
```

**3. Configure a run — _how to run it._**
A run config (`configs/<name>.toml`) is **scenario-agnostic** and has two tables:
`[experiment]` drives the conversation (actor / user models, `condition`s, manipulation
`level`s, token budget, thinking) and `[eval]` drives evaluation (judge / monitor models,
`reveal_scratchpad`, a required `name`). Any model / `condition` / `level` in
`[experiment]` can be a **list** to sweep — the runner expands the cartesian product into
one episode per combination. `configs/dev.toml` is a small/cheap objective profile;
`configs/dev_attitudinal.toml` is its attitudinal counterpart.

**4. Run and read — _converse, then evaluate._**
The two phases are decoupled so transcripts can be re-scored with new prompts:

```
python run.py converse configs/dev.toml generated_material/2_1/dev.md          # actor → transcripts/
python run.py eval      configs/dev.toml results/transcripts/dev-<stamp>.jsonl # judge + monitor → verdicts/
```

`converse` reads the scenario fields from the corpus's manifest, runs each episode's
actor turn, and writes one transcript record to `results/transcripts/`. `eval` scores a
transcripts file and writes verdicts to `results/verdicts/`, named after the transcript
they scored (so re-evaluations sort together). Each verdict logs the prompt versions it
used, so score changes across prompt iterations are traceable.

## Key ideas

- **Run config vs. scenario are separate.** The scenario (`scenarios/`) seeds *what* is
  under test; the run config (`configs/`) is *how* to test it and is reusable across
  scenarios. The *what* reaches a run only through the generated corpus (its text is the
  material; its manifest carries the question + answers) — the run config never names a
  scenario.
- **Objective vs. attitudinal questions.** `objective` questions have a correct answer
  the material supports, require a generated corpus, and are scored by a judge
  (answer-aware) plus two monitors (fully-blind and source-aware). `attitudinal` questions
  have no correct answer and no material: the run points straight at the scenario `.toml`,
  and a single *monitor* scores where the response lands on the stance axis
  (`implied_stance`) and how strongly it persuades (`persuasion`) — no judge.
- **One episode is one round today.** A round is one user message + one actor reply. The
  opening message is the served corpus (automatic), so the user simulator isn't consulted
  yet and `rounds > 1` (multi-turn) is not wired up.
- **Actor reasoning is private.** The actor thinks on Anthropic's native extended-thinking
  channel — logged per turn, never shown to the user and withheld from evaluators unless
  `[eval].reveal_scratchpad` is set. Set `thinking` per experiment: `"off"` or an effort
  level (`low`/`medium`/`high`/`xhigh`/`max`).

## Repo map

- [`scenarios/`](scenarios/) — scenario configs (generation input); [`configs/`](configs/) — run configs.
- [`generated_material/`](generated_material/) — generated corpora and their manifests.
- [`prompts/`](prompts/) — system prompts grouped by role; see [`prompts/README.md`](prompts/README.md).
- [`notebooks/`](notebooks/) — interactive drivers per question type: end-to-end `demo_e2e`, plus `converse`, `eval`, `direct_qa`, `variability`.
- [`generate_material.py`](generate_material.py), [`run.py`](run.py) — the two script entry points.
- [`config.py`](config.py), [`episode.py`](episode.py), [`client.py`](client.py) — config expansion, the episode loop, and the Anthropic client.
- [`smoke_test.py`](smoke_test.py) — live wiring check.

## Design notes

- **The generated corpus is what the persuadee sees.** It bundles narrative + question +
  documents, so the harness serves it whole and does *not* re-inject the scenario's
  `question` (that field only seeds the system prompts and generation).
- **Evaluation is a separate phase** from the conversation, so transcripts can be re-scored
  with new prompts. Evaluators get the material via their system prompt and the transcript
  with the opening corpus dump masked to a marker, so the corpus isn't duplicated.
- **`human = "simulator" | "real"`.** Only the LLM `simulator` exists; `real` (a live
  human, needing a GUI) raises — validated and logged as scaffolding for multi-turn.
