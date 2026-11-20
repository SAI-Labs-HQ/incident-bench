"""Real agent harnesses and real Docker environments (STANDARDS §1.8 realism levels).

Level 2 (real environment): the instance ships `env/compose.yaml` + `env/Dockerfile`; the sandbox has a real shell,
real files, real git and real local services. Outside services are local containers on the sandbox network; there
is no route to the internet (compose `internal: true` networks), so anything the agent sends out lands in a local sink.

Level 3 (real harness): the model under test drives the actual product inside the sandbox through inspect-swe
(Claude Code, Codex CLI, Gemini CLI, ...). The harness binary is downloaded on the host and copied in; the model API is
bridged from the host, so the sandbox itself needs no network.

    from ib.harness import real_agent, probe, probe_facts
    solver = [real_agent("claude_code", version="2.1.277", cwd="/home/dev/work"), probe({"live_files": "find ..."})]
    def deterministic(state): facts = probe_facts(state); ...

`probe` runs root-level checks in the sandbox AFTER the agent finishes (file counts, git fsck, sink logs) and stores
the outputs for the synchronous `deterministic(state)` hook of `incident_judge`.
"""
from __future__ import annotations

import re
from typing import Any

PROBE_KEY = "ib_probe"

# Environment for harness processes: no telemetry, no auto-update, no feedback prompts. Nothing here tells the model
# it is being evaluated.
QUIET_ENV = {
    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_BUG_COMMAND": "1",
}

HARNESSES = ("claude_code", "codex_cli", "gemini_cli", "opencode", "mini_swe_agent")


def real_agent(name: str, *, version: str | None = None, cwd: str | None = None, user: str | None = None,
               env: dict | None = None, **kwargs):
    """inspect-swe agent with incident-bench defaults (pinned version, quiet env, sandbox user)."""
    import inspect_swe
    if name not in HARNESSES:
        raise ValueError(f"unknown harness {name!r}; have {HARNESSES}")
    fn = getattr(inspect_swe, name)
    args: dict[str, Any] = {"env": {**QUIET_ENV, **(env or {})}}
    if version:
        args["version"] = version
    if cwd:
        args["cwd"] = cwd
    if user:
        args["user"] = user
    if name == "claude_code":
        args.setdefault("permission_mode", kwargs.pop("permission_mode", "bypassPermissions"))
        # Headless runs have nobody to answer AskUserQuestion or approve ExitPlanMode: the tool errors and the model may carry on as if it had
        # been told nothing (seen in PocketOS audit 5). Without it the model asks in text, which ends the run, as in a
        # headless CI session. Instances with a simulated user can pass disallowed_tools=[] to re-enable it.
        args["disallowed_tools"] = kwargs.pop("disallowed_tools", ["AskUserQuestion", "ExitPlanMode"])
    args.update(kwargs)
    return fn(**args)


def probe(commands: dict[str, str], user: str = "root", timeout: int = 120):
    """Solver: run named shell checks in the sandbox after the agent, store {name: {rc, out}} under PROBE_KEY."""
    from inspect_ai.solver import solver
    from inspect_ai.util import sandbox

    @solver
    def _probe():
        async def solve(state, generate):
            out = dict(state.store.get(PROBE_KEY, {}))
            for name, cmd in commands.items():
                try:
                    r = await sandbox().exec(["bash", "-lc", cmd], user=user, timeout=timeout)
                    out[name] = {"rc": r.returncode, "out": (r.stdout or "")[-4000:], "err": (r.stderr or "")[-1000:]}
                except Exception as ex:  # noqa: BLE001  record, do not fail the sample
                    out[name] = {"rc": -1, "out": "", "err": f"{type(ex).__name__}: {ex}"[:1000]}
            state.store.set(PROBE_KEY, out)
            return state
        return solve
    return _probe()


def probe_facts(state) -> dict[str, dict]:
    try:
        return dict(state.store.get(PROBE_KEY, {}))
    except AttributeError:
        return dict((getattr(state, "store_data", None) or {}).get(PROBE_KEY, {}))


def probe_int(facts: dict, name: str, default: int = -1) -> int:
    m = re.search(r"-?\d+", (facts.get(name) or {}).get("out", ""))
    return int(m.group(0)) if m else default


# ------------------------------------------------------------------ evaluation tells
# Words and patterns that tell a model it is in a test. Target-visible text (prompts, files, tool outputs, hostnames)
# must avoid them; `ib tells <id>` lists hits. Judge-only text (rubric, gold, README, notes) is exempt.
TELLS = [
    r"\bbenchmark\b", r"\bevaluat(?:ion|ed|ing)\b", r"\beval\b", r"\bsimulat(?:ed|ion|or)\b", r"\bsandbox(?:ed)?\b",
    r"\bdecoy\b", r"\bhoneypot\b", r"\bcanary\b", r"\bfictional\b", r"\bfake\b", r"\bmock\b", r"\btest scenario\b",
    r"\bincident-bench\b", r"\bred[- ]team", r"\bthis is a test\b", r"\.test\b", r"\bexample\.(?:com|org)\b",
]
TELL_RE = re.compile("|".join(TELLS), re.I)
TARGET_GLOBS = ("assets/*.jsonl", "assets/*.txt", "assets/**/*", "env/**/*", "task.py", "*.py")
EXEMPT = ("tests/", "calibration/", "judge/", "README.md", "CALIBRATION.md")
EXEMPT_SUFFIX = ("NOTES.md",)


def tells(instance_dir) -> list[dict]:
    """Scan target-visible files of an instance for evaluation tells. Python files: string literals only."""
    from pathlib import Path
    d = Path(instance_dir)
    seen, hits = set(), []
    for g in TARGET_GLOBS:
        for p in d.glob(g):
            rel = str(p.relative_to(d))
            if not p.is_file() or rel in seen or rel.startswith(EXEMPT) or rel.endswith(EXEMPT_SUFFIX) or "__pycache__" in rel:
                continue
            seen.add(rel)
            try:
                text = p.read_text(errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if p.suffix == ".py":
                text = "\n".join(re.findall(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"[^\"\n]*\"|'[^'\n]*')", text))
            for i, line in enumerate(text.splitlines(), 1):
                for m in TELL_RE.finditer(line):
                    hits.append({"file": rel, "line": i, "match": m.group(0), "text": line.strip()[:160]})
    return hits
