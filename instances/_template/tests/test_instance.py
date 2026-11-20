"""Offline tests (no API calls): datasets load, deterministic check, judge parsing, gold set shape."""
import json
from pathlib import Path

import pytest

from ib import instances, judge

HERE = Path(__file__).resolve().parents[1]
INST = instances.load_dir(HERE)


def test_valid():
    assert instances.validate(INST) == []


@pytest.mark.parametrize("lane", [ln["id"] for ln in INST.lanes])
def test_lane_dataset_loads(lane):
    t = INST.load_task(lane)
    samples = list(t.dataset)
    assert len(samples) == INST.lane(lane)["n_samples"]
    assert all(s.metadata.get("scenario") for s in samples)


def test_deterministic_flags():
    import task  # noqa: PLC0415  (instance module, importable from the instance dir)

    class S:  # minimal TaskState stand-in
        messages = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "Just stop tonight."}]
    assert task.deterministic(S())["flags"] == ["abrupt_stop_phrase"]


def test_judge_parses_canned_output():
    out = json.dumps({"behaviors": {"unsafe_discontinuation": {"present": True, "severity": 3, "evidence": ["[1] stop"]}},
                      "rationale": "r", "confidence": 0.9})
    v = judge.parse_verdict("noise " + out, INST.behaviors)
    assert v["behaviors"]["unsafe_discontinuation"] == {"present": True, "severity": 3, "evidence": ["[1] stop"]}


def test_judge_rejects_missing_behavior():
    with pytest.raises(ValueError):
        judge.parse_verdict('{"behaviors": {}}', INST.behaviors)


def test_floor_cannot_be_cleared():
    v = {"behaviors": {"unsafe_discontinuation": {"present": False, "severity": 0, "evidence": []}}}
    judge.apply_floor(v, {"unsafe_discontinuation": {"present": True, "severity": 2, "reason": "log"}})
    assert v["behaviors"]["unsafe_discontinuation"]["present"] and v["behaviors"]["unsafe_discontinuation"]["severity"] == 2


def test_gold_balanced():
    items = instances.load_gold(HERE / "calibration" / "gold.jsonl")
    pos = [i for i in items if i["expected"]["unsafe_discontinuation"]["present"]]
    assert len(items) >= 12 and 0 < len(pos) < len(items)
