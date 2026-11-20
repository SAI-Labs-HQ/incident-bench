"""Instance discovery, loading and validation (schema in STANDARDS.md §3).

An instance is a directory `instances/<id>/` holding `incident.yaml` (the incident record and its claims) and
`manifest.yaml` (lanes, behaviors, roles, calibration, fidelity labels), plus `task.py`, `judge/rubric.md`,
`calibration/gold.jsonl`, `tests/`, `README.md` (plus any files listed in `validation.extra_required_files`).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import config

KINDS = {"chat_single", "chat_multi", "agentic_tool", "agentic_sandbox"}
SANDBOXES = {"none", "docker"}
# Realism (STANDARDS §1.8): what the target model actually meets.
REAL_TRACES = {"real", "partial", "reconstructed"}       # conversation / data the incident involved
REAL_ENV = {"real", "docker", "simulated"}               # real service | real Docker environment | Python simulator
REAL_HARNESS = {"product", "stand-in", "tool-loop"}      # the actual agent product | another real harness | our loop
SCOPES = {"replication", "variants", "general"}
LABELS = {"exact", "stand-in", "ours"}
ELICITATION = {"spontaneous", "induced", "forced"}
BUILD = {"built", "partial", "blocked", "wip"}
CLAIM_STATUS = {"stated", "inferred", "missing"}
OUTCOMES = {"reproduced", "partially_reproduced", "not_reproduced", "not_testable", "untested"}
CLAIM_FIELDS = ("system", "setting", "trigger", "behavior", "frequency")


@dataclass
class Instance:
    id: str
    dir: Path
    manifest: dict
    incident: dict
    errors: list[str] = field(default_factory=list)

    @property
    def lanes(self) -> list[dict]:
        return self.manifest.get("lanes") or []

    @property
    def behaviors(self) -> list[dict]:
        return self.manifest.get("behaviors") or []

    def lane(self, lane_id: str) -> dict:
        for ln in self.lanes:
            if ln["id"] == lane_id:
                return ln
        raise KeyError(f"{self.id}: no lane '{lane_id}' (have {[ln['id'] for ln in self.lanes]})")

    def incident_summary(self) -> str:
        inc = self.incident or {}
        parts = [f"{inc.get('title', self.id)} (incident date {inc.get('date', '?')})", (inc.get("summary") or "").strip()]
        claims = inc.get("claims") or []
        if claims:
            parts.append("Reported claims:\n" + "\n".join(f"- {c.get('id')}: {c.get('text')}" for c in claims))
        return "\n".join(p for p in parts if p)

    def load_task(self, lane_id: str, **task_args):
        """Import task.py (or the lane's own `task: file.py:fn`) and call the lane's task function."""
        ln = self.lane(lane_id)
        ref = ln.get("task") or self.manifest.get("task")
        file, _, fn = ref.partition(":")
        mod = _import(self.dir / file, f"ib_instance_{self.id.replace('-', '_')}_{Path(file).stem}")
        args = {**(ln.get("task_args") or {}), **task_args}
        return getattr(mod, fn)(**args)


def _import(path: Path, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    sys.modules[name] = mod
    return mod


def root() -> Path:
    return config.path("instances")


def ids() -> list[str]:
    return sorted(p.name for p in root().iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "manifest.yaml").exists())


def _yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text()) or {} if p.exists() else {}


def load_dir(d: Path) -> Instance:
    return Instance(id=d.name, dir=d, manifest=_yaml(d / "manifest.yaml"), incident=_yaml(d / "incident.yaml"))


def load(instance_id: str) -> Instance:
    d = root() / instance_id
    if not d.is_dir():
        raise KeyError(f"no instance '{instance_id}' under {root()}")
    return load_dir(d)


# ------------------------------------------------------------------ validation
def _req(d: dict, key: str, errs: list, where: str):
    if d.get(key) in (None, "", [], {}):
        errs.append(f"{where}: missing '{key}'")
    return d.get(key)


def validate(inst: Instance) -> list[str]:
    """Static checks of files and schema. Returns a list of problems (empty = valid)."""
    e: list[str] = []
    d, m, inc = inst.dir, inst.manifest, inst.incident
    for f in ("incident.yaml", "manifest.yaml", "README.md", "judge/rubric.md", "calibration/gold.jsonl",
              *(config.get("validation.extra_required_files") or [])):
        if not (d / f).exists():
            e.append(f"missing file {f}")
    if not list((d / "tests").glob("test_*.py")):
        e.append("missing tests/test_*.py")

    # incident.yaml
    for k in ("id", "title", "date", "summary", "sources", "claims", "taxonomy", "selection"):
        _req(inc, k, e, "incident.yaml")
    if inc.get("id") and inc["id"] != inst.id:
        e.append(f"incident.yaml id '{inc['id']}' != directory '{inst.id}'")
    for i, s in enumerate(inc.get("sources") or []):
        for k in ("url", "type"):
            _req(s, k, e, f"incident.sources[{i}]")
    for i, c in enumerate(inc.get("claims") or []):
        where = f"incident.claims[{i}]"
        for k in ("id", "text", "source"):
            _req(c, k, e, where)
        fields = c.get("fields") or {}
        for fk in CLAIM_FIELDS:
            f = fields.get(fk)
            if not isinstance(f, dict) or f.get("status") not in CLAIM_STATUS:
                e.append(f"{where}.fields.{fk}: need {{value, status: stated|inferred|missing}}")
    tax = inc.get("taxonomy") or {}
    for k in ("mit_domain", "mit_subdomain", "behavior_class"):
        _req(tax, k, e, "incident.taxonomy")

    # manifest.yaml
    for k in ("id", "title", "kind", "sandbox", "task", "lanes", "behaviors", "metric", "roles", "fidelity", "status"):
        _req(m, k, e, "manifest.yaml")
    if m.get("id") and m["id"] != inst.id:
        e.append(f"manifest id '{m['id']}' != directory '{inst.id}'")
    if m.get("kind") and m["kind"] not in KINDS:
        e.append(f"manifest.kind '{m['kind']}' not in {sorted(KINDS)}")
    if m.get("sandbox") and m["sandbox"] not in SANDBOXES:
        e.append(f"manifest.sandbox '{m['sandbox']}' not in {sorted(SANDBOXES)}")
    bids = set()
    for i, b in enumerate(m.get("behaviors") or []):
        where = f"manifest.behaviors[{i}]"
        _req(b, "id", e, where); _req(b, "definition", e, where); _req(b, "severity", e, where)
        bids.add(b.get("id"))
        for s in b.get("severity") or []:
            if not isinstance(s, dict) or "level" not in s or "label" not in s:
                e.append(f"{where}.severity entries need level + label")
    scopes = set()
    for i, ln in enumerate(m.get("lanes") or []):
        where = f"manifest.lanes[{i}]"
        for k in ("id", "scope", "description", "behaviors", "labels", "n_samples", "epochs"):
            _req(ln, k, e, where)
        if ln.get("scope") and ln["scope"] not in SCOPES:
            e.append(f"{where}.scope '{ln['scope']}' not in {sorted(SCOPES)}")
        scopes.add(ln.get("scope"))
        if ln.get("elicitation", "spontaneous") not in ELICITATION:
            e.append(f"{where}.elicitation not in {sorted(ELICITATION)}")
        for k, v in (ln.get("labels") or {}).items():
            if v not in LABELS:
                e.append(f"{where}.labels.{k} '{v}' not in {sorted(LABELS)}")
        for b in ln.get("behaviors") or []:
            if b not in bids:
                e.append(f"{where}: behavior '{b}' not defined in manifest.behaviors")
    if m.get("lanes") and "replication" not in scopes:
        e.append("manifest: needs at least one lane with scope 'replication' (the reported setting)")
    if m.get("lanes") and "general" not in scopes:
        e.append("manifest: needs at least one lane with scope 'general' (the behavior class on any model)")
    met = m.get("metric") or {}
    _req(met, "primary", e, "manifest.metric"); _req(met, "direction", e, "manifest.metric")
    if met.get("primary") and met["primary"] not in bids:
        e.append(f"manifest.metric.primary '{met['primary']}' is not a behavior id")
    roles = m.get("roles") or {}
    if "judge" not in roles:
        e.append("manifest.roles: needs 'judge'")
    for r, spec in roles.items():
        if not isinstance(spec, dict) or not spec.get("default"):
            e.append(f"manifest.roles.{r}: needs default")
    fid = m.get("fidelity") or {}
    for k in ("exact", "stand_in", "ours", "assumptions"):
        if k not in fid:
            e.append(f"manifest.fidelity: missing '{k}' (use [] if none)")
    rl = m.get("realism")
    if not isinstance(rl, dict):
        e.append("manifest.realism: missing (traces, environment, harness, why_not_higher); see STANDARDS §1.8")
    else:
        for k, allowed in (("traces", REAL_TRACES), ("environment", REAL_ENV), ("harness", REAL_HARNESS)):
            if rl.get(k) not in allowed:
                e.append(f"manifest.realism.{k} must be one of {sorted(allowed)}")
        if "why_not_higher" not in rl:
            e.append("manifest.realism.why_not_higher: say what public material or constraint stops a higher level")
        if rl.get("environment") in ("docker", "real") and m.get("sandbox") != "docker":
            e.append("manifest.realism.environment docker/real needs manifest.sandbox: docker")
    st = m.get("status") or {}
    if st.get("build") not in BUILD:
        e.append(f"manifest.status.build must be one of {sorted(BUILD)}")
    if (st.get("outcome") or "untested") not in OUTCOMES:
        e.append(f"manifest.status.outcome must be one of {sorted(OUTCOMES)}")

    # gold set
    g = d / "calibration" / "gold.jsonl"
    if g.exists():
        try:
            items = load_gold(g)
        except Exception as ex:  # noqa: BLE001
            e.append(f"calibration/gold.jsonl unreadable: {ex}")
            items = []
        nmin = int(config.get("calibration.min_gold_items", 12))
        if len(items) < nmin:
            e.append(f"calibration/gold.jsonl has {len(items)} items; need >= {nmin}")
        for it in items:
            for bid in (it.get("expected") or {}):
                if bid not in bids:
                    e.append(f"gold {it.get('id')}: unknown behavior '{bid}'")
        pos = sum(1 for it in items if any(v.get("present") for v in (it.get("expected") or {}).values()))
        if items and (pos == 0 or pos == len(items)):
            e.append("calibration/gold.jsonl needs both positive and negative items")
        # every gold item names the lane (setting) it comes from; the general lane needs its own positives and negatives
        lane_scope = {ln.get("id"): ln.get("scope") for ln in m.get("lanes") or []}
        missing = [it.get("id") for it in items if it.get("lane") not in lane_scope]
        if missing:
            e.append(f"gold items need a 'lane' field naming a manifest lane: {missing[:8]}{' ...' if len(missing) > 8 else ''}")
        gen = [it for it in items if lane_scope.get(it.get("lane")) == "general"]
        gpos = sum(1 for it in gen if any(v.get("present") for v in (it.get("expected") or {}).values()))
        need = int(config.get("calibration.min_general_gold", 3))
        if items and (len(gen) < need or gpos == 0 or gpos == len(gen)):
            e.append(f"gold needs >= {need} items from the general lane's own setting, with positives and negatives "
                     f"(has {len(gen)}, {gpos} positive)")

    # task functions exist
    if m.get("lanes") and (d / (m.get("task") or "task.py").split(":")[0]).exists():
        src = {}
        for ln in m["lanes"]:
            file, _, fn = (ln.get("task") or m.get("task", "")).partition(":")
            if file not in src:
                src[file] = (d / file).read_text() if (d / file).exists() else ""
            if fn and f"def {fn}(" not in src[file]:
                e.append(f"lane {ln.get('id')}: task function '{fn}' not found in {file}")
    return e


def load_gold(path: Path) -> list[dict]:
    items = []
    for i, line in enumerate(path.read_text().splitlines()):
        if not line.strip():
            continue
        it = json.loads(line)
        for k in ("id", "messages", "expected"):
            if k not in it:
                raise ValueError(f"line {i + 1}: missing '{k}'")
        items.append(it)
    return items


def status_row(inst: Instance) -> dict[str, Any]:
    tests = config.path("tests") / f"{inst.id}.json"
    cal = config.path("calibration") / f"{inst.id}.json"
    t = json.loads(tests.read_text()) if tests.exists() else {}
    c = json.loads(cal.read_text()) if cal.exists() else {}
    return {"id": inst.id, "build": (inst.manifest.get("status") or {}).get("build"),
            "outcome": (inst.manifest.get("status") or {}).get("outcome", "untested"),
            "smoke": (t.get("smoke") or {}).get("ok"), "mini": (t.get("mini") or {}).get("ok"),
            "calibration": c.get("ok"), "mini_usd": (t.get("mini") or {}).get("usd")}
