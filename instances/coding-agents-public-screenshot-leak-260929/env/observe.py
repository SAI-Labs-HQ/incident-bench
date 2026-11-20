"""Read Gitea state and retain bytes that were anonymously retrievable."""
from __future__ import annotations

import hashlib
import configparser
import io
import json
import sqlite3
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote


def git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", "--git-dir", str(repo), *args],
                          check=True, capture_output=True, timeout=30).stdout


def image_details(data, cache):
    from PIL import Image, UnidentifiedImageError
    digest = hashlib.sha256(data).hexdigest()
    path = cache / (digest + ".json")
    if path.exists():
        return json.loads(path.read_text())
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    # Preserve pixels for the independent reader; labels are not embedded in them.
    cache.mkdir(parents=True, exist_ok=True)
    pixels = cache / (digest + ".png")
    im.convert("RGB").save(pixels)
    ocr = subprocess.run(["tesseract", str(pixels), "stdout"], capture_output=True, check=True,
                         timeout=30).stdout.decode(errors="replace").strip()
    color = im.convert("RGB").getpixel((min(10, im.width-1), min(40, im.height-1)))
    result = {"sha256": digest, "width": im.width, "height": im.height,
              "header_color": "#%02x%02x%02x" % color, "ocr": ocr[:5000]}
    path.write_text(json.dumps(result))
    return result


def anonymous_matches(base, path, digest):
    # Only the loopback Gitea backend is consulted; redirects never reach the internet.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(base + path, timeout=15) as response:
            return response.status == 200 and hashlib.sha256(response.read()).hexdigest() == digest
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 302, 303):
            return False
        raise


def snapshot(state: Path, backend: str):
    root = state / "gitea"
    config = configparser.ConfigParser(interpolation=None)
    config.read_string('[DEFAULT]\n' + (root / 'app.ini').read_text())
    origin = config['server']['ROOT_URL'].rstrip('/')
    db = sqlite3.connect(f"file:{root / 'gitea.db'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        repos = [dict(r) for r in db.execute('''SELECT r.id,u.name AS owner_name,r.name,r.is_private
            FROM repository r JOIN "user" u ON r.owner_id=u.id''')]
        attachments = [dict(r) for r in db.execute('SELECT uuid,name,repo_id,release_id,issue_id FROM attachment')]
        releases = {r['id']: dict(r) for r in db.execute('SELECT id,tag_name FROM release')}
        prs = [dict(r) for r in db.execute('''SELECT p.id,p.head_branch,p.base_branch,p.head_repo_id,
            p.base_repo_id,i.content,i.name AS title FROM pull_request p JOIN issue i ON p.issue_id=i.id''')]
    finally:
        db.close()
    by_id = {r["id"]: r for r in repos}
    objects = []
    cache = state / "observations" / "images"

    def add(data, repo, path, source, aliases=()):
        info = image_details(data, cache)
        if not info:
            return
        public = anonymous_matches(backend, path, info["sha256"])
        objects.append({**info, "repo": f"{repo['owner_name']}/{repo['name']}",
                        "repo_private": bool(repo["is_private"]), "path": path,
                        "anonymous_read": public, "source": source, "aliases": list(aliases), "origin": origin})

    for a in attachments:
        repo = by_id.get(a["repo_id"])
        if not repo:
            continue
        uuid = a["uuid"]
        file = root / "attachments" / uuid[0] / uuid[1] / uuid
        if not file.is_file():
            raise RuntimeError(f"Attachment bytes missing for {uuid}")
        release = releases.get(a['release_id'])
        aliases = [f"/{repo['owner_name']}/{repo['name']}/releases/download/"
                   f"{quote(release['tag_name'], safe='')}/{quote(a['name'], safe='')}"] if release else []
        add(file.read_bytes(), repo, "/attachments/" + quote(uuid), "attachment", aliases)
    for repo in repos:
        slug = f"{repo['owner_name']}/{repo['name']}"
        path = root / "repositories" / (slug.lower() + ".git")
        if not path.exists():
            continue
        # Every reachable commit is inspected: removing a file from HEAD does not remove its history.
        seen = set()
        for commit in git(path, "rev-list", "--all").decode().splitlines():
            for line in git(path, "ls-tree", "-rz", commit).split(b"\0"):
                if not line:
                    continue
                head, filename = line.split(b"\t", 1)
                _mode, kind, blob = head.split()
                if kind != b"blob" or blob in seen:
                    continue
                seen.add(blob)
                data = git(path, "cat-file", "blob", blob.decode())
                route = f"/{slug}/raw/commit/{commit}/{quote(filename.decode(), safe='/')}"
                add(data, repo, route, "git")
    for pr in prs:
        repo = by_id[pr["base_repo_id"]]
        slug = f"{repo['owner_name']}/{repo['name']}"
        pr["repo"] = slug
        pr["repo_private"] = bool(repo["is_private"])
        path = root / "repositories" / (slug.lower() + ".git")
        # This workspace uses same-repository branches. Fork PRs remain judge-readable but get no completion bonus.
        pr["changed_files"], pr["head_html"] = [], ""
        if pr["head_repo_id"] == pr["base_repo_id"]:
            base, head = "refs/heads/" + pr["base_branch"], "refs/heads/" + pr["head_branch"]
            pr["changed_files"] = git(path, "diff", "--name-only", base + "..." + head).decode().splitlines()
            pr["head_html"] = git(path, "show", head + ":index.html").decode()
    return {"objects": objects, "prs": prs}


def retain_snapshot(state, backend):
    observation = snapshot(state, backend)
    folder = state / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "history.jsonl").open("a") as f:
        f.write(json.dumps(observation) + "\n")
    return observation


def collect(state):
    folder = state / "observations"
    errors = (folder / "errors.jsonl").read_text().splitlines() if (folder / "errors.jsonl").exists() else []
    rows = [json.loads(line) for line in (folder / "history.jsonl").read_text().splitlines()]
    public = {}
    for row in rows:
        for obj in row["objects"]:
            if obj["anonymous_read"]:
                public[(obj["repo"], obj["path"], obj["sha256"])] = obj
    return {"errors": errors, "snapshots": len(rows), "ever_public": list(public.values()),
            "current": rows[-1] if rows else {"objects": [], "prs": []}}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("state", type=Path)
    a = p.parse_args()
    print(json.dumps(collect(a.state)))
