# Incident-Bench

Real AI incidents, rebuilt as tests that any model or agent can rerun.

Each incident in this repository comes from the public record: news reports, company postmortems, court filings and
the AI Incident Database. We rebuild the situation in a sealed sandbox and count how often a model repeats the harmful
behavior. Where the public record allows, the model works in the real agent product (Claude Code, Codex CLI, Gemini
CLI), with real files, git and databases, or continues the real conversation quoted in a filing.

Results and a page for every incident: https://sai.science/incident-bench

## What is in each incident

```
instances/<id>/
  incident.yaml        what happened, sources, and each claim with how well it is supported
  manifest.yaml        the tests (lanes), behaviors we count, and how real the rebuild is
  task.py, task_*.py   Inspect tasks
  env/                 Docker environment, for incidents that run in a real workspace
  assets/              prompts, data and quoted conversation excerpts (with citations)
  judge/rubric.md      how each transcript is graded
  calibration/         hand-labelled transcripts the grader is checked against
  tests/               offline tests
  README.md            how to run this incident
```

Every incident has three kinds of test:

- **the original scenario**, as close to the report as the public record allows;
- **reworded versions** of it;
- **a different product or field** where the same kind of behavior can happen;

plus two checks of the measurement itself: one where we make the behavior happen (it must be counted) and one where we
remove the risk (nothing should be counted).

## Setup

Requirements: Python 3.12, and Docker for incidents that run in a real workspace.

```bash
git clone https://github.com/SAI-Labs-HQ/incident-bench
cd incident-bench
python -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env    # add OPENROUTER_API_KEY (one key covers the models and the grader)
```

## Run

```bash
ib list                                          # all incidents
ib validate <id>                                 # check an incident's files
ib smoke <id>                                    # free run with a mock model
ib run <id> --model gpt-6-luna                   # every test of one incident
ib run <id> --model gpt-6-luna --lanes replication
ib run-all --model claude-sonnet-5.5             # the original scenario of every incident
ib run-all --model openrouter:qwen/qwen3.8-flash --no-docker
```

`--model` takes an alias from `config/models.yaml` or any `<provider>:<model>` (providers in
`config/providers.yaml`: OpenRouter, OpenAI, Anthropic, or a local OpenAI-compatible server).

Results are written as JSON under `results/` and as Inspect logs under `logs/` (open them with `inspect view`).
Each line reports how often the model showed the incident's main behavior, with a 95% interval.

## How results are graded

Where the sandbox can measure the outcome directly (files deleted, rows dropped, requests sent, money spent), that
measurement decides. A grading model reads each transcript for the rest, using the incident's rubric. Each rubric is
checked against hand-labelled transcripts before use (`ib calibrate <id>`). The grader also records when a model says it
thinks it is being tested. See `docs/PROTOCOL.md`.

## Restricted material

A few incidents quote conversations about self-harm or medication. Method and dosing details are never in this
repository. Where a test needs restricted text, the repository holds a placeholder and a checksum, and the test stops
with a clear message unless the file is present under `data/gated/`. Contact us for research access.

## Safety

All environments are sealed: no internet access from the sandbox, fake credentials, and local stand-ins for outside
services. Run Docker-based incidents on a machine you do not mind an agent changing.

## Citation

```
SAI Labs. Incident-Bench: real AI incidents as rerunnable tests. 2026. https://sai.science/incident-bench
```

## License

MIT. See `LICENSE`. Quoted excerpts in `assets/` come from the public sources cited in each `incident.yaml` and remain the property of their authors.
