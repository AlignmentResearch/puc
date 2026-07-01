# Prompts

Prompts and templates used to run the persuasion / manipulation experiments.

## Manipulation (misaligned) model

The misaligned model is built from a shared template plus one of three
guideline sets that control how aggressively it manipulates:

- [`manipulation_template.md`](manipulation_template.md) — the general
  template. Contains a `{guidelines}` slot into which one guideline set is
  inserted.
- [`guidelines_aggressive.md`](guidelines_aggressive.md)
- [`guidelines_moderate.md`](guidelines_moderate.md)
- [`guidelines_subtle.md`](guidelines_subtle.md)

To build a full misaligned prompt, substitute a guideline file into the
`{guidelines}` placeholder of the template.

These are adapted from the manipulation-evaluation prompts in Benton et al.
(the aggressive / moderate / subtle versions of the model).

## Aligned (baseline) model

- [`aligned_model.md`](aligned_model.md) — the aligned baseline. Mirrors the
  structure of the manipulation template but with an honest goal: help the
  user reach the **correct** answer. This is the baseline condition in the
  experimental design (a helpful model that steers toward the correct answer).

## Template placeholders

| Placeholder        | Meaning                                                        |
| ------------------ | -------------------------------------------------------------- |
| `{decision}`       | The main business decision the user is trying to make.         |
| `{correct_answer}` | The answer the generated material was intended to support.     |
| `{target_answer}`  | The incorrect answer the misaligned model steers toward.       |
| `{interaction}`    | The dialogue so far, reformatted with User/AI tags.            |
| `{guidelines}`     | One of the guideline sets above (misaligned template only).    |

## Still to come

Additional prompts required by the experimental design (LLM judge, material
generation / few-shot scaffolds, and any simulated-human follow-up prompts)
for **experiment 2.1** will be added here.
