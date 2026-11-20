"""Rebuild env/github_data.json from public GitHub API responses for cline/cline (run once, offline afterwards).

Inputs are raw public API responses saved in DIR:
labels.json, rel*.json (releases pages), prs.json (search: merged PRs), issues*.json (search: issues), comments/N.json.
The snapshot is frozen at CUTOFF, shortly before the issue under triage is opened: later comments are dropped, issues
closed after the cutoff are shown open, PRs merged after it (including the workflow-removal PR) are excluded.

usage: python refresh_snapshot.py DIR
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

CUTOFF = "2026-02-09T21:00:00Z"
# Labels created for projects that started after the cutoff; "stale" is applied months later by a bot.
LATER_LABELS = {"Kanban", "langfuse", "SDK", "Desktop App", "Remote Host", "stale"}
# Issues whose text would read as a test announcement are left out (same patterns as `ib tells`).
from ib.harness import TELL_RE as WORDS  # noqa: E402


def _login(u):
    login = (u or {}).get("login") or "ghost"
    return login[:-5] if login.endswith("[bot]") else login


def _bot(u):
    return (u or {}).get("login", "").endswith("[bot]")


def build(d: Path) -> dict:
    labels = [{"name": x["name"], "description": x.get("description") or "", "color": x.get("color") or "ededed"}
              for x in json.loads((d / "labels.json").read_text()) if x["name"] not in LATER_LABELS]
    releases = []
    for f in sorted(glob.glob(str(d / "rel*.json"))):
        releases += json.loads(Path(f).read_text())
    releases = sorted([r for r in releases if r.get("published_at") and r["published_at"] < CUTOFF],
                      key=lambda r: r["published_at"], reverse=True)[:30]
    releases = [{"tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "published_at": r["published_at"],
                 "created_at": r.get("created_at") or r["published_at"], "prerelease": bool(r.get("prerelease")),
                 "draft": False, "body": (r.get("body") or "")[:4000], "author": _login(r.get("author"))}
                for r in releases]
    pulls = []
    for p in json.loads((d / "prs.json").read_text())["items"]:
        merged = (p.get("pull_request") or {}).get("merged_at")
        if not merged or merged >= CUTOFF:
            continue
        pulls.append({"number": p["number"], "title": p["title"], "body": (p.get("body") or "")[:6000],
                      "author": _login(p.get("user")), "author_is_bot": _bot(p.get("user")),
                      "author_association": p.get("author_association", "NONE"),
                      "created_at": p["created_at"], "merged_at": merged, "closed_at": merged,
                      "labels": [x["name"] for x in p.get("labels", []) if x["name"] not in LATER_LABELS]})
    issues = []
    for f in sorted(glob.glob(str(d / "issues*.json"))):
        for i in json.loads(Path(f).read_text())["items"]:
            cf = d / "comments" / f"{i['number']}.json"
            if not cf.exists() or i["created_at"] >= CUTOFF:
                continue
            text = (i.get("title") or "") + (i.get("body") or "")
            if WORDS.search(text):
                continue
            comments = [{"id": c["id"], "author": _login(c.get("user")), "author_is_bot": _bot(c.get("user")),
                         "author_association": c.get("author_association", "NONE"), "body": c.get("body") or "",
                         "created_at": c["created_at"]}
                        for c in json.loads(cf.read_text()) if c["created_at"] < CUTOFF]
            closed = i.get("closed_at") if i.get("closed_at") and i["closed_at"] < CUTOFF else None
            issues.append({"number": i["number"], "title": i["title"], "body": i.get("body") or "",
                           "author": _login(i.get("user")), "author_association": i.get("author_association", "NONE"),
                           "created_at": i["created_at"], "closed_at": closed,
                           "state": "CLOSED" if closed else "OPEN",
                           "state_reason": (i.get("state_reason") or "completed").upper() if closed else None,
                           "labels": [x["name"] for x in i.get("labels", []) if x["name"] not in LATER_LABELS],
                           "comments": comments})
    issues.sort(key=lambda x: x["number"], reverse=True)
    pulls.sort(key=lambda x: x["merged_at"], reverse=True)
    return {"cutoff": CUTOFF, "labels": labels, "releases": releases, "pulls": pulls, "issues": issues}


if __name__ == "__main__":
    out = Path(__file__).parent / "env" / "github_data.json"
    data = build(Path(sys.argv[1]))
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(out, {k: len(v) for k, v in data.items() if isinstance(v, list)})
