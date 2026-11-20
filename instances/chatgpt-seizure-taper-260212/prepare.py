"""Fetch pinned public research evidence into ib.data_dir; tasks do not depend on it.

This is idempotent. Copyrighted sources stay in the shared cache, outside the instance.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from ib.config import data_dir

INSTANCE = Path(__file__).resolve().parent.name
SOURCES = {
    "complaint.pdf": ("https://schenklawfirm.com/press/wan/complaint.pdf", "230cc4d8efafcf13fde533d14827fc05fbe66472c0600b12c04ad15b07cbacb6"),
    "aiid.html": ("https://incidentdatabase.ai/cite/1814/", "5910b319d905938ca0a25140392b876f10f101f8eff5bec55127b608b571f65a"),
    "model-spec.html": ("https://model-spec.openai.com/2025-12-18.html", "a0484896ce9798c087b5f0fecf864674488f8f2ce66fe990cd04a7258269e217"),
    "gpt-4o-system-card.pdf": ("https://cdn.openai.com/gpt-4o-system-card.pdf", "e2579ecb185cbc13bac39f9dbf25e1917f78e1ea5a3a5023165c6614fb5db724"),
}


def prepare() -> Path:
    root = data_dir(INSTANCE) / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    for name, (url, sha) in SOURCES.items():
        dest = root / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
            continue
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError(f"source changed for {name}; update the pin only after source review")
        dest.write_bytes(content)
    return root


if __name__ == "__main__":
    print(prepare())
