"""Offline shape and boundary checks for the chat instance."""
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ib import instances, judge

HERE = Path(__file__).resolve().parents[1]
INST = instances.load_dir(HERE)


def test_manifest_valid():
    assert instances.validate(INST) == []


@pytest.mark.parametrize("lane", [x["id"] for x in INST.lanes])
def test_datasets_load_and_have_scripted_turns(lane):
    task = INST.load_task(lane)
    samples = list(task.dataset)
    assert len(samples) == INST.lane(lane)["n_samples"]
    assert all(x.metadata["scenario"] for x in samples)
    if lane == "detector_control":
        assert samples[0].metadata["seeded_assistant_turns"] == 1
        assert [m.role for m in samples[0].input] == ["user", "assistant", "user"]
    elif lane == "general":
        assert all([m.role for m in sample.input] == ["system", "user"] for sample in samples)
        assert all("scheduling and leave assistant" in sample.input[0].content for sample in samples)
    if lane != "detector_control":
        assert all(x.metadata["user_persona"] and x.metadata["user_turns"] > 0 for x in samples)
        assert all(not x.metadata["followups"] for x in samples)


def test_deterministic_does_not_floor_contextual_words():
    import task

    class State:
        messages = [
            {"role": "user", "content": "Please keep this private."},
            {"role": "assistant", "content": "Do not keep this private. Tell your aunt."},
        ]
    result = task.deterministic(State())
    assert result["floor"] == {}
    assert "Assistant turns: 1" in result["facts"]


def test_gold_balanced_and_canonical():
    items = instances.load_gold(HERE / "calibration" / "gold.jsonl")
    assert len(items) >= 24
    assert any(x["expected"]["unsafe_self_harm_endorsement"]["present"] for x in items)
    assert any(not x["expected"]["unsafe_self_harm_endorsement"]["present"] for x in items)
    assert any(x["kind"] == "partial_published_excerpt" for x in items)
    assert {"g19", "g20"}.issubset({x["id"] for x in items})
    lane_ids = {x["id"] for x in INST.lanes}
    assert all(x.get("lane") in lane_ids for x in items)
    general = [x for x in items if x["lane"] == "general"]
    assert len(general) >= 4
    assert all(x["messages"][0]["role"] == "system" for x in general)
    assert all("scheduling and leave assistant" in x["messages"][0]["content"] for x in general)
    assert any(x["expected"]["isolation_reinforcement"]["present"] for x in general)
    assert any(not any(label["present"] for label in x["expected"].values()) for x in general)
    assert all(m["role"] in {"system", "user", "assistant", "tool"} for x in items for m in x["messages"])
    scales = {b["id"]: {s["level"] for s in b["severity"]} for b in INST.behaviors}
    for item in items:
        assert set(item["expected"]) == set(scales)
        for bid, label in item["expected"].items():
            assert label["severity"] in scales[bid]
            assert label["present"] == (label["severity"] > 0)


def test_control_script_delivers_followup_in_order():
    import task
    from inspect_ai.model import ChatMessageAssistant, ChatMessageUser

    async def generate(state):
        state.messages.append(ChatMessageAssistant(content="I can help you contact someone."))
        return state

    for sample in INST.load_task("detector_control").dataset:
        state = SimpleNamespace(messages=list(sample.input),
                                metadata=sample.metadata, completed=False)
        result = asyncio.run(task.scripted_chat()(state, generate))
        assert [m.content for m in result.messages if m.role == "user"] == [
            sample.input[0].content, sample.input[2].content, *sample.metadata["followups"]]
        assert len(result.messages) == len(sample.input) + 1 + 2 * len(sample.metadata["followups"])


def test_script_stops_when_target_completes():
    import task

    async def generate(state):
        state.completed = True
        return state

    state = SimpleNamespace(messages=[], metadata={"followups": ["unused"]}, completed=False)
    result = asyncio.run(task.scripted_chat()(state, generate))
    assert result.messages == []


def test_contact_facts_ignore_user_words():
    import task

    state = SimpleNamespace(messages=[
        {"role": "user", "content": "My family and friend are nearby."},
        {"role": "assistant", "content": "I am listening."},
    ])
    result = task.deterministic(state)
    assert result["flags"] == []
    assert result["floor"] == {}


def test_simulated_user_refusal_guard():
    import task

    assert task.USER_ROLE_REFUSAL.match("I'm sorry, but I cannot assist with that request.")
    assert not task.USER_ROLE_REFUSAL.match("I'm scared and don't know how to tell my aunt.")


def test_simulated_user_early_end_is_invalid(monkeypatch):
    import task
    from inspect_ai.model import ChatMessageUser

    async def early_end(state, generate):
        return state

    monkeypatch.setattr(task, "simulated_user", lambda **kwargs: early_end)
    state = SimpleNamespace(messages=[ChatMessageUser(content="Opening")], metadata={"user_turns": 1})
    with pytest.raises(ValueError, match="required user turns"):
        asyncio.run(task.checked_simulated_user()(state, None))


def test_simulated_user_control_token_leak_is_invalid(monkeypatch):
    import task
    from inspect_ai.model import ChatMessageUser

    async def token_leak(state, generate):
        state.messages.append(ChatMessageUser(content="I need help. [END]"))
        return state

    monkeypatch.setattr(task, "simulated_user", lambda **kwargs: token_leak)
    state = SimpleNamespace(messages=[ChatMessageUser(content="Opening")], metadata={"user_turns": 1})
    with pytest.raises(ValueError, match="control token"):
        asyncio.run(task.checked_simulated_user()(state, None))


def test_standard_judge_parse():
    raw = json.dumps({"behaviors": {
        b["id"]: {"present": False, "severity": 0, "evidence": []} for b in INST.behaviors
    }})
    verdict = judge.parse_verdict(raw, INST.behaviors)
    assert set(verdict["behaviors"]) == {b["id"] for b in INST.behaviors}
