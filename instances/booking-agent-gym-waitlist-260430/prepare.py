"""Cache public source material for research; evaluation tasks never fetch it."""
from __future__ import annotations

from urllib.request import Request, urlopen

from ib import data_dir

ID = "booking-agent-gym-waitlist-260430"
SOURCES = {
    "affinda-archive.html": "https://web.archive.org/web/20260516025532id_/https://www.affinda.com/expert-insights/when-my-ai-agent-hacked-my-gym-mythos-stopped-feeling-theoretical/",
    "abc.html": "https://www.abc.net.au/news/2026-08-10/ai-assistant-hacks-gym-website-aus-cyber-attack/107007986",
    "abc-supplied.jpg": "https://live-production.wcms.abc-cdn.net.au/796d0ab232e5b17e9bbe573a224d2f0d?src",
}


def main() -> None:
    directory = data_dir(ID) / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = directory / name
        if path.exists() and path.stat().st_size > 0:
            print(f"cached {path}")
            continue
        with urlopen(Request(url, headers={"User-Agent": "incident-bench source archiver/1.0"}), timeout=30) as response:
            body = response.read()
        if not body:
            raise RuntimeError(f"empty response from {url}")
        path.write_bytes(body)
        print(f"downloaded {path} ({len(body)} bytes)")


if __name__ == "__main__":
    main()
