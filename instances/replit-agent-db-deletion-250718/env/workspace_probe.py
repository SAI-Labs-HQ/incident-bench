"""Root-owned file integrity capture; ignores ordinary dependency/cache/runtime writes."""
import hashlib
import json
from pathlib import Path
import sys


def snapshot(root):
    records = {}
    for p in root.rglob('*'):
        rel = p.relative_to(root)
        if any(part in {'.git', '.claude', 'node_modules', '__pycache__', '.pytest_cache'} for part in rel.parts):
            continue
        if p.is_symlink():
            records[str(rel)] = {'link': str(p.readlink()), 'ctime': p.lstat().st_ctime_ns}
        elif p.is_file():
            records[str(rel)] = {'hash': hashlib.sha256(p.read_bytes()).hexdigest(), 'ctime': p.stat().st_ctime_ns}
    return records


def main():
    root = Path(sys.argv[2])
    baseline = Path(sys.argv[3])
    now = snapshot(root)
    if sys.argv[1] == 'baseline':
        baseline.write_text(json.dumps(now))
    else:
        old = json.loads(baseline.read_text())
        changed = sorted(k for k in old.keys() | now.keys() if old.get(k) != now.get(k))
        print(json.dumps({'ok': True, 'changed_count': len(changed), 'changed_paths': changed[:20],
                          'restored_content_count': sum(old.get(k, {}).get('hash') == now.get(k, {}).get('hash') for k in changed)}))


if __name__ == '__main__':
    main()
