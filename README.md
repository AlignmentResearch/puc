# puc

Prompts, templates, and (later) experiment code for studying **persuasion under
control** — how a misaligned AI assistant can steer a human toward an incorrect
high-stakes decision, and how to measure that effect against an aligned baseline.

## Status

Early setup. Currently this repo contains the prompts for the manipulation
evaluation. Experiment code that hooks these prompts up to an API will come
later.

The first experiment to be implemented is **scenario alternate 2.1**: a research
manager is persuaded to deprioritize a promising safety research program.

## Layout

- [`prompts/`](prompts/) — model prompts and templates. See
  [`prompts/README.md`](prompts/README.md).
