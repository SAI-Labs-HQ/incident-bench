"""Core tests (offline): stats, transcript rendering, judge parsing/floor, instance validation of the template."""
import json

import pytest

from ib import config, instances, judge, stats, transcript


def test_wilson_and_rule_of_three():
    r = stats.rate(0, 20)
    assert r["upper_bound_95"] == 0.15 and r["ci95"][0] == 0.0
    r = stats.rate(5, 20)
    assert 0.09 < r["ci95"][0] < 0.12 and 0.45 < r["ci95"][1] < 0.5


def test_render_tool_calls_and_reasoning():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "reasoning", "reasoning": "think"}, {"type": "text", "text": "ok"}],
             "tool_calls": [{"id": "1", "function": "bash", "arguments": {"cmd": "curl x"}}]},
            {"role": "tool", "function": "bash", "content": "done"}]
    out = transcript.render(msgs)
    assert "[model reasoning]" in out and 'bash({"cmd": "curl x"})' in out and "TOOL RESULT (bash)" in out
    assert transcript.tool_calls(msgs)[0]["function"] == "bash"
    assert transcript.assistant_text(msgs) == "ok"


BEH = [{"id": "b1", "definition": "d", "severity": [{"level": 0, "label": "no"}, {"level": 2, "label": "yes"}]}]


def test_parse_verdict_tolerates_trailing_commas_and_strings():
    v = judge.parse_verdict('```json\n{"behaviors": {"b1": {"present": "true", "severity": "2", "evidence": [],},}}\n```', BEH)
    assert v["behaviors"]["b1"]["present"] is True and v["behaviors"]["b1"]["severity"] == 2


def test_absent_behavior_has_zero_severity():
    v = judge.parse_verdict('{"behaviors": {"b1": {"present": false, "severity": 3}}}', BEH)
    assert v["behaviors"]["b1"]["severity"] == 0


def test_prompt_contains_all_parts():
    p = judge.build_prompt(behaviors=BEH, rubric="RUBRIC", incident="INC", scenario="SCN", facts="FACTS",
                           messages=[{"role": "user", "content": "hello"}])
    for s in ("RUBRIC", "INC", "SCN", "FACTS", "hello", '"b1"'):
        assert s in p


def test_template_instance_valid():
    inst = instances.load_dir(config.path("instances") / "_template")
    assert instances.validate(inst) == []


def test_validation_catches_missing_general_lane(tmp_path):
    import shutil
    d = tmp_path / "x"
    shutil.copytree(config.path("instances") / "_template", d)
    m = (d / "manifest.yaml").read_text().replace("scope: general", "scope: variants")
    (d / "manifest.yaml").write_text(m)
    errs = instances.validate(instances.load_dir(d))
    assert any("scope 'general'" in e for e in errs)
