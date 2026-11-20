"""`ib` command line: list / validate / smoke / mini / calibrate / run / report / models / prices."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config, instances, models, pricing, runner


def _tests_file(iid: str) -> Path:
    return config.path("tests") / f"{iid}.json"


def _update_tests(iid: str, key: str, value: dict) -> None:
    f = _tests_file(iid)
    d = json.loads(f.read_text()) if f.exists() else {}
    d[key] = value
    runner.write_json(f, d)


def _kv(pairs: list[str] | None) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _print_summary(s: dict) -> None:
    print(f"\n{s['instance']}  target={s['target']}  roles={s['roles']}  cost=${s['usd']:.4f}")
    for lid, ln in s["lanes"].items():
        print(f"  lane {lid} ({ln['scope']}): {ln['n_scored']}/{ln['n_samples']} scored, ${ln['usd']:.4f}")
        for b, r in ln["behaviors"].items():
            print(f"    {b}: {r['text']}  severities={r['severity_counts']}")
        for p in ln["problems"][:5]:
            print(f"    PROBLEM {p}")


# ------------------------------------------------------------------ commands
def cmd_list(a) -> int:
    print(f"{'incident':46} {'date':10} {'lanes':>5} {'docker':6}  title")
    for iid in instances.ids():
        inst = instances.load(iid)
        docker = "yes" if inst.manifest.get("sandbox") == "docker" else "-"
        print(f"{iid:46} {str((inst.incident or {}).get('date', ''))[:10]:10} {len(inst.lanes):>5} {docker:6}  "
              f"{str(inst.manifest.get('title', ''))[:70]}")
    return 0


def _lanes_for(inst, which: str, docker: bool) -> list[str]:
    """Lane ids to run: 'reported' = the reported setting (scope replication, no controls); 'all' = every lane."""
    out = []
    for ln in inst.lanes:
        if which == "reported" and (ln.get("scope") != "replication" or ln.get("elicitation") == "forced" or "control" in ln["id"]):
            continue
        if not docker and ("_cc" in ln["id"] or "_codex" in ln["id"] or "_gemini" in ln["id"] or "_real" in ln["id"]):
            continue
        out.append(ln["id"])
    return out


def cmd_run_all(a) -> int:
    """Run one model on every incident (or --ids) and print one line per incident for the primary behavior."""
    ids = a.ids or instances.ids()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    key = models.resolve(a.model).key.replace("/", "_").replace(":", "_")
    outdir = config.path("root") / "results" / f"{key}-{stamp}"
    rows = []
    for iid in ids:
        inst = instances.load(iid)
        lanes = _lanes_for(inst, a.lanes, not a.no_docker)
        if not lanes:
            print(f"{iid}: no lanes selected (try --lanes all, or allow Docker)")
            continue
        try:
            out = runner.run(iid, model=a.model, lanes=lanes, epochs=a.epochs, tag="run", display="none")
            s = runner.summarize(out)
        except Exception as ex:  # noqa: BLE001
            print(f"{iid}: FAILED {type(ex).__name__}: {str(ex)[:200]}")
            continue
        runner.write_json(outdir / f"{iid}.json", s)
        primary = (inst.manifest.get("metric") or {}).get("primary")
        k = sum(ln["behaviors"].get(primary, {}).get("k", 0) for ln in s["lanes"].values())
        n = sum(ln["behaviors"].get(primary, {}).get("n", 0) for ln in s["lanes"].values())
        rows.append((iid, primary, k, n, s["usd"]))
        print(f"{iid:46} {primary:34} {k}/{n}  ${s['usd']:.3f}", flush=True)
    total = sum(r[4] for r in rows)
    print(f"\n{len(rows)} incidents, primary behavior in {sum(r[2] for r in rows)}/{sum(r[3] for r in rows)} runs, cost ${total:.2f}")
    print("results:", outdir)
    return 0


def cmd_tells(a) -> int:
    from . import harness
    hits = harness.tells(instances.load(a.instance).dir)
    for h in hits:
        print(f"{h['file']}:{h['line']}: [{h['match']}] {h['text']}")
    print(f"{len(hits)} possible evaluation tell(s) in target-visible files")
    return 0


def cmd_validate(a) -> int:
    errs = instances.validate(instances.load(a.instance))
    for e in errs:
        print("INVALID:", e)
    print("ok" if not errs else f"{len(errs)} problem(s)")
    return 0 if not errs else 1


def cmd_smoke(a) -> int:
    """Validate + offline pytest + every lane on the mock model with mock roles (free)."""
    inst = instances.load(a.instance)
    errs = instances.validate(inst)
    res = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "validate": errs}
    py = subprocess.run([sys.executable, "-m", "pytest", "-q", str(inst.dir / "tests")], capture_output=True, text=True,
                        cwd=inst.dir)
    res["pytest"] = {"rc": py.returncode, "tail": py.stdout[-1500:]}
    lanes_ok = {}
    if not errs:
        roles = {r: "mock" for r in (inst.manifest.get("roles") or {})}
        smoke_args = inst.manifest.get("smoke_task_args") or {}
        skipped: dict[str, str] = {}
        summaries = {}
        for ln in inst.lanes:
            try:
                out = runner.run(inst.id, model="mock", roles=roles, lanes=[ln["id"]], limit=int(config.get("tests.smoke_n", 2)),
                                 epochs=1, task_args=smoke_args, tag="smoke")
                sl = runner.summarize(out)["lanes"][ln["id"]]
                summaries[ln["id"]] = sl
                lanes_ok[ln["id"]] = sl["status"] == "success" and sl["n_samples"] > 0 and not sl["problems"]
            except Exception as ex:  # noqa: BLE001
                msg = f"{type(ex).__name__}: {ex}"
                if any(m in msg for m in ("prepare.py", "gated", "not prepared")):
                    skipped[ln["id"]] = msg[:200]  # needs data a fresh checkout does not have yet
                else:
                    lanes_ok[ln["id"]] = False
                    res.setdefault("exceptions", {})[ln["id"]] = msg[:400]
        res["summary"] = {"lanes": summaries}
        res["skipped"] = skipped
    ok = not errs and py.returncode == 0 and (bool(lanes_ok) or bool(res.get("skipped"))) and all(lanes_ok.values())
    res.update(ok=ok, lanes=lanes_ok)
    _update_tests(inst.id, "smoke", res)
    print(json.dumps({k: v for k, v in res.items() if k != "summary"}, indent=1, default=str)[:4000])
    if ok and res.get("skipped"):
        print(f"SMOKE PASS ({len(res['skipped'])} lane(s) skipped: run `python prepare.py` in the instance, or add restricted data)")
    else:
        print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_mini(a) -> int:
    """n real samples per lane with the default (or given) target and the instance's judges; budget-capped."""
    inst = instances.load(a.instance)
    mini = inst.manifest.get("mini") or {}
    budget = min(float(mini.get("budget_usd") or config.get("tests.mini_budget_usd")),
                 float(config.get("tests.max_mini_budget_usd")))
    model = a.model or mini.get("target") or config.get("models.target")
    n = a.n or int(mini.get("n_per_lane") or config.get("tests.mini_n"))
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    log_dir = config.path("logs") / inst.id / f"mini-{stamp}-{os.getpid()}"
    lanes, spent, summaries, problems = a.lanes or [ln["id"] for ln in inst.lanes], 0.0, {}, []
    for lid in lanes:
        if spent >= budget:
            problems.append(f"budget ${budget} reached before lane {lid}")
            break
        out = runner.run(inst.id, model=model, lanes=[lid], roles=_kv(a.role), limit=n, epochs=1,
                         task_args=_kv(a.task_arg), shuffle_seed=int(config.get("tests.shuffle_seed")),
                         log_dir=log_dir, tag="mini")
        s = runner.summarize(out)
        summaries[lid] = s["lanes"][lid]
        spent += s["usd"]
        target_label, role_labels = s["target"], s["roles"]
    for lid, ln in summaries.items():
        if ln["status"] != "success":
            problems.append(f"lane {lid}: eval status {ln['status']}")
        if ln["n_scored"] < min(n, ln["n_samples"]) or ln["n_samples"] == 0:
            problems.append(f"lane {lid}: {ln['n_scored']}/{ln['n_samples']} samples scored")
        for p in ln["problems"]:
            problems.append(f"lane {lid}: sample {p['id']} {p['problem']} {p['detail'][:120]}")
    proj = {}
    for lid, ln in summaries.items():
        lane = inst.lane(lid)
        per = ln["usd"] / max(1, ln["n_samples"])
        proj[lid] = round(per * int(lane.get("n_samples") or 0) * int(lane.get("epochs") or 1), 3)
    res = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "target": model, "n_per_lane": n,
           "budget_usd": budget, "usd": round(spent, 5), "over_budget": spent > budget, "log_dir": str(log_dir),
           "lanes": summaries, "problems": problems, "projected_full_usd": proj,
           "projected_full_total_usd": round(sum(proj.values()), 3), "ok": not problems and spent <= budget}
    if not a.no_record:
        default_model = not a.model or a.model == (mini.get("target") or config.get("models.target"))
        all_lanes = not a.lanes or set(a.lanes) == {ln["id"] for ln in inst.lanes}
        # only a full default-target mini is the canonical record (verification and calibration read it)
        key = "mini" if default_model and all_lanes and not a.n else \
            f"mini:{a.model or 'default'}" + ("" if all_lanes else ":" + "+".join(sorted(a.lanes)))
        _update_tests(inst.id, key, res)
    _print_summary({"instance": inst.id, "target": model, "roles": locals().get("role_labels", {}), "usd": spent,
                    "lanes": summaries})
    print(f"projected full run (this target): ${res['projected_full_total_usd']}  {proj}")
    for p in problems:
        print("PROBLEM:", p)
    print("MINI", "PASS" if res["ok"] else "FAIL")
    return 0 if res["ok"] else 1


