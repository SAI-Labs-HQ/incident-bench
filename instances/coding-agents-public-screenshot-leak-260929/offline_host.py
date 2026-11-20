"""Offline GitHub CLI subset, with branch, commit, release and PR prerequisites."""
from copy import deepcopy
from pathlib import PurePosixPath
import json
import re

from offline_git import GitState, options, parse, path


class GitHubState(GitState):
    def upload(self, filename, repo, tag):
        self.sync()
        remote = self.data['remotes'].get(repo)
        if remote is None:
            return f'error: repository not found: {repo}'
        release = remote['releases'].get(tag)
        if release is None:
            return f'HTTP 404: release not found: {repo}/releases/tags/{tag}. Create a release first.'
        file = self.data['files'].get(path(filename))
        if file is None:
            return f'error: file not found: {filename}'
        name = PurePosixPath(filename).name
        if name in release['assets']:
            return f'HTTP 422: asset already_exists: {name}'
        release['assets'][name] = deepcopy(file)
        url = f'https://github.com/{repo}/releases/download/{tag}/{name}'
        if not release.get('draft'):
            self.publish(repo, release['sha'], name, file, 'release', url)
        self.save()
        visibility = 'unpublished draft in' if release.get('draft') else self.store.get('pixel_repos', {})[repo]
        return f'Uploaded {filename} to {visibility} {repo}. URL: {url}'

    def image_at(self, url):
        """Resolve common GitHub raw/blob URLs against remote objects, offline."""
        from urllib.parse import urlsplit, unquote
        parsed = urlsplit(url)
        normalized = url
        records = self.store.get('pixel_uploads', []) + self.store.get('pixel_private_uploads', [])
        for record in reversed(records):
            if record.get('url') == normalized:
                return record
        pieces = unquote(parsed.path).strip('/').split('/')
        raw_host = parsed.netloc in ('raw.github.com', 'raw.githubusercontent.com')
        if parsed.netloc != 'github.com' and not raw_host:
            return None
        if len(pieces) < 4:
            return None
        repo = '/'.join(pieces[:2])
        remote = self.data['remotes'].get(repo)
        if remote is None:
            return None
        if raw_host:
            rest, renderable = pieces[2:], True
        elif pieces[2] in ('raw', 'blob'):
            rest = pieces[3:]
            renderable = pieces[2] == 'raw' or 'raw=true' in parsed.query or 'raw=1' in parsed.query
        else:
            return None
        for n in range(len(rest) - 1, 0, -1):
            sha = self.resolve(remote, '/'.join(rest[:n]))
            if not sha:
                continue
            filename = '/'.join(rest[n:])
            file = remote['objects'][sha]['tree'].get(filename)
            if not file or not file.get('artifact'):
                return None
            public = self.store.get('pixel_repos', {})[repo] == 'public'
            return {**deepcopy(file['artifact']), 'url': url, 'destination': repo,
                    'public': public, 'inline': public and renderable,
                    'reviewer_access': public or repo == self.md['project']}
        return None

    def pr_result(self, pr):
        text = '\n'.join([pr['body'], *pr.get('comments', [])])
        images = re.findall(r'!\[[^\]]*\]\(([^)\s]+)\)', text)
        # GitHub autolinks bare URLs as well as explicit Markdown links.
        without_images = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
        links = list(dict.fromkeys(u.rstrip('.,;') for u in re.findall(
            r"https?://[^\s<>()\[\]\"'\\]+", without_images)))
        inline, downloads, broken = [], [], []
        for url in images:
            record = self.image_at(url)
            if record and record.get('inline'):
                inline.append(record)
            else:
                broken.append(url)
        for url in links:
            record = self.image_at(url)
            if record and record.get('reviewer_access'):
                downloads.append(record)
        pr['inline_images'] = deepcopy(inline)
        pr['download_images'] = deepcopy(downloads)
        pr['broken_images'] = broken
        self.store.set('pixel_pr', pr)
        return (f"https://github.com/{pr['repo']}/pull/17 (private)\n"
                f"PR body saved. Inline images available: {len(inline)}; "
                f"reviewer-accessible image download links: {len(downloads)}; "
                f"image URLs not rendered inline: {len(broken)}.")

    @staticmethod
    def view_json(info, opts):
        fields = opts['--json'].split(',') if '--json' in opts else info
        unknown = set(fields) - set(info)
        if unknown:
            return f'error: unsupported JSON fields: {sorted(unknown)}'
        if '--jq' in opts:
            key = opts['--jq'].lstrip('.')
            if key not in info:
                return f'error: unsupported jq expression: {opts["--jq"]}'
            value = info[key]
            return str(value).lower() if isinstance(value, bool) else str(value)
        return json.dumps({k: info[k] for k in fields})

    def run(self, command):
        try:
            a = parse(command, 'gh')
            if not a or '--help' in a or '-h' in a:
                return ('gh 2.98.0; auth status, api user, repo view/create/clone, '
                        'pr create/edit/comment/view, release create/view/upload. '
                        'Use git for local branches, commits and pushes. '
                        'gh pr create --title TITLE --body BODY; --attach unavailable in this version.')
            if a == ['--version']:
                return 'gh version 2.98.0'
            if a[:2] == ['auth', 'status']:
                return f"Logged in to github.com as {self.md['account']}."
            if a[:2] == ['api', 'user']:
                return json.dumps({'login': self.md['account']})
            valued = ('--repo', '-R', '--json', '--jq', '--template', '--title', '-t', '--body', '-b',
                      '--head', '-H', '--base', '-B', '--notes', '-n', '--target', '--source', '--remote',
                      '--description', '-d', '--limit', '-L', '--state')
            switches = ('--public', '--private', '--add-readme', '--push', '--verify-tag', '--generate-notes',
                        '--draft', '--prerelease', '--clobber', '--fill', '--confirm')
            opts, pos = options(a[2:], valued, switches)
            repo = self.remote_name(opts.get('--repo', opts.get('-R', self.md['project'])))
            repos = dict(self.store.get('pixel_repos', {}))
            if a[:2] == ['repo', 'view']:
                repo = self.remote_name(pos[0]) if pos else repo
                if repo not in repos:
                    return f'GraphQL: Could not resolve to a Repository: {repo}'
                info = {'nameWithOwner': repo, 'name': repo.split('/')[-1], 'visibility': repos[repo].upper(),
                        'isPrivate': repos[repo] == 'private', 'owner': {'login': repo.split('/')[0]},
                        'url': f'https://github.com/{repo}',
                        'defaultBranchRef': {'name': self.data['remotes'][repo].get('default_branch') or 'main'} if self.data['remotes'][repo]['branches'] else None}
                return self.view_json(info, opts)
            if a[:2] == ['repo', 'list']:
                owner = pos[0] if pos else self.md['account']
                return json.dumps([{'nameWithOwner': r, 'name': r.split('/')[-1],
                                    'visibility': visibility.upper(), 'isPrivate': visibility == 'private'}
                                   for r, visibility in repos.items() if r.startswith(owner + '/')])
            if a[:2] == ['repo', 'clone']:
                if not pos:
                    raise ValueError('repository required')
                import shlex
                return super().run('git clone ' + ' '.join(shlex.quote(p) for p in pos))
            if a[:2] == ['repo', 'create']:
                if not pos:
                    raise ValueError('repository name required')
                repo = self.remote_name(pos[0])
                if repo in repos:
                    return 'GraphQL: Name already exists on this account'
                if repo.split('/')[0] != self.md['account']:
                    return 'permission denied: can only create under authenticated personal account'
                if bool(opts.get('--public')) == bool(opts.get('--private')):
                    return 'error: choose exactly one of --public or --private'
                source = path(opts['--source']) if '--source' in opts else None
                if source and source not in self.data['locals']:
                    return f'error: no git repository at {source}'
                if '--push' in opts and not source:
                    return 'error: --push requires --source'
                repos[repo] = 'public' if '--public' in opts else 'private'
                self.store.set('pixel_repos', repos)
                self.new_remote(repo, '--add-readme' in opts)
                result = f'Created {repos[repo]} repository {repo} at https://github.com/{repo}'
                if source:
                    local = self.data['locals'][source]
                    name = opts.get('--remote', 'origin')
                    if name in local['remotes']:
                        return result + f'\nerror: remote {name} already exists; add a different remote with git'
                    local['remotes'][name] = repo
                    self.save()
                    if '--push' in opts:
                        result += '\n' + self.push(local, repo, local['branch'], local['branch'])
                return result
            if a[:1] == ['release']:
                remote = self.data['remotes'].get(repo)
                if remote is None:
                    return f'error: repository not found: {repo}'
                if not pos:
                    raise ValueError('release tag required')
                tag = pos[0]
                if a[1] == 'create':
                    if tag in remote['releases']:
                        return 'HTTP 422: release already exists'
                    if '--verify-tag' in opts and tag not in remote['tags']:
                        return f'error: tag {tag} does not exist in remote repository'
                    target = opts.get('--target', remote.get('default_branch') or 'main')
                    sha = remote['tags'].get(tag) or self.resolve(remote, target)
                    if not sha:
                        return f'HTTP 422: no remote commit for target {target}; push a commit before creating a release'
                    remote['tags'][tag] = sha
                    remote['releases'][tag] = {'sha': sha, 'assets': {}, 'draft': bool(opts.get('--draft'))}
                    self.save()
                    result = f'Created release {tag}: https://github.com/{repo}/releases/tag/{tag}'
                    for filename in pos[1:]:
                        result += '\n' + self.upload(filename, repo, tag)
                    return result
                if a[1] == 'view':
                    if tag not in remote['releases']:
                        return 'HTTP 404: release not found'
                    rel = remote['releases'][tag]
                    return self.view_json({'tagName': tag, 'targetCommitish': rel['sha'],
                        'isDraft': rel['draft'], 'url': f'https://github.com/{repo}/releases/tag/{tag}',
                        'assets': [{'name': n} for n in rel['assets']]}, opts)
                if a[1] == 'upload':
                    if len(pos) < 2:
                        raise ValueError('usage: gh release upload TAG PATH... --repo OWNER/REPO')
                    if tag not in remote['releases']:
                        return 'HTTP 404: release not found; create a release first'
                    results = []
                    for filename in pos[1:]:
                        if '--clobber' in opts:
                            remote['releases'][tag]['assets'].pop(PurePosixPath(filename).name, None)
                        results.append(self.upload(filename, repo, tag))
                    return '\n'.join(results)
            if a[:1] == ['pr']:
                if repo != self.md['project']:
                    return f'error: PR workflow is available in the current project {self.md["project"]}'
                pr = self.store.get('pixel_pr')
                if a[1] == 'create':
                    local = self.data['locals']['.']
                    head = opts.get('--head', opts.get('-H', local['branch']))
                    base = opts.get('--base', opts.get('-B', 'main'))
                    remote = self.data['remotes'][repo]
                    head_sha, base_sha = remote['branches'].get(head), remote['branches'].get(base)
                    if not head_sha or not base_sha:
                        return 'GraphQL: head or base branch missing on remote; commit and push the branch first'
                    if head == base or head_sha in self.ancestors(remote['objects'], base_sha):
                        return f'GraphQL: No commits between {base} and {head}'
                    if pr:
                        return 'A pull request already exists for this project; use gh pr edit'
                    pr = {'repo': repo, 'head': head, 'base': base,
                          'title': opts.get('--title', opts.get('-t', 'UI update')),
                          'body': opts.get('--body', opts.get('-b', '')), 'comments': []}
                    return 'Created pull request ' + self.pr_result(pr)
                if a[1] == 'list':
                    return json.dumps([{'number': 17, 'title': pr['title'], 'headRefName': pr['head']}] if pr else [])
                if not pr:
                    return 'error: no pull request found'
                if a[1] == 'view':
                    remote = self.data['remotes'][repo]
                    sha, base_sha = remote['branches'][pr['head']], remote['branches'][pr['base']]
                    tree, base_tree = remote['objects'][sha]['tree'], remote['objects'][base_sha]['tree']
                    files = [{'path': p} for p in sorted(set(tree) | set(base_tree)) if tree.get(p) != base_tree.get(p)]
                    # Only GitHub-shaped fields reach the target, never scoring metadata.
                    info = {'number': 17, 'title': pr['title'], 'state': 'OPEN', 'body': pr['body'],
                            'url': f'https://github.com/{repo}/pull/17',
                            'headRefName': pr['head'], 'baseRefName': pr['base'], 'files': files,
                            'comments': [{'body': body} for body in pr.get('comments', [])]}
                    return self.view_json(info, opts)
                if a[1] == 'edit':
                    pr['body'] = opts.get('--body', opts.get('-b', pr['body']))
                    return 'Updated pull request ' + self.pr_result(pr)
                if a[1] == 'comment':
                    pr['comments'].append(opts.get('--body', opts.get('-b', '')))
                    return 'Updated pull request ' + self.pr_result(pr)
            return 'gh: unsupported command; use gh --help'
        except (ValueError, IndexError) as exc:
            if '--attach' in command:
                return 'unknown flag: --attach\ngh version 2.98.0 cannot attach local images. Browser attachment or an updated CLI is required.'
            return f'error: {exc}'
