"""Fetch released evidence to the shared cache, pinned to source revisions.

The evaluation does not depend on network access at run time. Re-running this
script leaves already downloaded files alone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlparse

from ib.config import data_dir

INSTANCE = "cohere-qwen-call-home-261026"
HERE = Path(__file__).parent
SOURCES = {
    "sai-fetch-flash-traces.parquet": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-qwenflash/traces.parquet",
    "sai-fetch-flash-README.md": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-qwenflash/README.md",
    "cohere-trace.jsonl": "https://huggingface.co/datasets/CohereLabs/10-2026-qwen-investigation-traces/resolve/028b7cd7d931586dbfa05463903be9d45c4924ac/qwen-investigation.jsonl",
    "cohere-readme.md": "https://huggingface.co/datasets/CohereLabs/10-2026-qwen-investigation-traces/resolve/028b7cd7d931586dbfa05463903be9d45c4924ac/README.md",
    "sai-readme.md": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/README.md",
    "wtogami-repro.py": "https://raw.githubusercontent.com/wtogami/qwen-block-proxy-artifacts/d8887964ad74cb81003315b5c8ace38ffa3f3b3c/tools/repro.py",
    "sai-fetch-flash-f01.jsonl": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-qwenflash/sessions/fetch-f01_docs_crawl-qwen3.8-flash-r01.jsonl",
    "sai-fetch-flash-f02.jsonl": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-qwenflash/sessions/fetch-f02_docs_crawl_with_failures-qwen3.8-flash-r01.jsonl",
    "sai-fetch-27b-f01.jsonl": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-others-b/sessions/fetch-f01_docs_crawl-qwen3.8-27b-r01.jsonl",
    "sai-fetch-27b-f02.jsonl": "https://huggingface.co/datasets/sailabs/incident-bench-agent-egress/resolve/1d37f4cccf8d59ba30b2d355e42f7c2d0b736583/fetch-01-others-b/sessions/fetch-f02_docs_crawl_with_failures-qwen3.8-27b-r01.jsonl",
}


def verify_fetch_conditions(dest: Path) -> None:
    """Check the compact checked-in assets against the pinned published runs."""
    import pyarrow.parquet as pq
    trials = pq.read_table(dest / "sai-fetch-flash-traces.parquet").to_pylist()
    observations = {}
    for trial in trials:
        rows = [json.loads(line) for line in trial["trace"].splitlines()]
        system = next(r["message"]["content"] for r in rows if r.get("message", {}).get("customType") == "original-system-prompt")
        assert system == (HERE / "assets/fetch_system.txt").read_text()
        source = rows[0]["source"]
        assert source["params"] == {"temperature": 1.0, "top_p": 1.0, "extra_body": {"top_k": 20}}
        if "f02" not in trial["task_id"]:
            continue
        _failure_observations(rows, observations)
    _failure_observations([json.loads(line) for line in (dest / "sai-fetch-27b-f02.jsonl").read_text().splitlines()], observations)
    assert observations == json.loads((HERE / "assets/fetch_failures.json").read_text())
    print(f"verified published fetch prompt, sampling and {len(observations)} per-URL failure outcomes")


def _failure_observations(rows: list[dict], observations: dict) -> None:
    calls = {}
    for row in rows:
        content = row.get("message", {}).get("content")
        for part in content if isinstance(content, list) else []:
            if part.get("name") != "webfetch":
                continue
            if part.get("type") == "toolCall":
                calls[part["id"]] = part["arguments"].get("url", "")
            if part.get("type") == "toolResult":
                url = calls.get(part["toolCallId"], "")
                if url.startswith("https://docs.python.org/3/library/"):
                    failed = "503" in part.get("output", "")[:100]
                    assert url not in observations or observations[url] == failed
                    observations[url] = failed


def build_docs_pages(dest: Path) -> None:
    """Recover the public documentation tool outputs in SAI's released runs."""
    output = dest / "docs_pages.json"
    expected_docs_hash = (HERE / "assets/docs_pages_sha256.txt").read_text().strip()
    if output.is_file() and hashlib.sha256(output.read_bytes()).hexdigest() == expected_docs_hash:
        print(f"keep {output.name} {hashlib.sha256(output.read_bytes()).hexdigest()}")
        return
    pages: dict[str, str] = {}
    for name in ("sai-fetch-flash-f01.jsonl", "sai-fetch-flash-f02.jsonl",
                 "sai-fetch-27b-f01.jsonl", "sai-fetch-27b-f02.jsonl"):
        calls: dict[str, str] = {}
        for line in (dest / name).read_text().splitlines():
            row = json.loads(line)
            message = row.get("message") or {}
            for part in message.get("content") if isinstance(message.get("content"), list) else []:
                if part.get("type") == "toolCall" and part.get("name") == "webfetch":
                    calls[part.get("id", "")] = (part.get("arguments") or {}).get("url", "")
                elif part.get("type") == "toolResult" and part.get("name") == "webfetch":
                    url = calls.get(part.get("toolCallId", ""), "")
                    if urlparse(url).hostname == "docs.python.org" and part.get("ok") and part.get("output"):
                        pages.setdefault(url, part["output"])
    if not pages.get("https://docs.python.org/3/library/index.html"):
        raise RuntimeError("released SAI traces did not contain the documentation index")
    extra_hashes = json.loads((HERE / "assets/docs_extra_sha256.json").read_text())
    extra_dir = dest / "docs-extra"
    extra_dir.mkdir(exist_ok=True)
    for filename, expected_hash in extra_hashes.items():
        path = extra_dir / filename
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash:
            content = path.read_text()
        else:
            # The source URL is versioned; the checked-in digest pins the
            # exact snapshot and fails if the upstream page changes.
            source = f"https://docs.python.org/3.15/library/{filename}"
            with urlopen(source, timeout=30) as response:
                html = response.read().decode("utf-8")
            from bs4 import BeautifulSoup
            import re
            soup = BeautifulSoup(html, "html.parser")
            main = soup.find("div", class_="body") or soup.find("main") or soup
            for tag in main(["script", "style", "nav"]):
                tag.decompose()
            content = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
            content = content[:8000] + ("\n\n[webfetch: content truncated at 8000 characters]" if len(content) > 8000 else "")
            if hashlib.sha256(content.encode()).hexdigest() != expected_hash:
                raise RuntimeError(f"documentation snapshot hash changed: {source}")
            path.write_text(content)
        pages[f"https://docs.python.org/3/library/{filename}"] = content
    blob = json.dumps(pages, ensure_ascii=False, sort_keys=True).encode()
    if hashlib.sha256(blob).hexdigest() != expected_docs_hash:
        raise RuntimeError("documentation cache does not match pinned snapshot hash")
    temp = Path(str(output) + ".tmp")
    temp.write_bytes(blob)
    temp.replace(output)
    print(f"wrote {output.name}: {len(pages)} cached URLs, sha256 {hashlib.sha256(blob).hexdigest()}")


def main() -> None:
    dest = data_dir(INSTANCE) / "evidence"
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = dest / name
        if path.is_file() and path.stat().st_size:
            print(f"keep {path.name} {hashlib.sha256(path.read_bytes()).hexdigest()}")
            continue
        with urlopen(url, timeout=30) as response:
            blob = response.read()
        if not blob:
            raise RuntimeError(f"empty response for {url}")
        tmp = Path(str(path) + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(path)
        print(f"wrote {path.name} {hashlib.sha256(blob).hexdigest()}")
    build_docs_pages(dest)
    verify_fetch_conditions(dest)


if __name__ == "__main__":
    main()
