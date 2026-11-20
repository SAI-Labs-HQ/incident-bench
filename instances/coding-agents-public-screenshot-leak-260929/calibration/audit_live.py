"""Supplement the standard calibration with an auditor blind to computed verdicts.

Uses the same rubric and scenario, but no deterministic facts/floors or prior
judge verdict. The standard `ib calibrate` remains the acceptance gate.
"""
import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from inspect_ai.log import read_eval_log
from ib import config, instances, judge, models

HERE = Path(__file__).resolve().parents[1]


async def audit(log_dirs, model_alias):
    inst = instances.load_dir(HERE)
    spec = models.resolve(model_alias or inst.manifest['roles']['auditor']['default'], role='auditor')
    model = models.get_model(spec)
    rubric = judge.load_rubric(HERE)
    semaphore = asyncio.Semaphore(4)
    jobs = []

    async def one(log, sample, filename):
        lane = log.eval.metadata['ib_lane']
        score = next(iter(sample.scores.values()))
        behaviors = [b for b in inst.behaviors if b['id'] in inst.lane(lane)['behaviors']]
        async with semaphore:
            result = await judge.judge_messages(model, behaviors=behaviors, rubric=rubric,
                incident=inst.incident_summary(), messages=sample.messages,
                scenario=sample.metadata.get('scenario', ''), facts='', floor=None,
                retries=0, fallback_model=None)
        return {'log': str(filename), 'target': str(log.eval.model), 'lane': lane,
                'sample': sample.id, 'epoch': sample.epoch, 'ok': result['ok'],
                'judge': score.value, 'auditor': result['verdict'], 'error': result['error'],
                'presence_agree': result['ok'] and all(
                    result['verdict']['behaviors'][b['id']]['present'] == bool(score.value[b['id']])
                    for b in behaviors),
                'severity_agree': result['ok'] and all(
                    result['verdict']['behaviors'][b['id']]['severity'] == score.value[b['id'] + '__sev']
                    for b in behaviors)}

    for directory in log_dirs:
        files = sorted(Path(directory).rglob('*.eval'))
        if not files:
            raise ValueError(f'No evaluation logs in {directory}')
        for filename in files:
            log = read_eval_log(str(filename))
            if log.status != 'success' or not log.samples:
                raise ValueError(f'Incomplete evaluation: {filename}')
            for sample in log.samples:
                jobs.append(one(log, sample, filename))
    rows = await asyncio.gather(*jobs)
    by_group = defaultdict(list)
    for row in rows:
        by_group[f"{row['target']} / {row['lane']}"].append(row['presence_agree'])
    return {'auditor': spec.label(), 'facts_supplied': False, 'floor_supplied': False,
            'fallback_used': False, 'n': len(rows),
            'agreement': sum(r['presence_agree'] for r in rows) / len(rows),
            'severity_agreement': sum(r['severity_agree'] for r in rows) / len(rows),
            'by_target_lane': {k: f'{sum(v)}/{len(v)}' for k, v in by_group.items()},
            'ok': all(r['presence_agree'] and r['severity_agree'] for r in rows), 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-dir', action='append', required=True)
    parser.add_argument('--model')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config.setup_runtime_env()
    result = asyncio.run(audit(args.log_dir, args.model))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
