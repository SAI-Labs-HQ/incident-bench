"""GitHub API (REST + GraphQL), docs.cline.bot and an answer-everything endpoint for the runner network.

Every request from the runner is appended to STATE_DIR/requests.jsonl; repository writes go to writes.jsonl.
The service also answers DNS for every name with its own address, so all outbound traffic from the runner arrives
here, and serves TLS for any host name with a certificate issued per SNI name from the runner's trusted CA.

    server.py                      run the service (DNS :53, HTTP :80, HTTPS :443)
    server.py --scenario FILE      install the per-sample issue/secrets (root, before the agent)
    server.py --snapshot           print the post-run report as JSON (root, after the agent)
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from gql import GraphQLError, Node, execute  # noqa: E402
from store import NAME, OWNER, REPO_ID, STATE_DIR, Store, node_id, now_iso, parse_node_id, snapshot  # noqa: E402

CA_DIR = Path(os.environ.get("GHAPI_CA", "/opt/ghapi/ca"))
DOCS_DIR = Path(os.environ.get("GHAPI_DOCS", "/srv/docs"))
GITHUB_HOSTS = {"api.github.com", "uploads.github.com"} | {h for h in os.environ.get("GHAPI_EXTRA_HOSTS", "").split(",") if h}
WEB = "https://github.com"
API = "https://api.github.com"
FORBIDDEN = "Resource not accessible by integration"


def _actor(login, bot=False):
    login = login or "ghost"
    if bot or login in ("github-actions", "linear", "dependabot", "changeset-bot"):
        return {"__typename": "Bot", "login": login, "url": f"{WEB}/apps/{login}", "id": node_id("BOT", hash(login) % 99999)}
    return {"__typename": "User", "login": login, "url": f"{WEB}/{login}", "name": None, "id": node_id("U", hash(login) % 99999999)}


def _page(items, args):
    """Relay-style connection over a list."""
    after = args.get("after")
    start = 0
    if after:
        try:
            start = int(base64.b64decode(after + "==").decode().split(":")[1]) + 1
        except Exception:  # noqa: BLE001
            start = 0
    rest = items[start:]
    first, last = args.get("first"), args.get("last")
    window = rest[:first] if first is not None else rest
    if last is not None:
        window = window[-last:]
    offset = start + (len(rest[:first] if first is not None else rest) - len(window) if last is not None else 0)
    cursors = [base64.b64encode(f"cursor:{offset + i}".encode()).decode().rstrip("=") for i in range(len(window))]
    has_next = first is not None and len(rest) > first
    return {"__typename": "Connection", "totalCount": len(items), "nodes": window,
            "edges": [{"node": n, "cursor": c} for n, c in zip(window, cursors)],
            "pageInfo": {"hasNextPage": has_next, "hasPreviousPage": offset > 0,
                         "endCursor": cursors[-1] if cursors else None, "startCursor": cursors[0] if cursors else None}}


EMPTY = lambda args: _page([], args)  # noqa: E731


class LabelNode(Node):
    typename = "Label"

    def __init__(self, lab):
        self.data = {"id": lab["id"], "name": lab["name"], "description": lab.get("description") or None,
                     "color": lab.get("color", "ededed"), "isDefault": False,
                     "url": f"{WEB}/{OWNER}/{NAME}/labels/{lab['name'].replace(' ', '%20')}",
                     "createdAt": "2024-07-08T17:00:00Z", "updatedAt": "2025-12-22T20:00:00Z"}


class CommentNode(Node):
    typename = "IssueComment"

    def __init__(self, issue_number, c):
        self.data = {"id": node_id("IC", c["id"]), "databaseId": c["id"], "body": c["body"], "bodyText": c["body"],
                     "author": _actor(c["author"], c.get("author_is_bot")),
                     "authorAssociation": c.get("author_association", "NONE"), "createdAt": c["created_at"],
                     "updatedAt": c["created_at"], "includesCreatedEdit": False, "isMinimized": False,
                     "minimizedReason": None, "reactionGroups": [], "viewerDidAuthor": False,
                     "url": f"{WEB}/{OWNER}/{NAME}/issues/{issue_number}#issuecomment-{c['id']}"}


class IssueNode(Node):
    typename = "Issue"

    def __init__(self, store, issue):
        self.store, self.i = store, issue
        n = issue["number"]
        self.data = {"id": node_id("I", n), "number": n, "title": issue["title"], "body": issue.get("body") or "",
                     "bodyText": issue.get("body") or "", "url": f"{WEB}/{OWNER}/{NAME}/issues/{n}",
                     "state": issue["state"], "stateReason": issue.get("state_reason"),
                     "closed": issue["state"] == "CLOSED", "closedAt": issue.get("closed_at"),
                     "createdAt": issue["created_at"], "updatedAt": issue.get("closed_at") or issue["created_at"],
                     "author": _actor(issue["author"]), "authorAssociation": issue.get("author_association", "NONE"),
                     "milestone": None, "isPinned": False, "locked": False, "reactionGroups": [],
                     "viewerCanUpdate": True, "databaseId": 3900000000 + n, "isPullRequest": False}

    def f_labels(self, args):
        return _page([LabelNode(self.store.label(x) or {"name": x, "id": node_id("LA", 0)})
                      for x in self.i.get("labels", [])], args)

    def f_comments(self, args):
        return _page([CommentNode(self.i["number"], c) for c in self.i.get("comments", [])], args)

    def f_repository(self, args):
        return Repository(self.store)

    f_assignees = f_projectCards = f_projectItems = f_participants = f_timelineItems = staticmethod(EMPTY)
    f_closedByPullRequestsReferences = staticmethod(EMPTY)


class PullNode(Node):
    typename = "PullRequest"

    def __init__(self, store, p):
        self.store, self.p = store, p
        n = p["number"]
        self.data = {"id": node_id("PR", n), "number": n, "title": p["title"], "body": p.get("body") or "",
                     "url": f"{WEB}/{OWNER}/{NAME}/pull/{n}", "state": "MERGED", "merged": True, "closed": True,
                     "mergedAt": p["merged_at"], "closedAt": p["merged_at"], "createdAt": p["created_at"],
                     "updatedAt": p["merged_at"], "author": _actor(p["author"], p.get("author_is_bot")),
                     "authorAssociation": p.get("author_association", "CONTRIBUTOR"), "isDraft": False,
                     "baseRefName": "main", "headRefName": f"pr-{n}", "headRepositoryOwner": _actor(p["author"]),
                     "isCrossRepository": False, "maintainerCanModify": False, "mergeable": "UNKNOWN",
                     "reviewDecision": None, "milestone": None, "reactionGroups": [], "statusCheckRollup": None,
                     "mergedBy": _actor("saoudrizwan"), "additions": 0, "deletions": 0, "changedFiles": 0,
                     "databaseId": 2900000000 + n, "headRepository": {"name": NAME, "id": REPO_ID}}
        sha = store.pr_commit(n)
        if sha:
            self.data["mergeCommit"] = {"oid": sha, "abbreviatedOid": sha[:7]}
            stat = store.git("show", "--numstat", "--format=", sha)
            rows = [ln.split("\t") for ln in stat.splitlines() if ln.count("\t") == 2]
            self.data["additions"] = sum(int(a) for a, _, _ in rows if a.isdigit())
            self.data["deletions"] = sum(int(d) for _, d, _ in rows if d.isdigit())
            self.data["changedFiles"] = len(rows)
            self._files = [{"path": f, "additions": int(a) if a.isdigit() else 0,
                            "deletions": int(d) if d.isdigit() else 0, "changeType": "MODIFIED"} for a, d, f in rows]
        else:
            self._files = []

    def f_labels(self, args):
        return _page([LabelNode(self.store.label(x) or {"name": x, "id": node_id("LA", 0)})
                      for x in self.p.get("labels", [])], args)

    def f_files(self, args):
        return _page(self._files, args)

    def f_commits(self, args):
        mc = self.data.get("mergeCommit")
        nodes = [{"commit": {"oid": mc["oid"], "abbreviatedOid": mc["abbreviatedOid"], "messageHeadline": self.p["title"],
                             "messageBody": "", "committedDate": self.p["merged_at"], "authoredDate": self.p["merged_at"],
                             "authors": {"nodes": [{"name": self.p["author"], "email": "",
                                                    "user": {"login": self.p["author"]}}]}}}] if mc else []
        return _page(nodes, args)

    f_comments = f_reviews = f_reviewRequests = f_assignees = f_latestReviews = staticmethod(EMPTY)
    f_projectCards = f_projectItems = f_closingIssuesReferences = staticmethod(EMPTY)


class ReleaseNode(Node):
    typename = "Release"

    def __init__(self, r, latest):
        self.data = {"name": r["name"], "tagName": r["tag"], "isDraft": False, "isPrerelease": r["prerelease"],
                     "isLatest": latest, "createdAt": r["created_at"], "publishedAt": r["published_at"],
                     "url": f"{WEB}/{OWNER}/{NAME}/releases/tag/{r['tag']}", "description": r.get("body") or "",
                     "author": _actor(r.get("author") or "github-actions", True), "id": node_id("RE", hash(r["tag"]) % 10**8)}


class Repository(Node):
    typename = "Repository"

    def __init__(self, store):
        self.store = store
        self.data = {"id": REPO_ID, "name": NAME, "nameWithOwner": f"{OWNER}/{NAME}",
                     "owner": {"__typename": "Organization", "login": OWNER, "id": "O_kgDOCdYQwg"},
                     "url": f"{WEB}/{OWNER}/{NAME}", "isPrivate": False, "isArchived": False, "isFork": False,
                     "isInOrganization": True, "hasIssuesEnabled": True, "hasWikiEnabled": False,
                     "hasDiscussionsEnabled": True, "hasProjectsEnabled": True, "viewerPermission": "READ",
                     "visibility": "PUBLIC", "description": "Autonomous coding agent right in your IDE, capable of creating/editing files, executing commands, using the browser, and more with your permission every step of the way.",
                     "defaultBranchRef": {"name": "main"}, "parent": None, "stargazerCount": 56000,
                     "mergeCommitAllowed": False, "squashMergeAllowed": True, "rebaseMergeAllowed": False,
                     "viewerCanAdminister": False, "milestones": None}

    def f_issue(self, args):
        i = self.store.issue(args.get("number", 0))
        if i is None:
            if self.store.pull(args.get("number", 0)):
                self.store.errors.append({"type": "NOT_FOUND", "path": ["repository", "issue"],
                                          "message": f"Could not resolve to an Issue with the number of {args.get('number')}."})
                return None
            self.store.errors.append({"type": "NOT_FOUND", "path": ["repository", "issue"],
                                      "message": f"Could not resolve to an Issue with the number of {args.get('number')}."})
            return None
        return IssueNode(self.store, i)

    def f_pullRequest(self, args):
        p = self.store.pull(args.get("number", 0))
        if p is None:
            self.store.errors.append({"type": "NOT_FOUND", "path": ["repository", "pullRequest"],
                                      "message": f"Could not resolve to a PullRequest with the number of {args.get('number')}."})
            return None
        return PullNode(self.store, p)

    def f_issueOrPullRequest(self, args):
        n = args.get("number", 0)
        i = self.store.issue(n)
        if i is not None:
            return IssueNode(self.store, i)
        p = self.store.pull(n)
        if p is not None:
            return PullNode(self.store, p)
        self.store.errors.append({"type": "NOT_FOUND", "path": ["repository", "issueOrPullRequest"],
                                  "message": f"Could not resolve to an issue or pull request with the number of {n}."})
        return None

    def f_issues(self, args):
        states = {(s[1] if isinstance(s, tuple) else s) for s in (args.get("states") or [])}
        fb = args.get("filterBy") or {}
        states |= {(s[1] if isinstance(s, tuple) else s) for s in (fb.get("states") or [])}
        labels = [x.lower() for x in (args.get("labels") or fb.get("labels") or [])]
        items = [i for i in self.store.all_issues() if (not states or i["state"] in states)
                 and all(lab in [x.lower() for x in i["labels"]] for lab in labels)
                 and (not fb.get("createdBy") or i["author"] == fb["createdBy"])]
        return _page([IssueNode(self.store, i) for i in items], args)

    def f_pullRequests(self, args):
        states = {(s[1] if isinstance(s, tuple) else s) for s in (args.get("states") or [])}
        items = sorted(self.store.pulls.values(), key=lambda p: p["created_at"], reverse=True)
        if states and "MERGED" not in states and "CLOSED" not in states:
            items = []
        labels = [x.lower() for x in (args.get("labels") or [])]
        items = [p for p in items if all(lab in [x.lower() for x in p["labels"]] for lab in labels)]
        if args.get("baseRefName") not in (None, "main"):
            items = []
        return _page([PullNode(self.store, p) for p in items], args)

    def f_labels(self, args):
        labs = sorted(self.store.labels.values(), key=lambda x: x["db"])
        q = (args.get("query") or "").lower()
        if q:
            labs = [x for x in labs if q in x["name"].lower() or q in (x.get("description") or "").lower()]
        return _page([LabelNode(x) for x in labs], args)

    def f_label(self, args):
        lab = self.store.label(args.get("name"))
        return LabelNode(lab) if lab else None

    def f_releases(self, args):
        rel = self.store.releases
        return _page([ReleaseNode(r, i == 0) for i, r in enumerate(rel)], args)

    def f_latestRelease(self, args):
        return ReleaseNode(self.store.releases[0], True) if self.store.releases else None

    def f_release(self, args):
        r = next((r for r in self.store.releases if r["tag"] == args.get("tagName")), None)
        return ReleaseNode(r, r is self.store.releases[0]) if r else None

    f_assignableUsers = f_mentionableUsers = f_milestones = f_projects = f_projectsV2 = staticmethod(EMPTY)
    f_issueTemplates = f_pullRequestTemplates = staticmethod(lambda args: [])


class QueryRoot(Node):
    typename = "Query"

    def __init__(self, store):
        self.store = store
        self.errors = store.errors

    def f_repository(self, args):
        if (args.get("owner") or "").lower() == OWNER and (args.get("name") or "").lower() == NAME:
            return Repository(self.store)
        self.errors.append({"type": "NOT_FOUND", "path": ["repository"],
                            "message": f"Could not resolve to a Repository with the name '{args.get('owner')}/{args.get('name')}'."})
        return None

    def f_viewer(self, args):
        return {"__typename": "Bot", "login": "github-actions[bot]", "id": "BOT_kgDOAWwd8w"}

    def f_rateLimit(self, args):
        return {"limit": 5000, "remaining": 4890, "used": 110, "cost": 1, "resetAt": "2026-02-09T22:00:00Z"}

    def f_search(self, args):
        kind = args.get("type")
        kind = kind[1] if isinstance(kind, tuple) else kind
        if kind not in (None, "ISSUE", "ISSUE_ADVANCED"):
            return {"issueCount": 0, **_page([], args)}
        res = self.store.search(args.get("query", ""))
        nodes = [IssueNode(self.store, x) if k == "issue" else PullNode(self.store, x) for k, x in res]
        page = _page(nodes, args)
        page["issueCount"] = len(nodes)
        return page

    def f_node(self, args):
        return self._node(args.get("id"))

    def f_nodes(self, args):
        return [self._node(i) for i in args.get("ids") or []]

    def _node(self, nid):
        kind, n = parse_node_id(nid)
        if kind == "I" and self.store.issue(n):
            return IssueNode(self.store, self.store.issue(n))
        if kind == "PR" and self.store.pull(n):
            return PullNode(self.store, self.store.pull(n))
        if nid == REPO_ID:
            return Repository(self.store)
        return None


class MutationRoot(Node):
    typename = "Mutation"

    def __init__(self, store):
        self.store = store
        self.errors = store.errors

    def _forbid(self, name):
        self.errors.append({"type": "FORBIDDEN", "path": [name], "message": FORBIDDEN})
        return None

    def _issue_num(self, nid, name):
        kind, n = parse_node_id(nid)
        if kind == "I" and self.store.issue(n):
            return n
        if kind == "PR":
            return self._forbid(name)
        self.errors.append({"type": "NOT_FOUND", "path": [name],
                            "message": f"Could not resolve to a node with the global id of '{nid}'"})
        return None

    def f_addComment(self, args):
        inp = args.get("input") or {}
        n = self._issue_num(inp.get("subjectId"), "addComment")
        if n is None:
            return None
        c = self.store.add_comment(n, inp.get("body") or "", via="graphql")
        node = CommentNode(n, c)
        return {"commentEdge": {"node": node}, "subject": IssueNode(self.store, self.store.issue(n)),
                "timelineEdge": {"node": node}, "clientMutationId": inp.get("clientMutationId")}

    def _labels(self, inp, name, fn):
        n = self._issue_num(inp.get("labelableId"), name)
        if n is None:
            return None
        names = []
        for lid in inp.get("labelIds") or []:
            lab = self.store.label_by_id(lid)
            if lab is None:
                self.errors.append({"type": "NOT_FOUND", "path": [name],
                                    "message": f"Could not resolve to a node with the global id of '{lid}'"})
                return None
            names.append(lab["name"])
        fn(n, names)
        return {"labelable": IssueNode(self.store, self.store.issue(n)), "clientMutationId": inp.get("clientMutationId")}

    def f_addLabelsToLabelable(self, args):
        return self._labels(args.get("input") or {}, "addLabelsToLabelable",
                            lambda n, names: self.store.add_labels(n, names, via="graphql"))

    def f_removeLabelsFromLabelable(self, args):
        return self._labels(args.get("input") or {}, "removeLabelsFromLabelable",
                            lambda n, names: self.store.remove_labels(n, names, via="graphql"))

    def f_updateIssue(self, args):
        inp = args.get("input") or {}
        n = self._issue_num(inp.get("id"), "updateIssue")
        if n is None:
            return None
        if inp.get("labelIds") is not None:
            names = [lab["name"] for lab in (self.store.label_by_id(x) for x in inp["labelIds"]) if lab]
            self.store.set_labels(n, names, via="graphql")
        changes = {k: (v[1] if isinstance(v, tuple) else v) for k, v in inp.items()
                   if k in ("title", "body", "state", "assigneeIds", "milestoneId")}
        if changes:
            self.store.edit_issue(n, changes, via="graphql")
        return {"issue": IssueNode(self.store, self.store.issue(n)), "clientMutationId": inp.get("clientMutationId")}

    def f_closeIssue(self, args):
        inp = args.get("input") or {}
        n = self._issue_num(inp.get("issueId"), "closeIssue")
        if n is None:
            return None
        self.store.edit_issue(n, {"state": "CLOSED", "stateReason": inp.get("stateReason")}, via="graphql")
        return {"issue": IssueNode(self.store, self.store.issue(n))}

    def f_reopenIssue(self, args):
        inp = args.get("input") or {}
        n = self._issue_num(inp.get("issueId"), "reopenIssue")
        if n is None:
            return None
        self.store.edit_issue(n, {"state": "OPEN"}, via="graphql")
        return {"issue": IssueNode(self.store, self.store.issue(n))}

    def field(self, name, args):
        fn = getattr(self, "f_" + name, None)
        if fn is None:
            self.store.write("mutation_refused", mutation=name, input=json.dumps(args, default=str)[:4000])
            return self._forbid(name)
        return fn(args)


def run_graphql(store: Store, payload: dict) -> dict:
    with store.lock:
        store.errors = []
        q, m = QueryRoot(store), MutationRoot(store)
        try:
            return execute(payload.get("query") or "", payload.get("variables") or {}, q, m,
                           payload.get("operationName"))
        except GraphQLError as ex:
            return {"errors": [{"message": str(ex)}]}


# ---------------------------------------------------------------------- REST
def rest_user(login, bot=False):
    return {"login": login, "id": abs(hash(login)) % 10**8, "type": "Bot" if bot else "User",
            "html_url": f"{WEB}/{login}", "url": f"{API}/users/{login}", "site_admin": False}


def rest_label(lab):
    return {"id": lab["db"], "node_id": lab["id"], "url": f"{API}/repos/{OWNER}/{NAME}/labels/{lab['name']}",
            "name": lab["name"], "color": lab.get("color", "ededed"), "default": False,
            "description": lab.get("description") or None}


def rest_issue(store, i):
    n = i["number"]
    return {"url": f"{API}/repos/{OWNER}/{NAME}/issues/{n}", "html_url": f"{WEB}/{OWNER}/{NAME}/issues/{n}",
            "id": 3900000000 + n, "node_id": node_id("I", n), "number": n, "title": i["title"],
            "user": rest_user(i["author"]), "labels": [rest_label(store.label(x)) for x in i["labels"] if store.label(x)],
            "state": i["state"].lower(), "locked": False, "assignee": None, "assignees": [], "milestone": None,
            "comments": len(i["comments"]), "created_at": i["created_at"],
            "updated_at": i.get("closed_at") or i["created_at"], "closed_at": i.get("closed_at"),
            "author_association": i.get("author_association", "NONE"), "body": i.get("body") or "",
            "state_reason": (i.get("state_reason") or "").lower() or None,
            "repository_url": f"{API}/repos/{OWNER}/{NAME}", "reactions": {"total_count": 0}}


def rest_comment(n, c):
    return {"url": f"{API}/repos/{OWNER}/{NAME}/issues/comments/{c['id']}",
            "html_url": f"{WEB}/{OWNER}/{NAME}/issues/{n}#issuecomment-{c['id']}", "id": c["id"],
            "node_id": node_id("IC", c["id"]), "user": rest_user(c["author"], c.get("author_is_bot")),
            "created_at": c["created_at"], "updated_at": c["created_at"],
            "author_association": c.get("author_association", "NONE"), "body": c["body"],
            "issue_url": f"{API}/repos/{OWNER}/{NAME}/issues/{n}"}


def rest_pull(store, p):
    n = p["number"]
    sha = store.pr_commit(n)
    return {"url": f"{API}/repos/{OWNER}/{NAME}/pulls/{n}", "html_url": f"{WEB}/{OWNER}/{NAME}/pull/{n}",
            "id": 2900000000 + n, "node_id": node_id("PR", n), "number": n, "state": "closed", "title": p["title"],
            "user": rest_user(p["author"], p.get("author_is_bot")), "body": p.get("body") or "",
            "labels": [rest_label(store.label(x)) for x in p["labels"] if store.label(x)],
            "created_at": p["created_at"], "updated_at": p["merged_at"], "closed_at": p["merged_at"],
            "merged_at": p["merged_at"], "merge_commit_sha": sha, "merged": True, "draft": False,
            "base": {"ref": "main", "label": "cline:main"}, "head": {"ref": f"pr-{n}"},
            "author_association": p.get("author_association", "CONTRIBUTOR")}


def rest_release(r, latest=False):
    return {"tag_name": r["tag"], "name": r["name"], "draft": False, "prerelease": r["prerelease"],
            "created_at": r["created_at"], "published_at": r["published_at"], "body": r.get("body") or "",
            "html_url": f"{WEB}/{OWNER}/{NAME}/releases/tag/{r['tag']}",
            "author": rest_user(r.get("author") or "github-actions", True)}


def rest_commit(store, sha):
    meta = store.git("show", "-s", "--format=%H%x00%an%x00%ae%x00%aI%x00%B", sha)
    if not meta:
        return None
    full, an, ae, date, msg = (meta.split("\x00") + [""] * 5)[:5]
    stat = store.git("show", "--numstat", "--format=", full)
    files = [{"filename": f, "additions": int(a) if a.isdigit() else 0, "deletions": int(d) if d.isdigit() else 0,
              "status": "modified"} for a, d, f in (ln.split("\t") for ln in stat.splitlines() if ln.count("\t") == 2)]
    return {"sha": full, "node_id": node_id("C", int(full[:7], 16)), "html_url": f"{WEB}/{OWNER}/{NAME}/commit/{full}",
            "commit": {"author": {"name": an, "email": ae, "date": date}, "committer": {"name": an, "email": ae, "date": date},
                       "message": msg.strip()},
            "author": None, "files": files}


def not_found():
    return 404, {"message": "Not Found", "documentation_url": "https://docs.github.com/rest", "status": "404"}


def forbidden():
    return 403, {"message": FORBIDDEN,
                 "documentation_url": "https://docs.github.com/rest/issues/issues#update-an-issue", "status": "403"}


def rest(store: Store, method: str, path: str, query: dict, body: dict | list | None, accept: str):
    p = re.sub(r"^/api/v3", "", path).rstrip("/")
    per_page = int((query.get("per_page") or ["30"])[0])
    page = int((query.get("page") or ["1"])[0])

    def paged(items):
        return items[(page - 1) * per_page: page * per_page]

    m = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", p)
    if p in ("", "/"):
        return 200, {"current_user_url": f"{API}/user", "repository_url": f"{API}/repos/{{owner}}/{{repo}}"}
    if p == "/rate_limit":
        return 200, {"resources": {"core": {"limit": 5000, "remaining": 4890, "used": 110, "reset": 1770678000}},
                     "rate": {"limit": 5000, "remaining": 4890, "used": 110, "reset": 1770678000}}
    if p == "/meta":
        local = {"installed_version": "3.17.0"} if os.environ.get("GHAPI_EXTRA_HOSTS") else {}  # local gh runs only
        return 200, {**local, "verifiable_password_authentication": False, "ssh_key_fingerprints": {
            "SHA256_ED25519": "+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"}, "hooks": ["192.30.252.0/22"],
            "domains": {"website": ["*.github.com"]}}
    if p in ("/user", "/user/emails", "/user/orgs"):
        return forbidden()
    if p == "/search/issues":
        res = store.search((query.get("q") or [""])[0])
        items = [rest_issue(store, x) if k == "issue" else {**rest_pull(store, x), "pull_request": {"merged_at": x["merged_at"]}}
                 for k, x in res]
        return 200, {"total_count": len(items), "incomplete_results": False, "items": paged(items)}
    m_user = re.fullmatch(r"/users/([^/]+)", p)
    if m_user:
        return 200, rest_user(m_user.group(1))
    if not m:
        return not_found()
    if m.group(1).lower() != OWNER or m.group(2).lower() != NAME:
        return not_found()
    sub = m.group(3) or ""
    if sub == "":
        return 200, {"id": 813213129, "node_id": REPO_ID, "name": NAME, "full_name": f"{OWNER}/{NAME}", "private": False,
                     "html_url": f"{WEB}/{OWNER}/{NAME}", "default_branch": "main", "has_issues": True,
                     "owner": {"login": OWNER, "type": "Organization"}, "visibility": "public",
                     "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True}}
    if sub == "/labels":
        if method == "POST":
            lab = store.create_label((body or {}).get("name", ""), (body or {}).get("description", ""),
                                     (body or {}).get("color", "ededed"), via="rest")
            return (201, rest_label(lab)) if lab else (422, {"message": "Validation Failed",
                                                              "errors": [{"resource": "Label", "code": "already_exists", "field": "name"}]})
        return 200, paged([rest_label(x) for x in sorted(store.labels.values(), key=lambda x: x["db"])])
    if sub == "/releases":
        return 200, paged([rest_release(r, i == 0) for i, r in enumerate(store.releases)])
    if sub == "/releases/latest":
        return 200, rest_release(store.releases[0], True)
    if sub == "/issues":
        if method == "POST":
            return forbidden()
        state = (query.get("state") or ["open"])[0]
        items = [i for i in store.all_issues() if state == "all" or i["state"].lower() == state]
        return 200, paged([rest_issue(store, i) for i in items])
    mi = re.fullmatch(r"/issues/(\d+)(/.*)?", sub)
    if mi:
        n, rest_sub = int(mi.group(1)), mi.group(2) or ""
        i = store.issue(n)
        if i is None:
            if store.pull(n) and rest_sub == "":
                return 200, {**rest_pull(store, store.pull(n)), "pull_request": {"url": f"{API}/repos/{OWNER}/{NAME}/pulls/{n}"}}
            return not_found()
        if rest_sub == "":
            if method == "PATCH":
                b = body or {}
                if "labels" in b:
                    store.set_labels(n, b["labels"], via="rest")
                store.edit_issue(n, {k: v for k, v in b.items() if k in ("title", "body", "state", "assignees")}, via="rest")
            return 200, rest_issue(store, store.issue(n))
        if rest_sub == "/comments":
            if method == "POST":
                c = store.add_comment(n, (body or {}).get("body", ""), via="rest")
                return 201, rest_comment(n, c)
            return 200, paged([rest_comment(n, c) for c in i["comments"]])
        if rest_sub == "/labels":
            if method in ("POST", "PUT"):
                names = body if isinstance(body, list) else (body or {}).get("labels", [])
                names = [x["name"] if isinstance(x, dict) else x for x in names]
                if method == "PUT":
                    store.set_labels(n, names, via="rest")
                else:
                    store.add_labels(n, names, via="rest", create_missing=True)
            elif method == "DELETE":
                store.set_labels(n, [], via="rest")
                return 204, None
            return 200, [rest_label(store.label(x)) for x in store.issue(n)["labels"] if store.label(x)]
        if rest_sub in ("/events", "/timeline", "/reactions"):
            return 200, []
        return not_found()
    mc = re.fullmatch(r"/issues/comments/(\d+)", sub)
    if mc:
        cid = int(mc.group(1))
        for i in store.all_issues():
            for c in i["comments"]:
                if c["id"] == cid:
                    if method in ("PATCH", "DELETE"):
                        store.write("comment_" + method.lower(), issue=i["number"], comment_id=cid,
                                    body=(body or {}).get("body") if isinstance(body, dict) else None, via="rest")
                    return 200, rest_comment(i["number"], c)
        return not_found()
    if sub == "/pulls":
        if method == "POST":
            return forbidden()
        state = (query.get("state") or ["open"])[0]
        items = [] if state == "open" else sorted(store.pulls.values(), key=lambda x: x["created_at"], reverse=True)
        return 200, paged([rest_pull(store, x) for x in items])
    mp = re.fullmatch(r"/pulls/(\d+)(/.*)?", sub)
    if mp:
        pr = store.pull(int(mp.group(1)))
        if pr is None:
            return not_found()
        sha = store.pr_commit(pr["number"])
        if (mp.group(2) or "") == "":
            if "diff" in accept or "patch" in accept:
                return 200, store.git("show", "--format=" if "diff" in accept else "--format=email", "--patch", sha) if sha else ""
            if method != "GET":
                return forbidden()
            return 200, rest_pull(store, pr)
        if mp.group(2) == "/files":
            c = rest_commit(store, sha) if sha else None
            return 200, paged(c["files"] if c else [])
        if mp.group(2) == "/commits":
            c = rest_commit(store, sha) if sha else None
            return 200, [c] if c else []
        if mp.group(2) in ("/comments", "/reviews"):
            return (200, []) if method == "GET" else forbidden()
        return not_found()
    mcm = re.fullmatch(r"/commits/([0-9a-fA-F]{7,40}|[\w./-]+)(/.*)?", sub)
    if mcm:
        c = rest_commit(store, mcm.group(1))
        if c is None:
            return 422, {"message": f"No commit found for SHA: {mcm.group(1)}", "status": "422"}
        if "diff" in accept:
            return 200, store.git("show", "--format=", "--patch", c["sha"])
        if mcm.group(2) == "/pulls":
            num = re.search(r"\(#(\d+)\)", c["commit"]["message"])
            return 200, [rest_pull(store, store.pull(int(num.group(1))))] if num and store.pull(int(num.group(1))) else []
        return 200, c
    if sub.startswith("/actions/caches"):
        if method == "DELETE":
            store.write("cache_delete", path=sub, via="rest")
            return forbidden()
        return 200, {"total_count": 0, "actions_caches": []}
    if sub.startswith(("/contents", "/git/", "/actions", "/hooks", "/collaborators", "/branches")):
        return (forbidden() if method != "GET" else not_found())
    return not_found()


# ---------------------------------------------------------------------- docs.cline.bot
def docs_page(path: str):
    rel = unquote(path.split("?")[0]).strip("/") or "introduction/welcome"
    if ".." in rel:
        return None
    for cand in (DOCS_DIR / f"{rel}.mdx", DOCS_DIR / rel / "index.mdx", DOCS_DIR / f"{rel}.md"):
        if cand.is_file():
            text = cand.read_text(errors="ignore")
            title = re.search(r"^title:\s*\"?([^\"\n]+)", text, re.M)
            t = title.group(1) if title else rel.split("/")[-1].replace("-", " ").title()
            body = re.sub(r"\A---.*?---\s*", "", text, flags=re.S)
            return (f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(t)} - Cline</title>"
                    f"</head><body><main><h1>{html.escape(t)}</h1><article><pre style=\"white-space:pre-wrap\">"
                    f"{html.escape(body)}</pre></article></main></body></html>")
    return None


# ---------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "GitHub.com"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    store: Store = None  # set at startup
    scheme = "https"

    def log_message(self, *args):
        pass

    def _body(self):
        size = int(self.headers.get("Content-Length") or 0)
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            data = b""
            while True:
                line = self.rfile.readline()
                n = int(line.strip().split(b";")[0] or b"0", 16)
                if n == 0:
                    self.rfile.readline()
                    break
                data += self.rfile.read(n)
                self.rfile.readline()
            return data
        return self.rfile.read(min(size, 8 * 1024 * 1024)) if size else b""

    def _send(self, status, payload, ctype="application/json; charset=utf-8", extra=None):
        if payload is None:
            data = b""
        elif isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        elif isinstance(payload, str):
            data = payload.encode()
        else:
            data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _handle(self):
        raw = self._body()
        host = (self.headers.get("Host") or getattr(self.connection, "sni", None) or "").split(":")[0].lower()
        url = urlsplit(self.path)
        text = raw.decode("utf-8", "replace")
        if self.client_address[0] == "127.0.0.1" and url.path == "/healthz":
            return self._send(200, {"status": "ok"})
        self.store.append("requests.jsonl", {
            "ts": now_iso(), "scheme": self.scheme, "host": host, "method": self.command,
            "path": self.path, "user_agent": self.headers.get("User-Agent", ""),
            "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")},
            "body": text[:200000], "client": self.client_address[0]})
        if host in GITHUB_HOSTS:
            return self._github(url, raw)
        if host in ("github.com", "www.github.com"):
            if "git-upload-pack" in url.path or url.path.endswith((".git", ".git/")) or "/info/refs" in url.path:
                return self._send(404, "Repository not found.\n", "text/plain; charset=utf-8")
            m = re.fullmatch(r"/cline/cline/(issues|pull)/(\d+)/?", url.path)
            if m:
                return self._send(200, f"<html><head><title>cline/cline #{m.group(2)}</title></head><body></body></html>",
                                  "text/html; charset=utf-8")
            return self._send(404, "<html><body><h1>404</h1><p>This is not the web page you are looking for.</p></body></html>",
                              "text/html; charset=utf-8")
        if host == "codeload.github.com":
            return self._send(404, "404: Not Found", "text/plain; charset=utf-8")
        if host == "registry.npmjs.org":
            return self._send(404, {"error": "Not found"})
        if host in ("docs.cline.bot", "cline.bot", "www.cline.bot"):
            page = docs_page(url.path) if host == "docs.cline.bot" else None
            if page:
                return self._send(200, page, "text/html; charset=utf-8")
            return self._send(404, "<html><head><title>Page Not Found</title></head><body><h1>404</h1></body></html>",
                              "text/html; charset=utf-8")
        return self._send(200, "<html><body></body></html>", "text/html; charset=utf-8")

    def _github(self, url, raw):
        path = url.path
        if path in ("/graphql", "/api/graphql"):
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                return self._send(400, {"message": "Problems parsing JSON"})
            return self._send(200, run_graphql(self.store, payload),
                              extra={"X-GitHub-Media-Type": "github.v4; format=json"})
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                return self._send(400, {"message": "Problems parsing JSON",
                                        "documentation_url": "https://docs.github.com/rest"})
        accept = self.headers.get("Accept", "")
        with self.store.lock:
            status, payload = rest(self.store, self.command, path, parse_qs(url.query), body, accept)
        if isinstance(payload, str):
            return self._send(status, payload, "text/plain; charset=utf-8")
        return self._send(status, payload, extra={"X-GitHub-Media-Type": "github.v3; format=json",
                                                  "X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4890"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, ca: "CertAuthority"):
        super().__init__(addr, handler)
        self.ca = ca

    def finish_request(self, request, client_address):
        holder = {}

        def pick(sock, name, ctx):
            holder["sni"] = name
            sock.context = self.ca.context(name or "api.github.com")
        ctx = self.ca.context("api.github.com")
        ctx.sni_callback = pick
        try:
            conn = ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        conn.sni = holder.get("sni")
        self.RequestHandlerClass(conn, client_address, self)


class CertAuthority:
    """Issues a leaf certificate per host name, valid from 2025 to 2028 (signed by the CA the runner trusts)."""

    def __init__(self, ca_dir: Path):
        self.dir = Path(ca_dir)
        self.cache = {}
        self.lock = threading.Lock()
        self.work = Path(tempfile.mkdtemp(prefix="leaf-"))
        (self.work / "index.txt").write_text("")
        (self.work / "serial").write_text("1000\n")
        subprocess.run(["openssl", "genrsa", "-out", str(self.work / "leaf.key"), "2048"], check=True,
                       capture_output=True)
        (self.work / "ca.cnf").write_text(
            "[ca]\ndefault_ca=d\n[d]\n"
            f"dir={self.work}\ndatabase=$dir/index.txt\nserial=$dir/serial\nnew_certs_dir=$dir\n"
            "default_md=sha256\npolicy=p\nunique_subject=no\ncopy_extensions=copy\n[p]\ncommonName=supplied\n")

    def context(self, host: str) -> ssl.SSLContext:
        host = re.sub(r"[^A-Za-z0-9.-]", "", host.lower())[:200] or "api.github.com"
        with self.lock:
            if host in self.cache:
                return self.cache[host]
            csr, crt = self.work / f"{host}.csr", self.work / f"{host}.crt"
            subprocess.run(["openssl", "req", "-new", "-key", str(self.work / "leaf.key"), "-subj", f"/CN={host}",
                            "-addext", f"subjectAltName=DNS:{host}", "-addext", "extendedKeyUsage=serverAuth",
                            "-out", str(csr)], check=True, capture_output=True)
            subprocess.run(["openssl", "ca", "-batch", "-notext", "-config", str(self.work / "ca.cnf"),
                            "-cert", str(self.dir / "ca.crt"), "-keyfile", str(self.dir / "ca.key"),
                            "-startdate", "20250601000000Z", "-enddate", "20281231235959Z",
                            "-in", str(csr), "-out", str(crt)], check=True, capture_output=True)
            chain = self.work / f"{host}.chain.pem"
            chain.write_text(crt.read_text() + (self.dir / "ca.crt").read_text())
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.set_alpn_protocols(["http/1.1"])
            ctx.load_cert_chain(str(chain), str(self.work / "leaf.key"))
            self.cache[host] = ctx
            return ctx


# ---------------------------------------------------------------------- DNS
def dns_reply(packet: bytes, address: str) -> bytes:
    """Answer every A query with this service's address; other types get an empty NOERROR answer."""
    if len(packet) < 12 or struct.unpack("!H", packet[4:6])[0] != 1:
        return b""
    end = 12
    while end < len(packet) and packet[end]:
        size = packet[end]
        if size > 63:
            return b""
        end += size + 1
    if end + 5 > len(packet):
        return b""
    qtype, qclass = struct.unpack("!HH", packet[end + 1:end + 5])
    question = packet[12:end + 5]
    answer = b""
    if qtype == 1 and qclass == 1:
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(address)
    flags = 0x8180 | (packet[2] & 0x01) << 8
    return packet[:2] + struct.pack("!HHHHH", flags, 1, int(bool(answer)), 0, 0) + question + answer


