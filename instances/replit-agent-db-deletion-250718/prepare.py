"""Idempotently cache the incident's public primary-source screenshots outside the repo.

These files are research evidence only; the evaluation uses small synthetic project state.
Post IDs pin the source posts. Existing nonempty files are never overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from ib import data_dir

INSTANCE = Path(__file__).parent.name
POSTS = {
    "jasonlk": [
        "1946065483653910889", "1946066422477529487", "1946069562723897802",
        "1946072038923530598", "1946073902805115256", "1946240562736365809",
    ],
    "amasad": ["1946986468586721478"],
}


def prepare() -> Path:
    evidence = data_dir(INSTANCE) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for user, ids in POSTS.items():
        for post_id in ids:
            meta = evidence / f"{post_id}.json"
            if not meta.exists() or not meta.stat().st_size:
                response = requests.get(f"https://api.fxtwitter.com/{user}/status/{post_id}", timeout=20)
                response.raise_for_status()
                tweet = response.json()["tweet"]
                if str(tweet.get("id")) != post_id:
                    raise ValueError(f"post ID mismatch for {post_id}")
                meta.write_text(json.dumps(tweet, indent=2))
            else:
                tweet = json.loads(meta.read_text())
            for index, media in enumerate((tweet.get("media") or {}).get("all") or []):
                if media.get("type") != "photo":
                    continue
                dest = evidence / f"{post_id}-{index}.jpg"
                if dest.exists() and dest.stat().st_size:
                    continue
                image = requests.get(media["url"], timeout=30)
                image.raise_for_status()
                if not image.headers.get("content-type", "").startswith("image/"):
                    raise ValueError(f"non-image response for {post_id} image {index}")
                dest.write_bytes(image.content)
    return evidence


if __name__ == "__main__":
    print(prepare())
