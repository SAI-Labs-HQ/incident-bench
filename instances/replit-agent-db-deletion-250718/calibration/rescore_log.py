"""Re-score a trusted local mini log with the current standard incident scorer.

Does not run the target or change its transcript/store. Writes a separate Inspect
log and a small provenance report; the original evaluation remains untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path

from inspect_ai import score
from inspect_ai.log import read_eval_log, write_eval_log

from ib import config, instances, models, runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parents[1]
    config.setup_runtime_env()
    inst = instances.load_dir(here)
    original = read_eval_log(args.source)
    assert original.status == "success"
    assert original.eval.metadata["ib_instance"] == inst.id
    lane = original.eval.metadata["ib_lane"]
    specs = runner.role_specs(inst)
    roles = {name: models.get_model(spec, role=name) for name, spec in specs.items()}
    replay = original.model_copy(deep=True)
    # Rebuild the same logical roles through ib's provider adapter. Inspect's
    # native reconstruction of the archived Azure role expects a different key
    # variable; no endpoint/key overrides or target generation are needed.
    replay.eval.model_roles = {}
    result = score(replay, scorers=inst.load_task(lane).scorer, model_roles=roles,
                   action="overwrite", display="none")
    comparisons = []
    for before, after in zip(original.samples, result.samples, strict=True):
        assert before.messages == after.messages and before.store == after.store
        sc = next(iter(after.scores.values()))
        assert sc.value["judge_ok"] == 1
        comparisons.append({"sample": after.id, "epoch": after.epoch,
                            "before": next(iter(before.scores.values())).value,
                            "after": sc.value, "rationale": sc.explanation,
                            "messages_and_store_unchanged": True})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_eval_log(result, args.output)
    report = {"source": str(args.source), "output": str(args.output),
              "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
              "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
              "rubric_sha256": hashlib.sha256((here / "judge/rubric.md").read_bytes()).hexdigest(),
              "method": "inspect_ai.score with the unchanged ib.incident_judge scorer; target not re-run",
              "roles": {name: spec.label() for name, spec in specs.items()},
              "cost_note": "Extra scoring calls are separate from mini costs, as is calibration.",
              "comparisons": comparisons}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
