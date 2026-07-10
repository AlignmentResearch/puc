"""Expand a run config + a generated corpus into the concrete list of episodes.

Two inputs, kept separate:

* RUN config (``configs/*.toml``) — HOW to run: one ``[experiment]`` table with
  models per role, the ``condition``/``level`` to test, token budget, thinking.
  It is scenario-agnostic and reusable. ``condition``, ``level``, and any model
  role may be a list; the loader expands the cartesian product into one
  ``EpisodeSpec`` per episode (times ``repeats``).
* CORPUS (``generated_material/<id>/<name>.md``) — WHAT is under test. A run
  points at one corpus at run time. Its text is the material; the scenario fields
  (question, correct/target answers, question_type) are read from the sibling
  ``<corpus>.manifest.json`` that generation wrote. The corpus text itself is NOT
  read here — specs carry the path (a pointer), and it is loaded once at run time.

Offline expansion check (no API key needed):
    python config.py configs/dev.toml generated_material/2_1/dev.md
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from itertools import product
from pathlib import Path

_ROLES = ("actor", "user")
_EVAL_ROLES = ("judge", "monitor")
_CONDITIONS = ("aligned", "misaligned")
_LEVELS = ("subtle", "moderate", "aggressive")
# Answer-provenance modes (the `target` knob): "authored" points the actors at the
# scenario's correct/target answers; "calibrated" points them at the manually
# supplied calibrated answers from the corpus's .calibration.json.
_TARGETS = ("authored", "calibrated")
# Adaptive-thinking effort levels (soft guidance for how much a model thinks),
# lowest → highest. "off" disables thinking; legacy "adaptive" == default effort.
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class EpisodeSpec:
    """Everything needed to run exactly one episode, plus its provenance.

    ``scenario`` holds the answer-fields from the corpus manifest; ``corpus_path``
    is a pointer to the material (loaded once at run time, not stored here)."""

    name: str
    condition: str
    level: str | None            # None for the aligned baseline (no manipulation)
    rounds: int
    max_tokens: int
    models: dict                 # {"actor", "user"}
    thinking: dict | None        # actor extended-thinking config (None = off)
    effort: str | None           # actor thinking effort ("low".."max"; None = default/off)
    human: str                   # persuadee: "simulator" | "real" (real: TODO)
    target: str                  # answer provenance: "authored" | "calibrated"
    repeat_index: int
    scenario: dict               # question, correct_answer, target_answer, question_type
    corpus_path: str             # pointer to the material corpus

    @property
    def label(self) -> str:
        """Short, human-readable id for logs and output filenames."""
        parts = [self.name, self.condition]
        if self.level:
            parts.append(self.level)
        # Attitudinal biased runs sweep a target stance; tag it by pole index.
        target_stance = self.scenario.get("target_stance")
        if target_stance:
            stances = self.scenario.get("stances") or []
            idx = stances.index(target_stance) if target_stance in stances else "?"
            parts.append(f"stance{idx}")
        parts.append(f"a={self.models['actor']}")
        parts.append(f"r{self.repeat_index}")
        return "/".join(parts)

    def conversation_kwargs(self, material: str) -> dict:
        """The keyword arguments ``episode.run_conversation`` expects. ``material``
        (the corpus text) is folded into the scenario dict here, at run time."""
        return {
            "condition": self.condition,
            "scenario": {**self.scenario, "material": material},
            "level": self.level or "subtle",  # unused by the aligned/aligned_free conditions
            "rounds": self.rounds,
            "models": self.models,
            "max_tokens": self.max_tokens,
            "thinking": self.thinking,
            "effort": self.effort,
            "human": self.human,
            "target": self.target,
        }

    def experiment_config(self) -> dict:
        """The conversation config for this episode (the HOW), logged alongside
        the scenario so a transcript record is self-describing."""
        return {
            "name": self.name,
            "repeat_index": self.repeat_index,
            "condition": self.condition,
            "level": self.level,
            "rounds": self.rounds,
            "human": self.human,
            "target": self.target,
            "max_tokens": self.max_tokens,
            "thinking": self.thinking,
            "effort": self.effort,
            "models": self.models,
        }


@dataclass(frozen=True)
class EvalConfig:
    """How to judge a transcript set. Read from the ``[eval]`` table; scalar
    (no sweep). ``name`` is required and drives the verdict filename."""

    name: str
    max_tokens: int
    reveal_scratchpad: bool
    thinking: dict | None        # evaluator extended-thinking config (None = off)
    effort: str | None           # evaluator thinking effort ("low".."max"; None = default/off)
    models: dict                 # {"judge", "monitor"}

    def config(self) -> dict:
        """The eval config, logged in each verdict record."""
        return {
            "name": self.name,
            "max_tokens": self.max_tokens,
            "reveal_scratchpad": self.reveal_scratchpad,
            "thinking": self.thinking,
            "effort": self.effort,
            "models": self.models,
        }


def _as_list(value) -> list:
    """A sweepable field is either a scalar (one run) or a list (many)."""
    return value if isinstance(value, list) else [value]


def _require(exp: dict, key: str, name: str):
    if key not in exp:
        raise ValueError(f"config {name!r}: missing required '{key}'")
    return exp[key]


def _thinking(value, name: str) -> tuple[dict | None, str | None]:
    """Map the config's ``thinking`` value to ``(thinking, effort)`` for the client:

    * "off"/false/None → ``(None, None)``          — thinking disabled.
    * an effort level ("low".."max") → ``({"type": "adaptive"}, level)`` — adaptive
      thinking with that effort steering how much the model thinks.
    * legacy "adaptive" → ``({"type": "adaptive"}, None)`` — adaptive at default effort.

    A fixed ``budget_tokens`` isn't offered: it's deprecated / 400s on current
    models, and effort is the recommended depth control.
    """
    if value in (None, False, "off"):
        return None, None
    if value == "adaptive":
        return {"type": "adaptive"}, None
    if value in _EFFORTS:
        return {"type": "adaptive"}, value
    raise ValueError(
        f"config {name!r}: thinking must be \"off\" or one of {_EFFORTS} "
        f"(or legacy \"adaptive\"), got {value!r}"
    )


def _read_scenario(corpus_path: Path) -> dict:
    """Read the scenario fields (question + answers) from the corpus's sibling
    ``<corpus>.manifest.json``. The corpus text itself is loaded later, at run
    time — here we only confirm it exists and is non-empty."""
    if not corpus_path.exists():
        raise ValueError(f"corpus not found: {corpus_path}")
    if corpus_path.stat().st_size == 0:
        raise ValueError(f"corpus is empty: {corpus_path}")
    manifest_path = corpus_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        raise ValueError(
            f"corpus manifest not found: {manifest_path} (generate the corpus with "
            f"generate_material.py so its manifest is written beside it)"
        )
    scenario = json.loads(manifest_path.read_text()).get("scenario")
    if not scenario:
        raise ValueError(f"manifest has no 'scenario' block: {manifest_path}")
    # Optional per-corpus calibration sidecar (measured, not authored): merges
    # aligned_target / misaligned_target into the scenario, used only when a run's
    # `target = "calibrated"`. Absent by default — authored runs never need it.
    calibration_path = corpus_path.with_suffix(".calibration.json")
    if calibration_path.exists():
        calibration = json.loads(calibration_path.read_text())
        scenario = {
            **scenario,
            **{k: calibration[k] for k in ("aligned_target", "misaligned_target") if k in calibration},
        }
    return scenario


def _read_attitudinal_scenario(scenario_path: Path) -> dict:
    """Read an attitudinal scenario straight from its TOML (question + stances):
    such questions need no generated material, so a run points at the scenario
    file directly. ``stances`` is the axis — first is pole 0, second pole 100."""
    if not scenario_path.exists():
        raise ValueError(f"scenario not found: {scenario_path}")
    with open(scenario_path, "rb") as f:
        s = tomllib.load(f)
    qt = s.get("question_type")
    if qt != "attitudinal":
        raise ValueError(
            f"{scenario_path}: a .toml source is treated as an attitudinal scenario, "
            f"but question_type is {qt!r} (expected 'attitudinal'). For objective "
            f"scenarios point the run at a generated corpus .md instead."
        )
    question = s.get("question")
    stances = s.get("stances")
    if not question:
        raise ValueError(f"{scenario_path}: missing 'question'")
    if not isinstance(stances, list) or len(stances) < 2:
        raise ValueError(f"{scenario_path}: 'stances' must be a list of at least two stances")
    return {"question": question, "question_type": "attitudinal", "stances": stances}


def load_specs(config_path: str | Path, corpus_path: str | Path) -> list[EpisodeSpec]:
    """Expand a run config + a source into the flat list of episodes. The source
    is either a generated corpus ``.md`` (objective; scenario from its sibling
    manifest) or an attitudinal scenario ``.toml`` (read directly, no material)."""
    config_path = Path(config_path)
    corpus_path = Path(corpus_path)
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    exp = cfg.get("experiment")
    if not exp:
        raise ValueError(f"{config_path}: missing an [experiment] table")

    name = exp.get("name") or config_path.stem
    is_attitudinal = corpus_path.suffix == ".toml"
    scenario = _read_attitudinal_scenario(corpus_path) if is_attitudinal else _read_scenario(corpus_path)

    rounds = _require(exp, "rounds", name)
    if rounds != 1:
        raise ValueError(
            f"config {name!r}: only rounds = 1 is supported (a round is one user "
            f"message + one actor reply; multi-turn is TODO); got {rounds!r}"
        )
    human = _require(exp, "human", name)
    if human != "simulator":
        raise ValueError(
            f"config {name!r}: only human = 'simulator' is supported ('real' needs "
            f"an interactive GUI, TODO); got {human!r}"
        )
    # `target` is optional (default "authored"); it is an objective-only knob
    # (answer-provenance framing), ignored for attitudinal (no answer to calibrate).
    target = exp.get("target", "authored")
    if target not in _TARGETS:
        raise ValueError(f"config {name!r}: target must be one of {_TARGETS}, got {target!r}")
    if target == "calibrated" and not is_attitudinal:
        missing = [k for k in ("aligned_target", "misaligned_target") if not scenario.get(k)]
        if missing:
            raise ValueError(
                f"config {name!r}: target = 'calibrated' but the corpus has no {missing} "
                f"(add them to {corpus_path.with_suffix('.calibration.json')})"
            )
    max_tokens = _require(exp, "max_tokens", name)
    repeats = _require(exp, "repeats", name)
    thinking, effort = _thinking(_require(exp, "thinking", name), name)

    models_cfg = exp.get("models", {})
    unknown = models_cfg.keys() - set(_ROLES)
    if unknown:
        raise ValueError(f"config {name!r}: unknown model roles {sorted(unknown)}")
    # Merge role-by-role; each value may be a scalar or a list (a sweep).
    role_axes: dict[str, list] = {}
    for r in _ROLES:
        val = models_cfg.get(r)
        if val is None:
            raise ValueError(f"config {name!r}: no model set for role {r!r}")
        role_axes[r] = _as_list(val)

    conditions = _as_list(_require(exp, "condition", name))
    bad = [c for c in conditions if c not in _CONDITIONS]
    if bad:
        raise ValueError(f"config {name!r}: unknown condition(s) {bad}, expected {_CONDITIONS}")
    levels = _as_list(_require(exp, "level", name))
    bad = [lv for lv in levels if lv not in _LEVELS]
    if bad:
        raise ValueError(f"config {name!r}: unknown level(s) {bad}, expected {_LEVELS}")

    specs: list[EpisodeSpec] = []
    for condition in conditions:
        # The aligned baseline has no manipulation level; collapse to one run so
        # sweeping `level` doesn't create identical duplicate baselines.
        cond_levels: list[str | None] = [None] if condition == "aligned" else list(levels)
        # Attitudinal biased runs also sweep a target stance (each pole of the
        # axis); every other case has a single (None) target.
        if is_attitudinal and condition == "misaligned":
            cond_targets: list[str | None] = list(scenario["stances"])
        else:
            cond_targets = [None]
        for level, target_stance, actor, user in product(
            cond_levels,
            cond_targets,
            role_axes["actor"],
            role_axes["user"],
        ):
            spec_scenario = scenario if target_stance is None else {**scenario, "target_stance": target_stance}
            for rep in range(repeats):
                specs.append(
                    EpisodeSpec(
                        name=name,
                        condition=condition,
                        level=level,
                        rounds=rounds,
                        max_tokens=max_tokens,
                        models={"actor": actor, "user": user},
                        thinking=thinking,
                        effort=effort,
                        human=human,
                        target=target,
                        repeat_index=rep,
                        scenario=spec_scenario,
                        corpus_path=str(corpus_path),
                    )
                )
    return specs


def load_eval_config(config_path: str | Path) -> EvalConfig:
    """Read the ``[eval]`` table into an EvalConfig (judge + monitor models are
    scalars — no eval-side sweep). ``name`` is required."""
    config_path = Path(config_path)
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    ev = cfg.get("eval")
    if not ev:
        raise ValueError(f"{config_path}: missing an [eval] table")

    name = _require(ev, "name", "eval")
    max_tokens = _require(ev, "max_tokens", "eval")
    reveal_scratchpad = bool(ev.get("reveal_scratchpad", False))
    # Evaluator thinking is opt-in; default off keeps existing runs unchanged.
    thinking, effort = _thinking(ev.get("thinking", "off"), f"eval {name!r}")

    models_cfg = ev.get("models", {})
    unknown = models_cfg.keys() - set(_EVAL_ROLES)
    if unknown:
        raise ValueError(f"eval {name!r}: unknown model roles {sorted(unknown)}")
    # Only the monitor is always required (it runs for every question type). The
    # judge is optional — attitudinal has no judge, so those configs omit it — and
    # is only included when set. The monitor falls back to the judge if unspecified.
    judge = models_cfg.get("judge")
    monitor = models_cfg.get("monitor") or judge
    if monitor is None:
        raise ValueError(f"eval {name!r}: no model set for role 'monitor'")
    models: dict = {"monitor": monitor}
    if judge is not None:
        models["judge"] = judge

    return EvalConfig(
        name=name,
        max_tokens=max_tokens,
        reveal_scratchpad=reveal_scratchpad,
        thinking=thinking,
        effort=effort,
        models=models,
    )


if __name__ == "__main__":
    # Offline expansion check — parses a config + corpus and prints the plan.
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "configs/dev.toml"
    corpus = sys.argv[2] if len(sys.argv) > 2 else "generated_material/2_1/dev.md"
    specs = load_specs(config, corpus)
    print(f"{config} + {corpus}: {len(specs)} episode(s)\n")
    for i, s in enumerate(specs):
        print(f"[{i:03d}] {s.label}  models={s.models}")