def cmd_calibrate(a) -> int:
    from . import calibrate
    res = calibrate.calibrate(a.instance, judge_model=a.judge, auditor_model=a.auditor, log_dir=a.log_dir,
                              skip_live=a.skip_live)
    print(calibrate.report_md(res))
    print("CALIBRATION", "PASS" if res["ok"] else "FAIL")
    return 0 if res["ok"] else 1


def cmd_run(a) -> int:
    out = runner.run(a.instance, model=a.model, lanes=a.lanes, roles=_kv(a.role), limit=a.limit, epochs=a.epochs,
                     task_args=_kv(a.task_arg), tag="run", display="none")
    s = runner.summarize(out)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    f = config.path("root") / "state" / "results" / a.instance / f"{models.resolve(a.model).key.replace('/', '_').replace(':', '_')}-{stamp}.json"
    runner.write_json(f, s)
    _print_summary(s)
    print("results:", f)
    return 0


def cmd_report(a) -> int:
    from inspect_ai.log import read_eval_log
    inst = instances.load(a.instance)
    for f in sorted(Path(a.log_dir).rglob("*.eval")):
        log = read_eval_log(str(f))
        lid = (log.eval.metadata or {}).get("ib_lane") or f.parent.name
        s = runner.summarize_log(log, inst.lane(lid))
        print(lid, json.dumps(s["behaviors"], default=str))
    return 0


