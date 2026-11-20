"""Idempotently cache public court filings used as evidence, outside the instance repo."""
from __future__ import annotations

import hashlib
import gzip
import json
import re
import sqlite3
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from ib import data_dir

ID = "fabricated-case-citations-251119"
BASE = "https://www.oscn.net/dockets/GetDocument.aspx"
DOCUMENTS = {
    "capers_petition.pdf": ("PR-123810", "1064376949"),
    "capers_brief.pdf": ("PR-123810", "1064376957"),
    "beason_petition.pdf": ("PR-123811", "1064376154"),
    "beason_brief.pdf": ("PR-123811", "1064376146"),
}
CORPUS_URL = (
    "https://huggingface.co/datasets/docketx/us-caselaw-ok/resolve/"
    "f3307109c734a0263ab33ebb2e1925be343997db/data/opinions-2026-06-30.jsonl.gz"
)
CORPUS_SHA256 = "aa6186b8a11aa5dbf4d004175f0c8373018b16faa453709259a4647dba52a39d"
NEUTRAL = re.compile(r"\b(?:19|20)\d{2}\s+OK(?:\s+CIV\s+APP|\s+CR)?\s+\d+\b", re.I)
BODY_START = re.compile(r"(?:\u00b6\s*\d+|\b(?:dissenting|concurring)\s*:)", re.I)
NEUTRAL_INDEX = "oklahoma_neutral_index_v3.sqlite"


def header_identifier(header: str, match: re.Match) -> bool:
    """Require a citation-bearing header shape before treating a neutral citation as identity."""
    prefix = header[:match.start()]
    suffix = header[match.end():match.end() + 125]
    line = prefix.rsplit("\n", 1)[-1]
    return (bool(re.search(r"Cite\s+as\s*:\s*$", prefix[-35:], re.I))
            or not line.strip()
            or bool(re.search(r"\b(?:Case Number|Decided)\s*:", suffix, re.I)))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def build_index(corpus, index) -> int:
    temp = index.with_suffix(".download")
    temp.unlink(missing_ok=True)
    connection = sqlite3.connect(temp)
    connection.execute("CREATE TABLE cases (title_key TEXT, title TEXT, citation TEXT, court TEXT, date TEXT, source TEXT)")
    rows = []
    count = 0
    with gzip.open(corpus, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            rows.append((normalize_title(record.get("title") or ""), record.get("title") or "",
                         record.get("citation") or "", record.get("court") or "", record.get("date") or "",
                         record.get("source") or ""))
            if len(rows) >= 1000:
                connection.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?)", rows)
                count += len(rows)
                rows.clear()
    if rows:
        connection.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?)", rows)
        count += len(rows)
    connection.execute("CREATE INDEX by_title ON cases(title_key)")
    connection.commit()
    connection.close()
    temp.replace(index)
    return count


def build_neutral_index(corpus, index) -> int:
    """Index conservative opinion-header identities, not early citations to other cases."""
    temp = index.with_suffix(".download")
    temp.unlink(missing_ok=True)
    connection = sqlite3.connect(temp)
    connection.execute("CREATE TABLE cases (neutral TEXT, title TEXT, date TEXT, court TEXT, source TEXT)")
    rows = []
    with gzip.open(corpus, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            # CourtListener records sometimes contain only a dissent or begin with
            # narrative text. The first 300 characters alone are not a header:
            # Stephenson's dissent cites Christian v. Gray there as 2003 OK 10.
            header = (record.get("text") or "")[:300]
            body = BODY_START.search(header)
            if body:
                header = header[:body.start()]
            for match in NEUTRAL.finditer(header):
                if not header_identifier(header, match):
                    continue
                neutral = " ".join(match.group().upper().split())
                court = record.get("court") or ""
                expected_court = ("ok-oklacivapp" if "CIV APP" in neutral else
                                  "ok-oklacrimapp" if "OK CR" in neutral else "ok-okla")
                # A different decision year or court is a likely cross-citation.
                # Omitting ambiguous records is safer than a false positive floor.
                if (record.get("date") or "")[:4] != neutral[:4] or court != expected_court:
                    continue
                rows.append((neutral, record.get("title") or "", record.get("date") or "",
                             court, record.get("source") or ""))
            if len(rows) >= 1000:
                connection.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?)", rows)
                rows.clear()
    if rows:
        connection.executemany("INSERT INTO cases VALUES (?, ?, ?, ?, ?)", rows)
    connection.execute("CREATE INDEX by_neutral ON cases(neutral)")
    count = connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    connection.commit()
    connection.close()
    temp.replace(index)
    return count


def prepare() -> list[str]:
    data = data_dir(ID)
    root = data / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for name, (case, barcode) in DOCUMENTS.items():
        path = root / name
        blocked = root / (name + ".blocked")
        if blocked.exists() and not path.exists():
            out.append(f"{name}: unavailable ({blocked.read_text().strip()})")
            continue
        if not path.exists() or path.stat().st_size < 1000:
            url = f"{BASE}?ct=appellate&bc={barcode}&cn={case}&fmt=pdf"
            req = Request(url, headers={"User-Agent": "incident-bench-evidence/1.0"})
            try:
                with urlopen(req, timeout=30) as response:
                    body = response.read()
            except (HTTPError, URLError) as error:
                blocked.write_text(f"document endpoint error: {type(error).__name__}\n")
                out.append(f"{name}: unavailable ({blocked.read_text().strip()})")
                continue
            if not body.startswith(b"%PDF"):
                blocked.write_text("OSCN document endpoint requires human verification\n")
                out.append(f"{name}: unavailable ({blocked.read_text().strip()})")
                continue
            path.write_bytes(body)
            blocked.unlink(missing_ok=True)
        out.append(f"{name}: {path.stat().st_size} bytes")
    corpus = data / "oklahoma_opinions_2026-06-30.jsonl.gz"
    if not corpus.exists() or corpus.stat().st_size < 300_000_000:
        temp = corpus.with_suffix(".download")
        digest = hashlib.sha256()
        request = Request(CORPUS_URL, headers={"User-Agent": "incident-bench-evidence/1.0"})
        with urlopen(request, timeout=60) as response, temp.open("wb") as target:
            while block := response.read(1024 * 1024):
                target.write(block)
                digest.update(block)
        if digest.hexdigest() != CORPUS_SHA256:
            temp.unlink(missing_ok=True)
            raise ValueError("Oklahoma opinion corpus checksum differs from pinned manifest")
        temp.replace(corpus)
    out.append(f"Oklahoma opinion corpus: {corpus.stat().st_size} bytes")
    index = data / "oklahoma_case_index.sqlite"
    if not index.exists():
        count = build_index(corpus, index)
        out.append(f"Oklahoma case-title index: {count} opinion records")
    else:
        out.append(f"Oklahoma case-title index: {index.stat().st_size} bytes")
    neutral_index = data / NEUTRAL_INDEX
    if not neutral_index.exists():
        count = build_neutral_index(corpus, neutral_index)
        out.append(f"Oklahoma neutral-citation index: {count} citation records")
    else:
        out.append(f"Oklahoma neutral-citation index: {neutral_index.stat().st_size} bytes")
    return out


if __name__ == "__main__":
    for line in prepare():
        print(line)
