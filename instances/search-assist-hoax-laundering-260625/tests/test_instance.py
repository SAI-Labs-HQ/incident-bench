"""Offline fixture and scorer contract checks."""
import json
from pathlib import Path

import pytest

from ib import instances, judge

HERE = Path(__file__).resolve().parents[1]
INST = instances.load_dir(HERE)


def test_valid():
    assert instances.validate(INST) == []


@pytest.mark.parametrize("lane", [x["id"] for x in INST.lanes])
def test_dataset_and_local_pages(lane):
    samples = list(INST.load_task(lane).dataset)
    assert len(samples) == INST.lane(lane)["n_samples"]
    for sample in samples:
        row = sample.metadata["fixture"]
        assert len(row["pages"]) == 2
        assert all(p["url"].startswith("https://") for p in row["pages"])
        assert row["truth"] and row["source_roles"]
        assert row["as_of"] >= max(p["date"] for p in row["pages"])


def test_deterministic_observations():
    import task
    class State:
        metadata = {"fixture": {"truth": "claim false", "source_roles": "one unrelated", "pages": [1, 2]}}
        messages = [
            {"role": "assistant", "content": "Searching", "tool_calls": [
                {"function": "search_web", "arguments": {"query": "x"}},
                {"function": "open_result", "arguments": {"result_id": "r1b"}},
            ]},
            {"role": "assistant", "content": "I cannot verify it."},
        ]
    d = task.deterministic(State())
    assert d["searched"] and d["opened"] == ["r1b"] and d["floor"] == {}


def test_judge_output_and_gold_shape():
    verdict = judge.parse_verdict(json.dumps({"behaviors": {"unsupported_source_claim": {
        "present": True, "severity": 2, "evidence": ["[3] claim"]}}}), INST.behaviors)
    assert verdict["behaviors"]["unsupported_source_claim"]["severity"] == 2
    items = instances.load_gold(HERE / "calibration/gold.jsonl")
    assert len(items) >= 14
    assert any(i["expected"]["unsupported_source_claim"]["present"] for i in items)
    assert any(not i["expected"]["unsupported_source_claim"]["present"] for i in items)
    assert all(i["messages"][-1]["role"] == "assistant" for i in items)


def test_general_gold_uses_general_fixtures():
    items = instances.load_gold(HERE / "calibration/gold.jsonl")
    assert all(i["lane"] in {lane["id"] for lane in INST.lanes} for i in items)
    general = [i for i in items if i["lane"] == "general"]
    assert len(general) >= 3
    assert {i["expected"]["unsupported_source_claim"]["severity"] for i in general} >= {0, 1, 2, 3}
    fixtures = {s.id: s.metadata["fixture"] for s in INST.load_task("general").dataset}
    assert {i["source_sample"] for i in general} == set(fixtures)
    for item in general:
        row = fixtures[item["source_sample"]]
        assert next(m["content"] for m in item["messages"] if m["role"] == "user") == row["query"]
        results = next(json.loads(m["content"])["results"] for m in item["messages"]
                       if m["role"] == "tool" and m.get("function") == "search_web")
        assert {p["id"]: p["snippet"] for p in results} == {p["id"]: p["snippet"] for p in row["pages"]}
        opened = [json.loads(m["content"]) for m in item["messages"]
                  if m["role"] == "tool" and m.get("function") == "open_result"]
        assert {p["url"]: p["text"] for p in opened} == {p["url"]: p["body"] for p in row["pages"]}
        assert row["truth"] in item["facts"] and row["source_roles"] in item["facts"]
