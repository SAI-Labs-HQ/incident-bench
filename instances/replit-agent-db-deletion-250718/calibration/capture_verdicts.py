"""Observe the standard ib CLI calibration's returned verdicts; never modify them."""
import json
import sys
from pathlib import Path
from ib import judge
from ib.cli import main

here = Path(__file__).resolve().parents[1]
gold = [json.loads(line) for line in (here/'calibration/gold.jsonl').read_text().splitlines()]
def item_key(messages, facts, scenario):
    return (json.dumps(messages, sort_keys=True), facts, scenario)
lookup = {item_key(it['messages'], it.get('facts', ''), it.get('scenario', '')): it for it in gold}
assert len(lookup) == len(gold)
original = judge.judge_messages
rows = []
async def capture(*args, **kwargs):
    result = await original(*args, **kwargs)
    messages = kwargs['messages']
    if messages and isinstance(messages[0], dict):
        item = lookup[item_key(messages, kwargs.get('facts', ''), kwargs.get('scenario', ''))]
        rows.append({'id': item['id'], 'expected': item['expected'],
                     'verdict': result['verdict'], 'ok': result['ok']})
    else:
        rows.append({'live': True, 'scenario': kwargs.get('scenario'),
                     'verdict': result['verdict'], 'ok': result['ok']})
    return result
judge.judge_messages = capture
rc = main(sys.argv[1:])
(here/'results/audit2_calibration_verdicts.json').write_text(json.dumps(rows, indent=2))
raise SystemExit(rc)
