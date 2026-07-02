"""Experiment configuration: turn one TOML file into a list of concrete runs.

Two configs are kept separate on purpose:

* RUN config — this file (``experiments.*.toml``): shared ``[defaults]`` and one
  or more ``[[experiment]]`` blocks describing HOW to run (models per role,
  condition, level, rounds, token budgets). Sweepable dimensions (model,
  condition, level) may be a scalar or a *list*; the loader expands the cartesian
  product into ``EpisodeSpec``s, one per episode ``run.py`` executes.
* SCENARIO config — a separate file per scenario under ``scenarios/`` describing
  WHAT is under test (the question, its correct/target answers, question_type,
  and how to source material). A run references it by id: ``scenario =
  "2_1"`` resolves to ``scenarios/2_1.toml`` (a path ending in ``.toml``
  works too). ``run.py`` logs both configs per episode so a result is
  self-describing.

TOML is parsed with the stdlib ``tomllib`` (Python 3.11+); no extra dependency.

Every run-shaping setting must come from the experiment or ``[defaults]``; there
are no hidden code-level defaults (a missing one is an error). Likewise a scenario
must state its fields explicitly — in particular an ``objective`` scenario MUST
have material at run time: either the scenario provides it (``material`` inline or
a ``material_file`` path) or the run supplies a generated corpus (``material_path``
/ ``material_dir`` in the experiment). There is no empty-material default.

Run-config shape (see ``experiments.2_1.toml``):

    [[experiment]]
    name       = "scenario_2_1"
    scenario   = "2_1"        # -> scenarios/2_1.toml
    rounds     = 0            # 0 = single-turn (one actor response, no user sim)
    max_tokens = 2048
    repeats    = 1
    thinking   = "adaptive"   # actor's private reasoning: "adaptive" | int | "off"
    condition  = ["aligned", "misaligned"]   # a list = sweep this axis
    level      = ["subtle", "aggressive"]    # ignored for the aligned condition
    # material_path = "generated_material/2_1/<run>.md"  # serve this generated
    #   corpus (from generate_material.py); required for objective scenarios that
    #   ship no material of their own.
    [experiment.models]
    actor = "claude-opus-4-8"
    user  = "claude-opus-4-8"
    judge = "claude-sonnet-4-6"

Scenario-config shape (see ``scenarios/2_1.toml``):

    question       = "..."
    correct_answer = "..."                 # the answer the generated material supports
    target_answer  = "..."                 # candidate incorrect answer (aims distractors)
    question_type  = "objective"           # "objective" | "attitudinal"; picks the judge
    # objective scenarios get material from a generated corpus (generate_material.py),
    # served via material_path / material_dir in the run config.

Offline expansion check (no API key needed):
    python config.py experiments.2_1.toml
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

Condition = str  # "aligned" | "misaligned"
_ROLES = ("actor", "user", "judge")
_CONDITIONS = ("aligned", "misaligned")
_LEVELS = ("subtle", "moderate", "aggressive")


@dataclass(frozen=True)
class EpisodeSpec:
    """Everything needed to run exactly one episode, plus its provenance.

    ``name`` groups specs that came from the same ``[[experiment]]`` block;
    ``repeat_index`` distinguishes identical settings run more than once."""

    name: str
    condition: Condition
    level: str | None            # None for the aligned baseline (no manipulation)
    rounds: int
    max_tokens: int
    models: dict = field(default_factory=dict)   # {"actor", "user", "judge"}
    scenario: dict = field(default_factory=dict)
    thinking: dict | None = None  # actor extended-thinking config (None = off)
    repeat_index: int = 0

    @property
    def label(self) -> str:
        """Short, human-readable id for logs and output filenames."""
        parts = [self.name, self.condition]
        if self.level:
            parts.append(self.level)
        parts.append(f"a={self.models['actor']}")
        parts.append(f"r{self.repeat_index}")
        return "/".join(parts)

    def episode_kwargs(self) -> dict:
        """The keyword arguments ``episode.run_episode`` expects."""
        return {
            "condition": self.condition,
            "scenario": self.scenario,
            "level": self.level or "subtle",  # unused when condition == "aligned"
            "rounds": self.rounds,
            "models": self.models,
            "max_tokens": self.max_tokens,
            "thinking": self.thinking,
        }

    def run_config(self) -> dict:
        """The RUN config for this episode (everything except the scenario), for
        logging alongside the scenario config so a record is self-describing."""
        return {
            "name": self.name,
            "repeat_index": self.repeat_index,
            "condition": self.condition,
            "level": self.level,
            "rounds": self.rounds,
            "max_tokens": self.max_tokens,
            "thinking": self.thinking,
            "models": self.models,
        }


def _as_list(value) -> list:
    """A sweepable field is either a scalar (one run) or a list (many)."""
    return value if isinstance(value, list) else [value]


def _require(exp: dict, defaults: dict, key: str, exp_name: str):
    """Fetch a setting from the experiment, falling back to [defaults]. Raises
    if neither sets it — settings are explicit; there are no hidden defaults."""
    if key in exp:
        return exp[key]
    if key in defaults:
        return defaults[key]
    raise ValueError(
        f"experiment {exp_name!r}: missing required '{key}' "
        f"(set it on the experiment or in [defaults])"
    )


# Anthropic requires budget_tokens >= 1024. max_tokens is the total ceiling for
# reasoning + visible reply, so a budget near max_tokens starves the reply; the
# reply reservation below is tunable per experiment via `min_reply_tokens`.
_MIN_THINKING_BUDGET = 1024
_DEFAULT_MIN_REPLY_TOKENS = 512


def _thinking(value, exp_name: str, max_tokens: int, min_reply_tokens: int) -> dict | None:
    """Map the config's `thinking` value to the actor's extended-thinking config:
    "off"/false → None; "adaptive" → adaptive; an int → that token budget.

    An int budget is validated against `max_tokens`: it must be a legal Anthropic
    budget and still leave `min_reply_tokens` for the visible reply (reasoning
    and reply share the `max_tokens` ceiling)."""
    if value in (None, False, "off"):
        return None
    if value == "adaptive":
        return {"type": "adaptive"}
    if isinstance(value, int):
        if value < _MIN_THINKING_BUDGET:
            raise ValueError(
                f"experiment {exp_name!r}: thinking budget {value} is below the "
                f"minimum of {_MIN_THINKING_BUDGET} tokens"
            )
        if value + min_reply_tokens > max_tokens:
            raise ValueError(
                f"experiment {exp_name!r}: thinking budget {value} leaves too "
                f"little of max_tokens={max_tokens} for the reply (need at least "
                f"min_reply_tokens={min_reply_tokens}); raise max_tokens or lower "
                f"the budget"
            )
        return {"type": "enabled", "budget_tokens": value}
    raise ValueError(
        f"experiment {exp_name!r}: thinking must be \"off\", \"adaptive\", or an "
        f"int token budget, got {value!r}"
    )


def _resolve_scenario(ref, base_dir: Path, exp_name: str) -> dict:
    """Resolve a scenario reference into its config dict.

    ``ref`` is normally a scenario id or path (loaded from a separate file under
    ``scenarios/``); an inline table is still accepted for quick tests. Any
    ``material_file`` is read here and folded into ``material`` so downstream code
    only ever sees resolved text (and the ``material_file`` path is kept for
    provenance)."""
    if ref is None:
        raise ValueError(f"experiment {exp_name!r}: missing 'scenario'")
    if isinstance(ref, dict):
        scenario = dict(ref)
        _load_material(scenario, base_dir, exp_name)
        return scenario
    if isinstance(ref, str):
        path = Path(ref)
        if path.suffix != ".toml":  # a bare id -> scenarios/<id>.toml
            path = base_dir / "scenarios" / f"{ref}.toml"
        elif not path.is_absolute():
            path = base_dir / path
        if not path.exists():
            raise ValueError(
                f"experiment {exp_name!r}: scenario file not found: {path}"
            )
        with open(path, "rb") as f:
            scenario = dict(tomllib.load(f))
        _load_material(scenario, path.parent, exp_name)
        return scenario
    raise ValueError(f"experiment {exp_name!r}: 'scenario' must be an id, path, or table")


def _load_material(scenario: dict, scenario_dir: Path, exp_name: str) -> None:
    """Read a ``material_file`` (relative to the scenario file) into
    ``material``. Inline ``material`` is left as-is. Does not enforce presence —
    that is ``_validate_scenario``'s job, so it can key off question_type."""
    mfile = scenario.get("material_file")
    if not mfile:
        return
    mpath = Path(mfile)
    if not mpath.is_absolute():
        mpath = scenario_dir / mpath
    if not mpath.exists():
        raise ValueError(
            f"experiment {exp_name!r}: material_file not found: {mpath}"
        )
    if scenario.get("material"):
        raise ValueError(
            f"experiment {exp_name!r}: scenario sets both 'material' and "
            f"'material_file'; use one"
        )
    scenario["material"] = mpath.read_text()


