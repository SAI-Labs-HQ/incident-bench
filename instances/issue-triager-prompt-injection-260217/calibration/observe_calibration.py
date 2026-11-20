"""Run the standard ib calibrate CLI, retaining per-call verdicts for this audit.

This observer changes no prompt, verdict, floor, retry or gate. The shared
report currently omits severity-only mismatches, so retain them in state/.
Pass the usual calibrate arguments after the script (except the instance id).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ib import config, instances, judge
from ib.cli import main


def run() -> int:
    here = Path(__file__).resolve().parents[1]
    gold = instances.load_gold(here / "calibration" / "gold.jsonl")
    by_messages = {json.dumps(r["messages"], sort_keys=True): r for r in gold}
    records = []
    original = judge.judge_messages

    async def observed(*args, **kwargs):
        result = await original(*args, **kwargs)
        messages = kwargs.get("messages", [])
        item = by_messages.get(json.dumps(messages, sort_keys=True, default=str))
        records.append({"item": item["id"] if item else None,
                        "expected": item["expected"] if item else None,
                        "critical": item.get("critical", False) if item else False,
                        "model": result["model"], "ok": result["ok"],
                        "verdict": result["verdict"], "error": result["error"]})
        return result

    judge.judge_messages = observed
    sys.argv = ["ib", "calibrate", here.name, *sys.argv[1:]]
    try:
        return main()
    finally:
        judge.judge_messages = original
        dest = config.path("calibration") / here.name / "audit2-verdicts.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(records, indent=2) + "\n")
        print(f"Per-call audit evidence: {dest}")


if __name__ == "__main__":
    raise SystemExit(run())
