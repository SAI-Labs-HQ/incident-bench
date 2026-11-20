"""Supplement standard calibration with an independent audit without facts or floors.

Usage: python calibration/audit_live.py --log-dir DIR [--log-dir DIR] --out FILE
Uses the manifest auditor alias and standard judge; writes no transcript copies.
"""
import argparse
import asyncio
from pathlib import Path

from inspect_ai.log import read_eval_log
from ib import config, instances, judge, models, runner


async def audit(log_dirs):
    inst = instances.load_dir(Path(__file__).resolve().parents[1])
    spec = models.resolve(inst.manifest['roles']['auditor']['default'], role='auditor')
    model = models.get_model(spec)
    rubric = judge.load_rubric(inst.dir)
    sem = asyncio.Semaphore(4)

    async def one(sample, lane, target):
        score = next(iter((sample.scores or {}).values()), None)
        if sample.error or score is None or score.value.get('judge_ok') != 1:
            return {'sample': sample.id, 'lane': lane, 'target': target, 'ok': False,
                    'error': 'unscored or invalid source sample'}
        async with sem:
            result = await judge.judge_messages(
                model, behaviors=inst.behaviors, rubric=rubric, incident=inst.incident_summary(),
                messages=sample.messages, scenario=(sample.metadata or {}).get('scenario', ''),
                facts='', floor=None)
        verdict = result['verdict']['behaviors']
        matches = {b['id']: bool(score.value[b['id']]) == verdict[b['id']]['present']
                   and int(score.value[b['id'] + '__sev']) == verdict[b['id']]['severity']
                   for b in inst.behaviors}
        return {'sample': sample.id, 'epoch': sample.epoch, 'lane': lane, 'target': target,
                'ok': result['ok'] and all(matches.values()), 'matches': matches,
                'original': score.value, 'auditor': verdict,
                'rationale': result['verdict']['rationale'], 'error': result['error']}

    jobs = []
    for directory in log_dirs:
        for file in sorted(Path(directory).rglob('*.eval')):
            log = read_eval_log(str(file))
            lane = log.eval.metadata['ib_lane']
            jobs.extend(one(s, lane, log.eval.model) for s in log.samples or [])
    rows = await asyncio.gather(*jobs)
    return {'auditor': spec.label(), 'facts_supplied': False, 'floors_applied': False,
            'log_dirs': log_dirs, 'n_samples': len(rows),
            'ok': bool(rows) and all(r['ok'] for r in rows), 'samples': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--log-dir', action='append', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    config.setup_runtime_env()
    report = asyncio.run(audit(args.log_dir))
    runner.write_json(Path(args.out), report)
    print(f"Independent audit without floors: {report['n_samples']} samples; ok={report['ok']}")
    raise SystemExit(0 if report['ok'] else 1)
