"""Run an instance's lanes with resolved target + role models; summarise logs into rates with CIs and cost."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from . import config, instances, models, pricing, stats


def role_specs(inst: instances.Instance, overrides: dict[str, str] | None = None) -> dict[str, models.ModelSpec]:
    """Model spec for each non-target role: CLI override > manifest default > config models.<role>."""
    overrides = overrides or {}
    out = {}
    for role, r in (inst.manifest.get("roles") or {}).items():
        spec = overrides.get(role) or (r or {}).get("default") or config.get(f"models.{role}")
        if spec:
            out[role] = models.resolve(spec, role=role)
    for role, spec in overrides.items():
        out.setdefault(role, models.resolve(spec, role=role))
    return out


def run(instance_id: str, model: str | None = None, lanes: list[str] | None = None, roles: dict[str, str] | None = None,
        limit: int | None = None, epochs: int | None = None, task_args: dict | None = None,
        shuffle_seed: int | None = None, log_dir: Path | None = None, tag: str = "run",
        max_connections: int | None = None, display: str = "none", **eval_kwargs) -> dict[str, Any]:
    """Run the selected lanes (default: all). Returns {"instance", "target", "roles", "logs": {lane: EvalLog}}."""
    from inspect_ai import eval as inspect_eval

    config.setup_runtime_env()
    inst = instances.load(instance_id)
    target = models.resolve(model, role="target")
    rspecs = role_specs(inst, roles)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    log_dir = log_dir or (config.path("logs") / instance_id / f"{tag}-{target.key.replace('/', '_').replace(':', '_')}-{stamp}-{os.getpid()}")
    lane_ids = lanes or [ln["id"] for ln in inst.lanes]
    out = {"instance": inst, "target": target, "roles": rspecs, "logs": {}, "log_dir": str(log_dir)}
    model_roles = {r: models.get_model(s, role=r) for r, s in rspecs.items()} or None
    for lid in lane_ids:
        ln = inst.lane(lid)
        task = inst.load_task(lid, **(task_args or {}))
        logs = inspect_eval(
            task, model=models.get_model(target), model_roles=model_roles, limit=limit,
            epochs=epochs if epochs is not None else ln.get("epochs", 1), sample_shuffle=shuffle_seed,
            log_dir=str(Path(log_dir) / lid), max_connections=max_connections or config.get("tests.max_connections", 8),
            display=display, fail_on_error=False, metadata={"ib_instance": instance_id, "ib_lane": lid}, **eval_kwargs)
        out["logs"][lid] = logs[0]
    return out


# ------------------------------------------------------------------ summaries
def _price_map(target: models.ModelSpec, rspecs: dict[str, models.ModelSpec]) -> dict[str, pricing.Price]:
    pm = {}
    specs = [target, *rspecs.values()]
    fb = config.get("models.judge_fallback")
    if fb:
        specs.append(models.resolve(fb, role="judge"))
    for s in specs:
        if s.provider == "mock":
            pm[s.inspect_name] = pricing.Price(0, 0, source="mock")
            continue
        try:
            pm[s.inspect_name] = pricing.price_for(s)
        except Exception:  # noqa: BLE001
            pass
    return pm


def log_cost(log, price_map: dict[str, pricing.Price]) -> tuple[float, dict]:
    usd, by = 0.0, {}
    for name, u in ((log.stats.model_usage if log.stats else {}) or {}).items():
        p = price_map.get(name) or next((v for k, v in price_map.items() if name.endswith(k.split("/", 1)[-1])), None)
        c = p.cost(u) if p else 0.0
        by[name] = {"usd": round(c, 5), "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                    "priced": p is not None}
        usd += c
    return usd, by


def summarize_log(log, lane: dict, price_map: dict | None = None) -> dict:
    samples = log.samples or []
    bids = list(lane.get("behaviors") or [])
    vals = [v for x in samples for v in [next(iter((x.scores or {}).values()), None)] if v and isinstance(v.value, dict)]
    if any("eval_awareness" in v.value for v in vals):
        bids.append("eval_awareness")  # standard auxiliary measure (STANDARDS §5.9)
    counts = {b: [0, 0] for b in bids}
    sev = {b: {} for b in bids}
    problems = []
    n_ok = 0
    for s in samples:
        sc = next(iter((s.scores or {}).values()), None)
        if s.error or sc is None or not isinstance(sc.value, dict):
            problems.append({"id": s.id, "epoch": s.epoch, "problem": "error" if s.error else "unscored",
                             "detail": str(s.error.message if s.error else "")[:300]})
            continue
        if sc.value.get("judge_ok", 1) != 1:
            problems.append({"id": s.id, "epoch": s.epoch, "problem": "judge_failed",
                             "detail": str((sc.metadata or {}).get("judge_error"))[:300]})
        n_ok += 1
        for b in bids:
            if b in sc.value:
                counts[b][1] += 1
                counts[b][0] += int(sc.value[b] >= 1)
                lv = int(sc.value.get(f"{b}__sev", 0))
                sev[b][lv] = sev[b].get(lv, 0) + 1
    usd, by = log_cost(log, price_map or {})
    return {"lane": lane["id"], "scope": lane.get("scope"), "status": log.status, "n_samples": len(samples),
            "n_scored": n_ok, "problems": problems,
            "behaviors": {b: {**stats.rate(*counts[b]), "severity_counts": dict(sorted(sev[b].items()))} for b in bids},
            "usd": round(usd, 5), "cost_by_model": by, "log": log.location}


def summarize(result: dict) -> dict:
    inst = result["instance"]
    pm = _price_map(result["target"], result["roles"])
    lanes = {lid: summarize_log(log, inst.lane(lid), pm) for lid, log in result["logs"].items()}
    return {"instance": inst.id, "target": result["target"].label(),
            "roles": {r: s.label() for r, s in result["roles"].items()},
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "lanes": lanes,
            "usd": round(sum(v["usd"] for v in lanes.values()), 5), "log_dir": result["log_dir"]}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
