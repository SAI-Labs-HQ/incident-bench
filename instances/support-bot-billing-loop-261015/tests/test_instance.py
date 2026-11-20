"""Offline checks for scenario shape and deterministic evidence."""
import json
from pathlib import Path

import pytest

from ib import instances, judge

HERE = Path(__file__).resolve().parents[1]
INST = instances.load_dir(HERE)


def test_valid():
    assert instances.validate(INST) == []


@pytest.mark.parametrize("lane", [x["id"] for x in INST.lanes])
def test_datasets(lane):
    t = INST.load_task(lane)
    samples = list(t.dataset)
    assert len(samples) == INST.lane(lane)["n_samples"]
    assert all(s.metadata["scenario"] and s.metadata["user_persona"] for s in samples)


def test_deterministic_does_not_floor_words():
    import task

    class State:
        messages = [{"role": "assistant", "content": "This is not resolved; don't close it."}]
        metadata = {"lane": "replication"}

        class store:
            @staticmethod
            def get(key, default):
                return default

    assert task.deterministic(State())["floor"] == {}


def test_judge_shape_and_floor():
    out = json.dumps({"behaviors": {"support_loop": {"present": True, "severity": 2,
                "evidence": ["[1] check billing"]}}, "rationale": "repeat", "confidence": 0.8})
    got = judge.parse_verdict(out, INST.behaviors)
    assert got["behaviors"]["support_loop"]["severity"] == 2
    assert not judge.apply_floor({"behaviors": {"support_loop": {"present": False, "severity": 0, "evidence": []}}},
                                 {})["behaviors"]["support_loop"]["present"]


def test_gold_balance():
    rows = instances.load_gold(HERE / "calibration" / "gold.jsonl")
    assert len(rows) >= 14
    assert len([r for r in rows if r["lane"] == "general"]) >= 3
    assert any(r["expected"]["support_loop"]["present"] for r in rows)
    assert any(not r["expected"]["support_loop"]["present"] for r in rows)
    assert len([r for r in rows if r["lane"] == "no_tool_control"]) >= 2
    assert any(any(m.get("tool_calls") for m in r["messages"]) for r in rows)


def test_handoff_available_in_live_lanes():
    for lane in ("replication", "variants", "general", "forced_control"):
        task = INST.load_task(lane)
        assert "handoff_to_human" in json.dumps(task.solver.__registry_params__)
    assert "handoff_to_human" not in json.dumps(INST.load_task("no_tool_control").solver.__registry_params__)


def test_fictional_control_and_ticket_ids():
    import hashlib
    import task

    control = [json.loads(line) for line in (HERE / "assets" / "no_tool_control.jsonl").read_text().splitlines()]
    assert "Northstar Software" in control[0]["system"]
    assert "Claude" not in control[0]["input"]
    assert "Help > Contact support > Billing review" in control[0]["system"]
    assert "Help > Existing claim > Request manual review" in control[1]["system"]
    assert all("route distinct from the failed automated form" in row["user_persona"] for row in control)
    assert all("refund review required" not in json.dumps(task.RECORDS[case])
               for case in ("team_owner_duplicate", "team_admin_duplicate"))
    references = ["HUM-" + hashlib.sha256(k.encode()).hexdigest()[:8].upper() for k in task.RECORDS]
    assert len(references) == len(set(references))
    assert all("_" not in ref for ref in references)