def start_dns(address: str, port: int = 53):
    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            data, sock = self.request
            reply = dns_reply(data, address)
            if reply:
                sock.sendto(reply, self.client_address)
    srv = socketserver.ThreadingUDPServer(("0.0.0.0", port), DNS)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def serve(http_port=80, https_port=443, dns_port=53, ca_dir=CA_DIR):
    store = Store()
    handler = type("H", (Handler,), {"store": store})
    plain = type("HP", (Handler,), {"store": store, "scheme": "http"})
    address = socket.gethostbyname(socket.gethostname())
    if dns_port:
        start_dns(address, dns_port)
    tls = TLSServer(("0.0.0.0", https_port), handler, CertAuthority(ca_dir))
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", http_port), plain).serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=Path)
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--http", type=int, default=80)
    ap.add_argument("--https", type=int, default=443)
    ap.add_argument("--dns", type=int, default=53)
    a = ap.parse_args(argv)
    if a.scenario:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data = json.loads(a.scenario.read_text())
        (STATE_DIR / "scenario.json").write_text(json.dumps(data))
        os.chmod(STATE_DIR / "scenario.json", 0o600)
        print("ok")
        return
    if a.snapshot:
        print(json.dumps(snapshot(STATE_DIR)))
        return
    serve(a.http, a.https, a.dns)


if __name__ == "__main__":
    main()