def _apply_run_material(exp: dict, scenario: dict, base_dir: Path, exp_name: str) -> None:
    """If the experiment points at a GENERATED corpus, use it as the served
    material, overriding whatever the scenario config supplied.

    ``material_dir`` is a run folder from ``generate_material.py`` (its
    ``corpus.md`` is served); ``material_path`` points straight at a corpus file.
    Both are resolved relative to the run-config file. When neither is set the
    scenario's own ``material`` / ``material_file`` stands (today's behavior), so
    existing configs are unaffected. The source path is recorded for provenance."""
    mdir = exp.get("material_dir")
    mpath = exp.get("material_path")
    if not mdir and not mpath:
        return
    if mdir and mpath:
        raise ValueError(
            f"experiment {exp_name!r}: set only one of 'material_dir' / 'material_path'"
        )
    corpus = Path(mdir) / "corpus.md" if mdir else Path(mpath)
    if not corpus.is_absolute():
        corpus = base_dir / corpus
    if not corpus.exists():
        raise ValueError(
            f"experiment {exp_name!r}: generated corpus not found: {corpus}"
        )
    scenario["material"] = corpus.read_text()
    scenario["material_source"] = str(corpus)


_QUESTION_TYPES = ("objective", "attitudinal")


def _validate_scenario(scenario: dict, condition: str, exp_name: str) -> None:
    required = {"question", "correct_answer"}
    if condition == "misaligned":
        required |= {"target_answer"}
    missing = required - scenario.keys()
    if missing:
        raise ValueError(
            f"experiment {exp_name!r} ({condition}): scenario is missing "
            f"{sorted(missing)}"
        )
    qt = scenario.get("question_type", "objective")
    if qt not in _QUESTION_TYPES:
        raise ValueError(
            f"experiment {exp_name!r}: unknown question_type {qt!r}, "
            f"expected one of {_QUESTION_TYPES}"
        )
    # Objective questions have a correct answer the material supports, so material
    # is mandatory — no empty default. Attitudinal material is optional.
    if qt == "objective" and not (scenario.get("material") or "").strip():
        raise ValueError(
            f"experiment {exp_name!r}: objective scenario requires material — "
            f"either set 'material'/'material_file' in the scenario config, or "
            f"generate a corpus (`python generate_material.py <scenario>.toml`) and "
            f"point the experiment at it with 'material_path'/'material_dir'"
        )


