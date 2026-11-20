"""Idempotently cache source evidence; no runtime task asset depends on this."""
from __future__ import annotations
from pathlib import Path
from urllib.request import Request, urlopen
from ib import data_dir

ID = Path(__file__).parent.name
SOURCES = {
    'workflow.yml': 'https://raw.githubusercontent.com/cline/cline/7bdbf0a9a745f6abc09483fe9b08874c80fb44f3/.github/workflows/claude-issue-triage.yml',
    'npm-nightly.yaml': 'https://raw.githubusercontent.com/cline/cline/7bdbf0a9a745f6abc09483fe9b08874c80fb44f3/.github/workflows/npm-nightly.yaml',
    'research.html': 'https://adnanthekhan.com/posts/clinejection/',
    'postmortem.html': 'https://cline.bot/blog/post-mortem-unauthorized-cline-cli-npm',
    'advisory.html': 'https://github.com/cline/cline/security/advisories/GHSA-9ppg-jx86-fqw7',
}

def main() -> None:
    root = data_dir(ID) / 'evidence'
    root.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        dest = root / name
        if dest.exists() and dest.stat().st_size:
            continue
        req = Request(url, headers={'User-Agent': 'evidence-fetch/1.0'})
        with urlopen(req, timeout=30) as response:
            data = response.read()
        dest.write_bytes(data)
        print(name, len(data))

if __name__ == '__main__':
    main()
