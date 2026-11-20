"""Judge calibration: (1) gold set, (2) independent auditor on live transcripts.

1. Gold: `calibration/gold.jsonl` holds hand-labelled transcripts (clear positives, clear negatives, near-misses,
   the incident's own released trace when there is one). The instance's judge must reach
   `calibration.min_gold_accuracy` on the behavior verdict and make no error on items marked `critical`.
2. Live audit: every sample of the latest mini run is re-judged by the `auditor` model (a different family from
   the judge, same rubric and same deterministic facts). Agreement below `calibration.min_audit_agreement`, or any
   disagreement on a positive, is reported for human/agent review in CALIBRATION.md.
Results go to state/calibration/<id>.json and instances/<id>/CALIBRATION.md.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

from . import config, instances, judge, models, runner


def _judge_spec(inst: instances.Instance, override: str | None) -> models.ModelSpec:
    spec = override or ((inst.manifest.get("roles") or {}).get("judge") or {}).get("default") or config.get("models.judge")
    return models.resolve(spec, role="judge")


async def _gather(coros, limit: int = 8):
    sem = asyncio.Semaphore(limit)

    async def one(c):
        async with sem:
            return await c
    return await asyncio.gather(*(one(c) for c in coros))


def _fallback():
    fb = config.get("models.judge_fallback")
    try:
        return models.get_model(fb) if fb else None
    except Exception:  # noqa: BLE001
        return None


async def gold(inst: instances.Instance, judge_spec: models.ModelSpec) -> dict:
    items = instances.load_gold(inst.dir / "calibration" / "gold.jsonl")
    rubric = judge.load_rubric(inst.dir)
    behaviors = inst.behaviors
    jm, fb = models.get_model(judge_spec), _fallback()

    async def one(it):
        bl = [b for b in behaviors if b["id"] in it["expected"]]
        r = await judge.judge_messages(jm, behaviors=bl, rubric=rubric, incident=inst.incident_summary(),
                                       messages=it["messages"], scenario=it.get("scenario", ""),
                                       facts=it.get("facts", ""), floor=it.get("floor"), fallback_model=fb)
        rows = []
        for bid, exp in it["expected"].items():
            got = r["verdict"]["behaviors"][bid]
            rows.append({"item": it["id"], "behavior": bid, "expected": bool(exp.get("present")),
                         "got": got["present"], "expected_sev": exp.get("severity"), "got_sev": got["severity"],
                         "ok": r["ok"], "critical": bool(it.get("critical")), "kind": it.get("kind", ""),
                         "rationale": r["verdict"].get("rationale", "")[:600], "error": r["error"]})
        return rows

    reps = max(1, int(config.get("calibration.gold_repeats", 3)))
    raw = [x for rs in await _gather([one(it) for it in items for _ in range(reps)]) for x in rs]
    groups: dict[tuple, list] = {}
    for r in raw:
        groups.setdefault((r["item"], r["behavior"]), []).append(r)
    rows = []
    for runs in groups.values():  # majority vote over repeats; non-unanimous = unstable boundary
        okr = [r for r in runs if r["ok"]]
        pres = [r["got"] for r in okr]
        got = sum(pres) * 2 > len(pres) if pres else False
        sevs = [r["got_sev"] for r in okr if r["got"] == got]
        pick = next((r for r in okr if r["got"] != runs[0]["expected"]), okr[0] if okr else runs[0])
        rows.append({**pick, "got": got, "ok": bool(okr), "got_sev": max(set(sevs), key=sevs.count) if sevs else 0,
                     "votes": f"{sum(pres)}/{len(pres)}", "unstable": len(set(pres)) > 1})
    unstable = [r for r in rows if r["unstable"]]
    lane_of = {it["id"]: it.get("lane", "?") for it in items}
    by_lane: dict[str, list] = {}
    for r in rows:
        by_lane.setdefault(lane_of.get(r["item"], "?"), []).append(r["ok"] and r["got"] == r["expected"])
    lane_acc = {ln: f"{sum(v)}/{len(v)}" for ln, v in sorted(by_lane.items())}
    n = len(rows)
    correct = sum(1 for r in rows if r["ok"] and r["got"] == r["expected"])
    sev_rows = [r for r in rows if r["expected"] and r["expected_sev"] is not None and r["ok"]]
    sev_ok = sum(1 for r in sev_rows if r["got_sev"] == r["expected_sev"])
    crit_err = [r for r in rows if r["critical"] and (not r["ok"] or r["got"] != r["expected"] or r["unstable"])]
    fn = sum(1 for r in rows if r["expected"] and not r["got"])
    fp = sum(1 for r in rows if not r["expected"] and r["got"])
    acc = correct / n if n else 0.0
    ok = (n >= int(config.get("calibration.min_gold_items", 12)) and acc >= float(config.get("calibration.min_gold_accuracy"))
          and len(crit_err) <= int(config.get("calibration.max_critical_errors", 0)))
    return {"ok": ok, "judge": judge_spec.label(), "n_items": len(items), "n_verdicts": n, "accuracy": round(acc, 4),
            "false_negatives": fn, "false_positives": fp, "critical_errors": len(crit_err),
            "severity_exact": round(sev_ok / len(sev_rows), 4) if sev_rows else None,
            "repeats": reps, "by_lane": lane_acc, "unstable": [{"item": r["item"], "behavior": r["behavior"], "votes": r["votes"],
                                           "critical": r["critical"]} for r in unstable],
            "errors": [r for r in rows if not r["ok"] or r["got"] != r["expected"]]}


async def live(inst: instances.Instance, auditor_spec: models.ModelSpec, log_dir: str | None) -> dict:
    from inspect_ai.log import read_eval_log

    if not log_dir:
        tests = config.path("tests") / f"{inst.id}.json"
        log_dir = (json.loads(tests.read_text()).get("mini") or {}).get("log_dir") if tests.exists() else None
    if not log_dir or not Path(log_dir).exists():
        return {"ok": None, "skipped": "no mini log to audit (run `ib mini` first)"}
    rubric = judge.load_rubric(inst.dir)
    am, fb = models.get_model(auditor_spec), _fallback()
    jobs = []
    for f in sorted(Path(log_dir).rglob("*.eval")):
        log = read_eval_log(str(f))
        lane = inst.lane((log.eval.metadata or {}).get("ib_lane") or f.parent.name)
        bl = [b for b in inst.behaviors if b["id"] in (lane.get("behaviors") or [])]
        for s in log.samples or []:
            sc = next(iter((s.scores or {}).values()), None)
            if sc is None or not isinstance(sc.value, dict) or sc.value.get("judge_ok") != 1:
                continue
            det = (sc.metadata or {}).get("deterministic") or {}

            async def one(s=s, sc=sc, det=det, bl=bl, lane=lane):
                r = await judge.judge_messages(am, behaviors=bl, rubric=rubric, incident=inst.incident_summary(),
                                               messages=s.messages, scenario=str((s.metadata or {}).get("scenario", "")),
                                               facts=det.get("facts", ""), floor=det.get("floor"), fallback_model=fb)
                out = []
                for b in bl:
                    j = sc.value.get(b["id"], 0) >= 1
                    a = r["verdict"]["behaviors"][b["id"]]["present"] if r["ok"] else None
                    out.append({"lane": lane["id"], "sample": s.id, "epoch": s.epoch, "behavior": b["id"], "judge": j,
                                "auditor": a, "judge_rationale": ((sc.metadata or {}).get("judge") or {}).get("rationale", "")[:500],
                                "auditor_rationale": r["verdict"].get("rationale", "")[:500], "auditor_error": r["error"]})
                return out
            jobs.append(one())
    rows = [x for rs in await _gather(jobs) for x in rs]
    valid = [r for r in rows if r["auditor"] is not None]
    agree = sum(1 for r in valid if r["auditor"] == r["judge"])
    rate = agree / len(valid) if valid else None
    dis = [r for r in valid if r["auditor"] != r["judge"]]
    thr = float(config.get("calibration.min_audit_agreement"))
    per: dict[str, list] = {}
    for r in valid:
        per.setdefault(r["lane"], []).append(r["auditor"] == r["judge"])
    lane_agree = {ln: f"{sum(v)}/{len(v)}" for ln, v in sorted(per.items())}
    gen_lanes = {ln["id"] for ln in inst.lanes if ln.get("scope") == "general"}
    gv = [x for ln, v in per.items() if ln in gen_lanes for x in v]
    gen_ok = (sum(gv) / len(gv) >= thr) if len(gv) >= 3 else True
    ok = None if rate is None else (rate >= thr and gen_ok)
    return {"ok": ok, "auditor": auditor_spec.label(), "log_dir": log_dir, "n": len(valid), "agreement": rate and round(rate, 4),
            "positives_judge": sum(1 for r in valid if r["judge"]), "positives_auditor": sum(1 for r in valid if r["auditor"]),
            "by_lane": lane_agree, "general_ok": gen_ok,
            "disagreements": dis, "auditor_errors": len(rows) - len(valid)}


def calibrate(instance_id: str, judge_model: str | None = None, auditor_model: str | None = None,
              log_dir: str | None = None, skip_live: bool = False) -> dict:
    config.setup_runtime_env()
    inst = instances.load(instance_id)
    js = _judge_spec(inst, judge_model)
    aspec = models.resolve(auditor_model or ((inst.manifest.get("roles") or {}).get("auditor") or {}).get("default")
                           or config.get("models.auditor"), role="auditor")

    async def both():
        g = await gold(inst, js)
        lv = {"ok": None, "skipped": "--skip-live"} if skip_live else await live(inst, aspec, log_dir)
        return g, lv
    g, lv = asyncio.run(both())
    res = {"instance": instance_id, "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "gold": g, "live": lv, "ok": bool(g["ok"]) and lv.get("ok") is not False}
    runner.write_json(config.path("calibration") / f"{instance_id}.json", res)
    (inst.dir / "CALIBRATION.md").write_text(report_md(res))
    return res


def report_md(res: dict) -> str:
    g, lv = res["gold"], res["live"]
    L = [f"# Calibration: {res['instance']}", "", f"Generated by `ib calibrate` at {res['at']}. Overall: **{'PASS' if res['ok'] else 'FAIL'}**.", "",
         "## Gold set (judge vs hand labels)", "",
         f"- Judge: `{g['judge']}`; items {g['n_items']}, verdicts {g['n_verdicts']}",
         f"- Accuracy {g['accuracy']:.1%}; false negatives {g['false_negatives']}; false positives {g['false_positives']}; "
         f"critical errors {g['critical_errors']} (a critical item counts as an error if its repeats disagree); "
         f"severity exact {g['severity_exact']}",
         f"- By lane (correct/total): {g.get('by_lane', {})}",
         f"- Repeats per item: {g.get('repeats', 1)}; unstable items (repeats disagree): "
         f"{', '.join(u['item'] + '/' + u['behavior'] + ' ' + u['votes'] for u in g.get('unstable', [])) or 'none'}",
         f"- Gate: {'pass' if g['ok'] else 'FAIL'}", ""]
    if g["errors"]:
        L += ["| item | behavior | kind | expected | judge | rationale |", "|---|---|---|---|---|---|"]
        for e in g["errors"]:
            L.append(f"| {e['item']} | {e['behavior']} | {e['kind']} | {e['expected']} | {e['got'] if e['ok'] else 'ERROR'} | "
                     f"{(e['rationale'] or e['error'] or '').replace('|', '/').replace(chr(10), ' ')[:300]} |")
        L.append("")
    L += ["## Live audit (judge vs independent auditor on mini transcripts)", ""]
    if lv.get("skipped"):
        L.append(f"- Skipped: {lv['skipped']}")
    else:
        L += [f"- Auditor: `{lv['auditor']}`; verdicts compared {lv['n']}; agreement {lv['agreement']}",
              f"- Positives: judge {lv['positives_judge']}, auditor {lv['positives_auditor']}; auditor errors {lv['auditor_errors']}",
              f"- By lane (agree/total): {lv.get('by_lane', {})}; general lane gate: {'pass' if lv.get('general_ok', True) else 'FAIL'}",
              f"- Gate: {'pass' if lv['ok'] else 'FAIL'}", ""]
        if lv["disagreements"]:
            L += ["| lane | sample | behavior | judge | auditor | auditor rationale |", "|---|---|---|---|---|---|"]
            for d in lv["disagreements"]:
                L.append(f"| {d['lane']} | {d['sample']}#{d['epoch']} | {d['behavior']} | {d['judge']} | {d['auditor']} | "
                         f"{d['auditor_rationale'].replace('|', '/').replace(chr(10), ' ')[:300]} |")
    return "\n".join(L) + "\n"
