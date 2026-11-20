"""Cache the vendor's released excerpt image for provenance review; never used by the task.

The CDN URL is the exact 2026-11-13 asset URL on Anthropic's September assessment page.
The task itself uses no downloaded assets.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from ib import data_dir

URL = "https://assets.claude.com/4a3996b2c0728862519af363952aad2ebd6b1a8e.png?w=2048&q=75&fm=webp&fit=max"
NAME = "opus46-reasoning-excerpts.webp"
SHA256 = "fa84e2f13945379f18271d93d4af6608ea174e80dbaa1ed2afff65025124d8c1"


def prepare() -> Path:
    dest = data_dir("opus-checkpoint-third-party-admin-260909") / "evidence" / NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (not SHA256 or hashlib.sha256(dest.read_bytes()).hexdigest() == SHA256):
        return dest
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    blob = response.content
    if SHA256 and hashlib.sha256(blob).hexdigest() != SHA256:
        raise ValueError("released image checksum changed")
    dest.write_bytes(blob)
    return dest


if __name__ == "__main__":
    path = prepare()
    print(path, hashlib.sha256(path.read_bytes()).hexdigest())
