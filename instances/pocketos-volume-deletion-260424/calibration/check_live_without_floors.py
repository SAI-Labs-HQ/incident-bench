"""Independent live re-judging without deterministic facts/floors.

Usage: python calibration/check_live_without_floors.py LOG_DIR [LOG_DIR ...] --output results/audit.json
Uses the configured auditor role. Makes paid model calls, never executes target tools.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from inspect_ai.log import read_eval_log

from ib import config, instances, judge, models, pricing
from ib.transcript import render


async def audit(log_dirs: list[Path], auditor: str | None = None, full_transcript: bool = False,
                only_clipped: bool = False) -> dict:
    config.setup_runtime_env()
    inst = instances.load_dir(Path(__file__).resolve().parents[1])
    spec = models.resolve(auditor or inst.manifest['roles']['auditor']['default'], role='auditor')
    model = models.get_model(spec)
    usage = []

    class MeteredModel:
        name = model.name

        def __init__(self, messages):
            self.full_text = render(messages, max_chars_per_message=10**9, max_total_chars=10**9) if full_transcript else None

        async def generate(self, prompt):
            if self.full_text is not None:
                # Preserve the shared judge template and parser, replacing only the clipped body.
                head, opening, rest = prompt.partition("\n<transcript>\n")
                _, closing, tail = rest.rpartition("\n</transcript>\n")
                if not opening or not closing:
                    raise ValueError("Shared judge transcript delimiters changed")
                prompt = head + opening + self.full_text + closing + tail
            output = await model.generate(prompt)
            if output.usage is not None:
                usage.append(output.usage)
            return output

        def __str__(self):
            return str(model)

    gate = asyncio.Semaphore(4)

    async def one(lane, sample):
        score = next(iter((sample.scores or {}).values()))
        async with gate:
            result = await judge.judge_messages(
                MeteredModel(sample.messages), behaviors=inst.behaviors, rubric=judge.load_rubric(inst.dir),
                incident=inst.incident_summary(), messages=sample.messages,
                scenario=sample.metadata.get('scenario', ''), facts='', floor=None,
                retries=1)
        expected = {b['id']: {'present': score.value[b['id']] >= 1,
                             'severity': int(score.value[b['id'] + '__sev'])} for b in inst.behaviors}
        got = result['verdict']['behaviors']
        agreement = result['ok'] and all(got[b]['present'] == e['present'] and
                                        got[b]['severity'] == e['severity'] for b, e in expected.items())
        return {'lane': lane, 'sample': sample.id, 'epoch': sample.epoch,
                'judge': expected, 'auditor': got, 'ok': result['ok'], 'agreement': agreement,
                'rationale': result['verdict']['rationale'], 'error': result['error']}

    jobs = []
    for directory in log_dirs:
        for path in sorted(directory.rglob('*.eval')):
            log = read_eval_log(str(path))
            lane = (log.eval.metadata or {}).get('ib_lane', path.parent.name)
            for sample in log.samples or []:
                if only_clipped and 'chars omitted' not in render(sample.messages):
                    continue
                score = next(iter((sample.scores or {}).values()), None)
                if score is not None and score.value.get('judge_ok') == 1:
                    jobs.append(one(lane, sample))
    rows = await asyncio.gather(*jobs)
    price = pricing.price_for(spec)
    return {'auditor': spec.label(), 'facts_supplied': False, 'floors_supplied': False,
            'full_transcript': full_transcript, 'only_clipped': only_clipped,
            'log_dirs': [str(p) for p in log_dirs], 'n': len(rows),
            'agreement': sum(r['agreement'] for r in rows) / len(rows) if rows else None,
            'ok': bool(rows) and all(r['agreement'] for r in rows),
            'usd': round(sum(price.cost(u) for u in usage), 5), 'price_source': price.source,
            'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log_dirs', type=Path, nargs='+')
    parser.add_argument('--auditor')
    parser.add_argument('--full-transcript', action='store_true', help='Remove shared renderer size clipping for this supplemental audit')
    parser.add_argument('--only-clipped', action='store_true', help='Audit only samples clipped by the shared renderer')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(audit(args.log_dirs, args.auditor, args.full_transcript, args.only_clipped))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
