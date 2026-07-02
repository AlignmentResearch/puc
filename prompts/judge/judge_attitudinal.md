---
name: judge-attitudinal
description: PLACEHOLDER — judge for ATTITUDINAL questions (matters of judgment
  with no single correct answer). Not yet designed; see "Still to come" in the
  README. The build_* wiring exists so the question_type path is testable.
type: system
placeholders:
  - question
  - material
---
PLACEHOLDER — no attitudinal judge prompt has been written yet.

The attitudinal question type is wired end to end (config validation, judge
selection in loader.py) but its rubric is not designed. Do not treat this as a
usable prompt. Write the real evaluation instructions here before running any
attitudinal scenario. The question and material placeholders are declared in the
frontmatter and can be dropped in once the rubric is written.
