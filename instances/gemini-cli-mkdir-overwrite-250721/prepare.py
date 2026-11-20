"""Idempotently cache the released terminal trace outside the instance repository."""
from __future__ import annotations

import hashlib
import urllib.request

from ib import data_dir

ID = "gemini-cli-mkdir-overwrite-250721"
SOURCES = (
    ("gemini-cli-powershell.txt", "https://github.com/user-attachments/files/21372906/gemini.cli.screw.up.txt",
     "000ddab20df84d46ebbe70b36a73f21eadb6761b190f3d16722f404c31a34935"),
    ("prompts.ts", "https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.1.13/packages/core/src/core/prompts.ts",
     "47208ea0476eea96d0a8ed173e2a6ef53bbccffa171264963d8d71899784eaa6"),
    ("shell.ts", "https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.1.13/packages/core/src/tools/shell.ts",
     "c95d65f7d4bd7d087a1c1ca7f553bdc609abb4660f197de299b9a9d731838af1"),
)


def main() -> None:
    directory = data_dir(ID) / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    for name, url, expected_sha256 in SOURCES:
        dest = directory / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == expected_sha256:
            print(dest)
            continue
        with urllib.request.urlopen(url, timeout=30) as reply:
            content = reply.read()
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            raise ValueError(f"Released source hash changed for {name}: {digest}")
        dest.write_bytes(content)
        print(dest)


if __name__ == "__main__":
    main()
