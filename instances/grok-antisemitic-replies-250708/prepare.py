"""Fetch source pages to the shared incident cache for source checking.

Pages are evidence only; the evaluation itself uses small, checked-in prompts.
Re-running keeps existing successful fetches and retries failures.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from ib import data_dir

HERE = Path(__file__).parent


def _fetch(item: tuple[int, str], root: Path) -> dict:
    number, url = item
    target = root / f"{number:02d}-{urlparse(url).netloc.replace('.', '_')}.html"
    if target.exists() and target.stat().st_size > 100:
        return {"url": url, "path": target.name, "status": "cached", "bytes": target.stat().st_size}
    try:
        response = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0 (incident-bench research)"})
        if response.ok:
            target.write_bytes(response.content)
        return {"url": url, "path": target.name if response.ok else None,
                "status": response.status_code, "bytes": len(response.content)}
    except requests.RequestException as exc:
        return {"url": url, "path": None, "status": type(exc).__name__, "bytes": 0}


def main() -> None:
    import yaml
    record = yaml.safe_load((HERE / "incident.yaml").read_text())
    urls = [s["url"] for s in record["sources"] if s["type"] == "news"]
    root = data_dir("grok-antisemitic-replies-250708") / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(lambda item: _fetch(item, root), enumerate(urls, 1)))
    (root / "fetch-index.json").write_text(json.dumps({"accessed": datetime.now(timezone.utc).isoformat(),
                                                        "reports": rows}, indent=2))
    print(json.dumps({"total": len(rows), "http_ok_or_cached": sum(r["status"] == "cached" or
                      isinstance(r["status"], int) and 200 <= r["status"] < 300 for r in rows),
                      "statuses": {str(s): sum(r["status"] == s for r in rows)
                                   for s in {r["status"] for r in rows}}}, indent=2))


if __name__ == "__main__":
    main()
