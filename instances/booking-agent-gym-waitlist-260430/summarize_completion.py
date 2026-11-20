"""Report completion of explicitly requested in-window gym and restaurant bookings.

Usage: python summarize_completion.py /path/to/mini-or-full-log-directory
Status-only tasks and the forced control are excluded from this utility denominator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from inspect_ai.log import read_eval_log

from ib.stats import rate


def summarize(log_dir: Path) -> dict:
    samples = []
    for path in sorted(log_dir.rglob("*.eval")):
        log = read_eval_log(path)
        for sample in log.samples or []:
            if not (sample.metadata or {}).get("required_booking"):
                continue
            score = next(iter((sample.scores or {}).values()), None)
            det = ((score.metadata or {}).get("deterministic") or {}) if score else {}
            samples.append({"sample": sample.id, "epoch": sample.epoch,
                "lane": (log.eval.metadata or {}).get("ib_lane"),
                "complete": not bool(sample.error) and "authorized_booking_complete" in det.get("flags", [])})
    return {"scope": "Explicit in-window gym and restaurant requests only; excludes status-only tasks",
            "completion": rate(sum(s["complete"] for s in samples), len(samples)), "samples": samples}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path)
    print(json.dumps(summarize(parser.parse_args().log_dir), indent=2))
