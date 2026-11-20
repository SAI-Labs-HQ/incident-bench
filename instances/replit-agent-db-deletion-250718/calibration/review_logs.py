"""Export complete readable mini transcripts; retain markers for provider-encrypted reasoning."""
import hashlib
import json
from pathlib import Path
import sys
from inspect_ai.log import read_eval_log

root=Path(sys.argv[1]); output=Path(sys.argv[2]); output.mkdir(parents=True,exist_ok=True)
index=[]
for path in sorted(root.rglob('*.eval')):
    log=read_eval_log(path)
    if log.status!='success': continue
    for sample in log.samples or []:
        lines=[]
        encrypted=0
        for i,message in enumerate(sample.messages):
            d=message.model_dump(exclude_none=True)
            lines.append(f'[{i}] {message.role.upper()}')
            if isinstance(message.content,str): lines.append(message.content)
            else:
                for part in message.content:
                    c=part.model_dump(exclude_none=True)
                    if c['type']=='reasoning':
                        if c.get('redacted'):
                            encrypted+=1; lines.append('[Provider-encrypted reasoning unavailable]')
                            details = c.get('signature', '')
                            summaries = []
                            if details.startswith('reasoning-details://'):
                                summaries = [x['summary'] for x in json.loads(details.split('://', 1)[1]) if x.get('type') == 'reasoning.summary' and x.get('summary')]
                            elif c.get('summary') and not c['summary'].startswith('gAAAA'):
                                summaries = [c['summary']]
                            lines.extend('REASONING SUMMARY: ' + text for text in summaries)
                        else: lines.append('REASONING: '+c.get('reasoning',''))
                    elif c['type']=='text': lines.append(c['text'])
                    else: lines.append(json.dumps(c,ensure_ascii=False))
            for call in d.get('tool_calls',[]): lines.append('CALL '+call['function']+' '+json.dumps(call['arguments'],ensure_ascii=False))
            if d.get('error'): lines.append('ERROR '+json.dumps(d['error']))
        scores={k:{'value':v.value,'explanation':v.explanation} for k,v in (sample.scores or {}).items()}
        lines.append('SCORES '+json.dumps(scores,ensure_ascii=False))
        dest=output/f'{root.name}-{path.parent.name}-{sample.id}.txt';dest.write_text('\n\n'.join(lines))
        index.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'lane':path.parent.name,
                      'sample':sample.id,'messages':len(sample.messages),'encrypted_reasoning_messages':encrypted,
                      'readable_transcript':str(dest),'scores':scores})
print(json.dumps(index,indent=2))
