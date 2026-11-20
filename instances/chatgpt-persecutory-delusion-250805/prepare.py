"""Idempotently cache the two public court PDFs used as source evidence.

Runtime tasks do not require these PDFs. They are retained outside the repository for
source review. SHA-256 pins the downloaded bytes and detects changed documents.
"""
from __future__ import annotations

import hashlib
from urllib.request import Request, urlopen

from ib import data_dir

INSTANCE = "chatgpt-persecutory-delusion-250805"
FILES = {
    "complaint_adams.pdf": (
        "https://www.courthousenews.com/wp-content/uploads/2025/12/ChatGPT-lawsuit-SF.pdf",
        "77c757a9da30b4446faa604e0836f6daafc57116d455a2241480cd8b4b93a7ac",
    ),
    "complaint_lyons.pdf": (
        "https://www.courthousenews.com/wp-content/uploads/2026/04/lyons-v-openai-complaint.pdf",
        "ae8d70b09b5f350c66dd97009014547d80a77e10dd2841fe652d88f05a69bcdd",
    ),
}


def prepare() -> None:
    evidence = data_dir(INSTANCE) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for filename, (url, expected) in FILES.items():
        dest = evidence / filename
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
            continue
        req = Request(url, headers={"User-Agent": "incident-bench/1.0 source review"})
        with urlopen(req, timeout=30) as response:
            body = response.read()
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise ValueError(f"{filename}: source bytes changed ({actual})")
        dest.write_bytes(body)


if __name__ == "__main__":
    prepare()
