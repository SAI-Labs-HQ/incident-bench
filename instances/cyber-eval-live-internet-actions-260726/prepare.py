"""Idempotently cache AISI's public technical report for source review; no task runtime dependency."""
from __future__ import annotations
import hashlib
from urllib.request import Request, urlopen
from ib import data_dir

ID = 'cyber-eval-live-internet-actions-260726'
URL = 'https://cdn.prod.website-files.com/663bd486c5e4c81588db7a1d/6a724858f7db25c81487016d_Security%20Incident%20INC-2026-07-28-01.pdf'
SHA256 = 'b64590f61dad20fb812ee08b360a57a0feeaebd4257cc98b0e4ca43ed70a28e9'

def main() -> None:
    path = data_dir(ID) / 'evidence' / 'report.pdf'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print(path)
        return
    data = urlopen(Request(URL, headers={'User-Agent': 'incident-bench source cache'}), timeout=30).read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('AISI report changed; review before updating pinned hash')
    path.write_bytes(data)
    print(path)

if __name__ == '__main__':
    main()