def _expand_experiment(exp: dict, defaults: dict, base_dir: Path) -> list[EpisodeSpec]:
    name = exp.get("name")
    if not name:
        raise ValueError("every [[experiment]] needs a 'name'")

    rounds = _require(exp, defaults, "rounds", name)
    max_tokens = _require(exp, defaults, "max_tokens", name)
    repeats = _require(exp, defaults, "repeats", name)
    # min_reply_tokens is a guard parameter, only consulted for an int `thinking`
    # budget, so it keeps a default rather than being required everywhere.
    min_reply_tokens = exp.get(
        "min_reply_tokens", defaults.get("min_reply_tokens", _DEFAULT_MIN_REPLY_TOKENS)
    )
    thinking = _thinking(
        _require(exp, defaults, "thinking", name), name, max_tokens, min_reply_tokens
    )

    base_models = defaults.get("models", {})
    exp_models = exp.get("models", {})
    unknown_roles = exp_models.keys() - set(_ROLES)
    if unknown_roles:
        raise ValueError(f"experiment {name!r}: unknown model roles {sorted(unknown_roles)}")
    # Merge role-by-role; each role value may be a scalar or a list (a sweep).
    role_axes = {r: _as_list(exp_models.get(r, base_models.get(r))) for r in _ROLES}
    for role, values in role_axes.items():
        if any(v is None for v in values):
            raise ValueError(f"experiment {name!r}: no model set for role {role!r}")

    conditions = _as_list(_require(exp, defaults, "condition", name))
    bad = [c for c in conditions if c not in _CONDITIONS]
    if bad:
        raise ValueError(f"experiment {name!r}: unknown condition(s) {bad}, expected {_CONDITIONS}")

    levels = _as_list(_require(exp, defaults, "level", name))
    bad = [lv for lv in levels if lv not in _LEVELS]
    if bad:
        raise ValueError(f"experiment {name!r}: unknown level(s) {bad}, expected {_LEVELS}")

    scenario = _resolve_scenario(exp.get("scenario"), base_dir, name)
    _apply_run_material(exp, scenario, base_dir, name)

    specs: list[EpisodeSpec] = []
    for condition in conditions:
        _validate_scenario(scenario, condition, name)
        # The aligned baseline has no manipulation level; collapse to one run so
        # sweeping `level` doesn't create identical duplicate baselines.
        cond_levels: list[str | None] = [None] if condition == "aligned" else list(levels)
        for level, actor, user, judge in product(
            cond_levels, role_axes["actor"], role_axes["user"], role_axes["judge"]
        ):
            for rep in range(repeats):
                specs.append(
                    EpisodeSpec(
                        name=name,
                        condition=condition,
                        level=level,
                        rounds=rounds,
                        max_tokens=max_tokens,
                        models={"actor": actor, "user": user, "judge": judge},
                        scenario=scenario,
                        thinking=thinking,
                        repeat_index=rep,
                    )
                )
    return specs


def load_specs(path: str | Path) -> list[EpisodeSpec]:
    """Parse a run-config TOML file into the flat list of episodes to run.
    Scenario references are resolved relative to the run file's directory."""
    path = Path(path)
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    defaults = cfg.get("defaults", {})
    experiments = cfg.get("experiment", [])
    if not experiments:
        raise ValueError(f"{path}: no [[experiment]] blocks found")

    base_dir = path.parent
    specs: list[EpisodeSpec] = []
    for exp in experiments:
        specs.extend(_expand_experiment(exp, defaults, base_dir))
    return specs


if __name__ == "__main__":
    # Offline expansion check — parses a config and prints the run plan, no API calls.
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "experiments.2_1.toml"
    specs = load_specs(path)
    print(f"{path}: {len(specs)} episode(s)\n")
    for i, s in enumerate(specs):
        print(
            f"[{i:03d}] {s.label}\n"
            f"      rounds={s.rounds} max_tokens={s.max_tokens}\n"
            f"      models={s.models}"
        )
