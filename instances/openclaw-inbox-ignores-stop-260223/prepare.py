"""Cache the three released screenshot images for human evidence review.

Evaluation itself uses only synthetic data in assets and never needs these images.
"""
from __future__ import annotations

from urllib.request import urlopen

from ib import data_dir

HERE = data_dir("openclaw-inbox-ignores-stop-260223") / "evidence"
URLS = [
    "https://pbs.twimg.com/media/HBz-x6haYAA26Cc.jpg?name=orig",
    "https://pbs.twimg.com/media/HBz-x6nbAAAOqt7.jpg?name=orig",
    "https://pbs.twimg.com/media/HBz-x6iakAAegxq.jpg?name=orig",
]


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(URLS, 1):
        path = HERE / f"original-post-{i}.jpg"
        if not path.exists() or path.stat().st_size == 0:
            with urlopen(url, timeout=30) as response:  # noqa: S310 (fixed evidence URLs)
                path.write_bytes(response.read())
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
