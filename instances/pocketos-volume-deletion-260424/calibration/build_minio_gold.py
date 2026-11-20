"""Import reviewed real transcripts with independent hand labels; never copy judge labels."""
import argparse
import hashlib
import json
from pathlib import Path

from inspect_ai.log import read_eval_log
from ib.transcript import normalize

HERE = Path(__file__).resolve().parents[1]


def build(labels):
    result = []
    for number, row in enumerate(labels, 1):
        log = read_eval_log(row['log'])
        sample = next(s for s in log.samples if s.id == row['sample'])
        messages = normalize(sample.messages)
        score = next(iter(sample.scores.values()))
        severity = row['severity']
        result.append({
            'id': f'mg{number:02d}', 'lane': row['lane'], 'kind': row['kind'], 'critical': True,
            'scenario': sample.metadata['scenario'], 'messages': messages,
            'facts': score.metadata['deterministic']['facts'],
            'expected': {'out_of_scope_deletion': {'present': severity > 0, 'severity': severity}},
            'note': row['note'],
            'provenance': {'run': Path(row['log']).parent.parent.name,
                           'source_lane': log.eval.metadata['ib_lane'], 'sample': sample.id,
                           'model': log.eval.model,
                           'transcript_sha256': hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()}})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('labels', type=Path)
    args = parser.parse_args()
    path = HERE / 'calibration/gold.jsonl'
    old = [json.loads(s) for s in path.read_text().splitlines()]
    rows = [row for row in old if not row['id'].startswith('mg')] + build(json.loads(args.labels.read_text()))
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    print(f'Wrote {len(rows)} independently labelled items.')
