---
name: user-simulator
description: PLACEHOLDER — the simulated human decision-maker (the "user"), blind
  to the correct/target answer. Only used in multi-turn (rounds > 1), which is
  not wired up yet. The build_* wiring exists so the persona can be dropped in.
type: system
placeholders:
  - question
---
PLACEHOLDER — no user-simulator persona has been written yet.

The simulated user only drives follow-up turns, which need multi-turn support
(rounds > 1); that is not built. Do not treat this as a usable prompt. Write the
persona here before enabling multi-turn. The question placeholder is declared in
the frontmatter and can be dropped in once the persona is written.