def cmd_models(a) -> int:
    import asyncio
    config.setup_runtime_env()
    ms = models.resolve(a.spec)
    m = models.get_model(ms)
    out = asyncio.run(m.generate("Reply with the single word: pong"))
    print(ms.label(), "->", repr(out.completion[:80]), out.usage)
    return 0


def cmd_prices(a) -> int:
    config.setup_runtime_env()
    p = pricing.price_for(models.resolve(a.spec))
    print(f"input ${p.input * 1e6:.3f}/M  output ${p.output * 1e6:.3f}/M  cached {p.cached_input}  source: {p.source}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ib", description="incident-bench: rebuilt AI incidents as sandboxed evaluations")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("tells"); s.add_argument("instance")
    s = sub.add_parser("run-all", help="run one model on every incident")
    s.add_argument("--model", required=True); s.add_argument("--ids", nargs="*")
    s.add_argument("--lanes", choices=["reported", "all"], default="reported")
    s.add_argument("--epochs", type=int); s.add_argument("--no-docker", action="store_true")
    for name in ("validate", "smoke"):
        s = sub.add_parser(name); s.add_argument("instance")
    s = sub.add_parser("mini"); s.add_argument("instance"); s.add_argument("--model"); s.add_argument("--n", type=int)
    s.add_argument("--lanes", nargs="*"); s.add_argument("--role", action="append", help="role=model")
    s.add_argument("--task-arg", action="append", help="key=value"); s.add_argument("--no-record", action="store_true")
    s = sub.add_parser("calibrate"); s.add_argument("instance"); s.add_argument("--judge"); s.add_argument("--auditor")
    s.add_argument("--log-dir"); s.add_argument("--skip-live", action="store_true")
    s = sub.add_parser("run"); s.add_argument("instance"); s.add_argument("--model", required=True)
    s.add_argument("--lanes", nargs="*"); s.add_argument("--role", action="append"); s.add_argument("--limit", type=int)
    s.add_argument("--epochs", type=int); s.add_argument("--task-arg", action="append")
    s = sub.add_parser("report"); s.add_argument("instance"); s.add_argument("log_dir")
    s = sub.add_parser("models"); s.add_argument("action", choices=["check"]); s.add_argument("spec")
    s = sub.add_parser("prices"); s.add_argument("action", choices=["show"]); s.add_argument("spec")
    a = ap.parse_args(argv)
    config.setup_runtime_env()
    return {"list": cmd_list, "tells": cmd_tells, "run-all": cmd_run_all, "validate": cmd_validate, "smoke": cmd_smoke, "mini": cmd_mini, "calibrate": cmd_calibrate,
            "run": cmd_run, "report": cmd_report, "models": cmd_models, "prices": cmd_prices}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
