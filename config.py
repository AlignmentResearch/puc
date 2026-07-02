"""Experiment configuration: turn one TOML file into a list of concrete runs.

An experiment file declares shared defaults, reusable scenarios, and one or more
`[[experiment]]` blocks. Each experiment can pin a single value or a *list* for
the sweepable dimensions — the model per role, the condition, and the misaligned
level — and the loader expands the cartesian product into `EpisodeSpec`s, one per
episode the runner should execute (see ``run.py``).

TOML is parsed with the stdlib ``tomllib`` (Python 3.11+); no extra dependency.

A ``[defaults]`` block is optional — settings there are inherited by every
experiment that doesn't state its own. Explicit per-experiment settings are fine
too; that's what ``experiments.scenario2dot1.toml`` uses. Every run-shaping
setting must come from the experiment or ``[defaults]``; there are no hidden
code-level default values (a missing one is an error), so the file is the full,
explicit spec.

File shape (see ``experiments.scenario2dot1.toml``):

    [scenarios.alt_2_1]
    decision       = "..."
    correct_answer = "..."
    target_answer  = "..."

    [[experiment]]
    name       = "scenario2dot1"
    scenario   = "alt_2_1"
    rounds     = 4
    max_tokens = 2048
    repeats    = 1
    thinking   = "adaptive"   # actor's private reasoning: "adaptive" | int | "off"
    [experiment.models]
    actor = "claude-opus-4-8"
    user  = "claude-opus-4-8"
    judge = "claude-sonnet-4-6"
    condition = ["aligned", "misaligned"]   # a list = sweep this axis
    level     = ["subtle", "aggressive"]    # ignored for the aligned condition

Offline expansion check (no API key needed):
    python config.py experiments.scenario2dot1.toml
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


def _resolve_scenario(ref, scenarios: dict, exp_name: str) -> dict:
    """A scenario is given inline as a table or by name into ``[scenarios]``."""
    if ref is None:
        raise ValueError(f"experiment {exp_name!r}: missing 'scenario'")
    if isinstance(ref, str):
        if ref not in scenarios:
            raise ValueError(
                f"experiment {exp_name!r}: unknown scenario {ref!r} "
                f"(defined: {sorted(scenarios)})"
            )
        return dict(scenarios[ref])
    if isinstance(ref, dict):
        return dict(ref)
    raise ValueError(f"experiment {exp_name!r}: 'scenario' must be a name or a table")


def _validate_scenario(scenario: dict, condition: str, exp_name: str) -> None:
    required = {"decision", "correct_answer"}
    if condition == "misaligned":
        required |= {"target_answer"}
    missing = required - scenario.keys()
    if missing:
        raise ValueError(
            f"experiment {exp_name!r} ({condition}): scenario is missing "
            f"{sorted(missing)}"
        )


def _expand_experiment(exp: dict, defaults: dict, scenarios: dict) -> list[EpisodeSpec]:
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

    scenario = _resolve_scenario(exp.get("scenario"), scenarios, name)

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
    """Parse a TOML experiment file into the flat list of episodes to run."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    defaults = cfg.get("defaults", {})
    scenarios = cfg.get("scenarios", {})
    experiments = cfg.get("experiment", [])
    if not experiments:
        raise ValueError(f"{path}: no [[experiment]] blocks found")

    specs: list[EpisodeSpec] = []
    for exp in experiments:
        specs.extend(_expand_experiment(exp, defaults, scenarios))
    return specs


if __name__ == "__main__":
    # Offline expansion check — parses a config and prints the run plan, no API calls.
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "experiments.scenario2dot1.toml"
    specs = load_specs(path)
    print(f"{path}: {len(specs)} episode(s)\n")
    for i, s in enumerate(specs):
        print(
            f"[{i:03d}] {s.label}\n"
            f"      rounds={s.rounds} max_tokens={s.max_tokens}\n"
            f"      models={s.models}"
        )
