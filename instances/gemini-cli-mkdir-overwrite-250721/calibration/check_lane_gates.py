"""Check each lane's calibration rates, including the non-general live lanes.

The core report currently enforces aggregate gold and general live gates. This
instance additionally requires every reported lane to meet the same thresholds.
"""
import json
from pathlib import Path

from ib import config, instances


def check(result: dict) -> dict:
    thresholds = {'gold': float(config.get('calibration.min_gold_accuracy')),
                  'live': float(config.get('calibration.min_audit_agreement'))}
    lanes = {}
    for section, threshold in thresholds.items():
        for lane, counts in result[section]['by_lane'].items():
            correct, total = map(int, counts.split('/'))
            lanes.setdefault(lane, {})[section] = {'correct': correct, 'total': total,
                                                  'ok': total > 0 and correct / total >= threshold}
    inst = instances.load_dir(Path(__file__).resolve().parents[1])
    covered = {ln['id'] for ln in inst.lanes} <= set(result['live']['by_lane'])
    ok = (result['ok'] and covered and not result['gold']['critical_errors']
          and not result['live']['auditor_errors']
          and all(value['ok'] for lane in lanes.values() for value in lane.values()))
    return {'ok': bool(ok), 'all_live_lanes_covered': covered, 'by_lane': lanes,
            'calibration_at': result['at']}


if __name__ == '__main__':
    inst = instances.load_dir(Path(__file__).resolve().parents[1])
    result = json.loads((config.path('calibration') / f'{inst.id}.json').read_text())
    gates = check(result)
    print(json.dumps(gates, indent=2))
    raise SystemExit(0 if gates['ok'] else 1)
