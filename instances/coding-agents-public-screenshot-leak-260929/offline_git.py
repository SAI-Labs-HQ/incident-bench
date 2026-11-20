"""In-memory git/hosting subset. No subprocess, filesystem I/O, or network.

Objects are immutable snapshots with image provenance. Publishing happens only
when reachable commits enter a remote, including images removed in later commits.
SHAs identify the immutable objects in this module.
"""
from copy import deepcopy
from hashlib import sha1
import fnmatch
import posixpath
import shlex


def path(value):
    return posixpath.normpath(value)


def parse(command, program):
    args = shlex.split(command)
    if args and args[0] == program:
        args.pop(0)
    if any(a in ('&&', '||', ';', '|') for a in args):
        raise ValueError(f'one {program} command per call is supported')
    return args


def options(args, valued=(), switches=()):
    """Reject unsupported flags rather than silently doing a different action."""
    opts, pos = {}, []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            pos.extend(args[i + 1:])
            break
        key, sep, value = arg.partition('=')
        if key in valued:
            if not sep:
                i += 1
                if i >= len(args):
                    raise ValueError(f'{key} requires a value')
                value = args[i]
            opts[key] = value
        elif key in switches and not sep:
            opts[key] = True
        elif arg.startswith('-'):
            raise ValueError(f'unsupported option: {arg}')
        else:
            pos.append(arg)
        i += 1
    return opts, pos


class GitState:
    def __init__(self, store):
        self.store = store
        self.md = store.get('pixel_scenario', {})
        data = store.get('pixel_git')
        if data is None:
            tree = {'index.html': {'content': self.md.get('initial_html', '')}}
            obj = self.commit_object(tree, None, 'Initial app')
            sha = obj['sha']
            data = {'files': deepcopy(tree), 'locals': {'.': {
                'branch': 'main', 'branches': {'main': sha}, 'objects': {sha: obj},
                'index': deepcopy(tree), 'remotes': {'origin': self.md['project']}, 'tags': {},
                'upstream': {'main': ['origin', 'main']}}},
                'remotes': {self.md['project']: {'branches': {'main': sha},
                    'objects': {sha: obj}, 'tags': {}, 'releases': {}, 'default_branch': 'main'}}}
            store.set('pixel_git', data)
        self.data = data

    @staticmethod
    def commit_object(tree, parent, message):
        import json
        obj = {'tree': deepcopy(tree), 'parent': parent, 'message': message}
        obj['sha'] = sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()
        return obj

    def save(self):
        self.store.set('pixel_git', self.data)

    def sync(self):
        self.data['files']['index.html'] = {'content': self.store.get('pixel_html', self.md['initial_html'])}
        for art in self.store.get('pixel_artifacts', []):
            self.data['files'][art['path']] = {'content': art['content'], 'artifact': deepcopy(art)}
        self.save()

    def remote_name(self, value):
        for prefix in ('https://github.com/', 'git@github.com:'):
            if value.startswith(prefix):
                value = value[len(prefix):]
        value = value.removesuffix('.git').rstrip('/')
        if '/' not in value:
            value = self.md['project'] if value == self.md['project'].split('/')[-1] else f"{self.md['account']}/{value}"
        return value

    def new_remote(self, name, readme=False):
        r = {'branches': {}, 'objects': {}, 'tags': {}, 'releases': {}, 'default_branch': None}
        if readme:
            obj = self.commit_object({'README.md': {'content': name}}, None, 'Initial commit')
            r['branches']['main'] = obj['sha']
            r['default_branch'] = 'main'
            r['objects'][obj['sha']] = obj
        self.data['remotes'][name] = r
        self.save()
        return r

    @staticmethod
    def resolve(local, ref):
        if ref == 'HEAD':
            return local['branches'].get(local.get('branch'))
        sha = local['branches'].get(ref) or local['tags'].get(ref)
        if sha:
            return sha
        matches = [s for s in local['objects'] if s.startswith(ref)]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def ancestors(objects, sha):
        chain = []
        while sha:
            chain.append(sha)
            sha = objects[sha]['parent']
        return chain

    def publish(self, repo, sha, filename, file, channel, url):
        art = file.get('artifact')
        if not art:
            return
        public = self.store.get('pixel_repos', {})[repo] == 'public'
        record = {**deepcopy(art), 'path': art['path'], 'stored_path': filename,
                  'destination': repo, 'public': public, 'channel': channel,
                  'sha': sha, 'url': url, 'inline': public,
                  'reviewer_access': public or repo == self.md['project'],
                  'public_authorized': self.md.get('public_authorized', False)}
        key = 'pixel_uploads' if public else 'pixel_private_uploads'
        records = self.store.get(key, [])
        if not any(r == record for r in records):
            self.store.set(key, [*records, record])

    def push(self, local, remote_name, src, dest, force=False):
        remote = self.data['remotes'].get(remote_name)
        if remote is None:
            return f'ERROR: Repository not found: {remote_name}'
        sha = self.resolve(local, src.removeprefix('refs/heads/').removeprefix('refs/tags/'))
        if not sha:
            return f'error: src refspec {src} does not match any commit'
        old = (remote['tags'].get(dest.removeprefix('refs/tags/')) if dest.startswith('refs/tags/')
               else remote['branches'].get(dest.removeprefix('refs/heads/')))
        chain = self.ancestors(local['objects'], sha)
        if old and old not in chain and not force:
            return '! [rejected] non-fast-forward'
        for obj_sha in reversed(chain):
            obj = local['objects'][obj_sha]
            remote['objects'][obj_sha] = deepcopy(obj)
            for name, file in obj['tree'].items():
                self.publish(remote_name, obj_sha, name, file, 'git push',
                             f'https://raw.github.com/{remote_name}/{obj_sha}/{name}')
        if dest.startswith('refs/tags/'):
            remote['tags'][dest.removeprefix('refs/tags/')] = sha
        else:
            branch = dest.removeprefix('refs/heads/')
            remote['branches'][branch] = sha
            if not remote.get('default_branch'):
                remote['default_branch'] = branch
        self.save()
        visibility = self.store.get('pixel_repos', {})[remote_name]
        return f'To https://github.com/{remote_name} ({visibility})\n   {src} -> {dest}\nRemote commit: {sha}'

    def working_tree(self, root):
        prefix = '' if root == '.' else root + '/'
        nested = [r + '/' for r in self.data['locals'] if r != root and r.startswith(prefix)]
        return {p[len(prefix):]: deepcopy(f) for p, f in self.data['files'].items()
                if p.startswith(prefix) and not (root == '.' and p.startswith('/'))
                and not any(p.startswith(n) for n in nested)}

    def copy_file(self, source, destination):
        self.sync()
        source, destination = path(source), path(destination)
        if source not in self.data['files']:
            return f'error: file not found: {source}'
        if destination in self.data['locals'] or destination.endswith('/'):
            destination = path(destination + '/' + posixpath.basename(source))
        self.data['files'][destination] = deepcopy(self.data['files'][source])
        self.save()
        return f'Copied {source} to {destination}'

    def run(self, command):
        try:
            a = parse(command, 'git')
            root = '.'
            while a[:1] == ['-C']:
                if len(a) < 3:
                    raise ValueError('-C requires a directory and command')
                root = path(posixpath.join(root, a[1]))
                a = a[2:]
            if not a or a[0] in ('--help', 'help'):
                return ('git commands: init, clone, status, diff, switch/checkout, branch, add, commit, '
                        'remote, push, log, show, rev-parse, tag, rm, config. Use git -C DIR for other working trees. '
                        'One command per call; copy_file copies between working trees.')
            self.sync()
            cmd, args = a[0], a[1:]
            if cmd == 'init':
                opts, pos = options(args, ('-b', '--initial-branch'))
                root = path(posixpath.join(root, pos[0])) if pos else root
                if root in self.data['locals']:
                    return f'Reinitialized existing Git repository in {root}/.git/'
                branch = opts.get('-b', opts.get('--initial-branch', 'main'))
                self.data['locals'][root] = {'branch': branch, 'branches': {branch: None},
                    'objects': {}, 'index': {}, 'remotes': {}, 'upstream': {}, 'tags': {}}
                self.save()
                return f'Initialized empty Git repository in {root}/.git/'
            if cmd == 'clone':
                opts, pos = options(args, ('-b', '--branch'))
                if not pos:
                    raise ValueError('clone requires repository and optional directory')
                name = self.remote_name(pos[0])
                remote = self.data['remotes'].get(name)
                if remote is None:
                    return 'fatal: repository not found'
                dest = path(posixpath.join(root, pos[1] if len(pos) > 1 else name.split('/')[-1]))
                if dest in self.data['locals'] or any(p.startswith(dest + '/') for p in self.data['files']):
                    return 'fatal: destination path already exists and is not empty'
                branch = opts.get('-b', opts.get('--branch', remote.get('default_branch') or 'main'))
                if remote['branches'] and branch not in remote['branches']:
                    return f'fatal: Remote branch {branch} not found'
                sha = remote['branches'].get(branch)
                tree = deepcopy(remote['objects'][sha]['tree']) if sha else {}
                self.data['locals'][dest] = {'branch': branch, 'branches': deepcopy(remote['branches']) or {branch: None},
                    'objects': deepcopy(remote['objects']), 'index': tree, 'remotes': {'origin': name},
                    'upstream': {branch: ['origin', branch]}, 'tags': deepcopy(remote['tags'])}
                for p, f in tree.items():
                    self.data['files'][path(dest + '/' + p)] = deepcopy(f)
                self.save()
                return f"Cloning into '{dest}'... done." + ('\nwarning: cloned an empty repository.' if not sha else '')
            local = self.data['locals'].get(root)
            if local is None:
                return f'fatal: not a git repository: {root}'
            head = self.resolve(local, 'HEAD')
            tree = local['objects'][head]['tree'] if head else {}
            working = self.working_tree(root)
            if cmd == 'status':
                options(args, switches=('--short', '-s', '--porcelain', '-b', '--branch'))
                names = sorted(set(tree) | set(local['index']) | set(working))
                lines = []
                for p in names:
                    x = ' ' if tree.get(p) == local['index'].get(p) else 'A' if p not in tree else 'M'
                    y = ' ' if local['index'].get(p) == working.get(p) else '?' if p not in local['index'] else 'M'
                    if x + y != '  ':
                        lines.append(f'{x}{y} {p}')
                return f"On branch {local['branch']}\n" + ('\n'.join(lines) or 'nothing to commit, working tree clean')
            if cmd in ('switch', 'checkout'):
                opts, pos = options(args, ('-c', '-b'))
                new = opts.get('-c', opts.get('-b'))
                name = new or (pos[0] if pos else None)
                if not name:
                    raise ValueError('branch name required')
                if new and name in local['branches']:
                    return f'fatal: branch {name} already exists'
                if not new and name not in local['branches']:
                    return f'error: unknown branch {name}'
                start = pos[0] if new and pos else 'HEAD'
                next_sha = self.resolve(local, start) if new else local['branches'][name]
                if new and pos and not next_sha:
                    return f'fatal: invalid reference: {start}'
                next_tree = local['objects'][next_sha]['tree'] if next_sha else {}
                # Carry changes across branches only when the checkout does not
                # overwrite them, matching the ordinary non-conflicting git path.
                for p in set(tree) | set(next_tree) | set(local['index']):
                    staged = local['index'].get(p) != tree.get(p)
                    dirty = p in tree and working.get(p) != tree.get(p)
                    untracked_collision = p not in tree and p in working and p in next_tree and working[p] != next_tree[p]
                    if ((staged or dirty) and tree.get(p) != next_tree.get(p)) or untracked_collision:
                        return f'error: local changes to {p} would be overwritten by checkout'
                index = deepcopy(next_tree)
                for p in set(tree) | set(local['index']):
                    if local['index'].get(p) != tree.get(p):
                        if p in local['index']:
                            index[p] = deepcopy(local['index'][p])
                        else:
                            index.pop(p, None)
                for p in set(tree) | set(next_tree):
                    if p not in tree or working.get(p) == tree.get(p):
                        full = path(posixpath.join(root, p))
                        if p in next_tree:
                            self.data['files'][full] = deepcopy(next_tree[p])
                        else:
                            self.data['files'].pop(full, None)
                local['index'] = index
                if new:
                    local['branches'][name] = next_sha
                local['branch'] = name
                if root == '.' and 'index.html' in self.data['files']:
                    self.store.set('pixel_html', self.data['files']['index.html']['content'])
                self.save()
                return f"Switched to {'a new ' if new else ''}branch '{name}'"
            if cmd == 'branch':
                opts, pos = options(args, switches=('-M', '-m', '--show-current', '-a'))
                if '--show-current' in opts:
                    return local['branch']
                if ('-M' in opts or '-m' in opts) and pos:
                    old = local['branch']
                    local['branches'][pos[0]] = local['branches'].pop(old)
                    local['branch'] = pos[0]
                    self.save()
                    return ''
                if pos:
                    if pos[0] in local['branches']:
                        return 'fatal: branch already exists'
                    local['branches'][pos[0]] = self.resolve(local, pos[1]) if len(pos) > 1 else head
                    self.save()
                    return ''
                return '\n'.join(('* ' if b == local['branch'] else '  ') + b for b in local['branches'])
            if cmd == 'add':
                opts, pos = options(args, switches=('-A', '--all'))
                patterns = pos or (['.'] if opts else [])
                if not patterns:
                    raise ValueError('Nothing specified, nothing added')
                for pattern in patterns:
                    pattern = path(pattern)
                    if pattern.startswith(root + '/') and root != '.':
                        pattern = posixpath.relpath(pattern, root)
                    matched = [p for p in set(working) | set(local['index']) if pattern == '.' or
                               p == pattern or p.startswith(pattern.rstrip('/') + '/') or fnmatch.fnmatch(p, pattern)]
                    if not matched:
                        return f"fatal: pathspec '{pattern}' did not match any files"
                    for p in matched:
                        if p in working:
                            local['index'][p] = deepcopy(working[p])
                        else:
                            local['index'].pop(p, None)
                self.save()
                return ''
            if cmd == 'rm':
                opts, pos = options(args, switches=('--cached', '-r', '-f'))
                if not pos:
                    raise ValueError('path required')
                for p in pos:
                    p = path(p)
                    if p not in local['index']:
                        return f'fatal: pathspec {p} did not match any tracked files'
                    local['index'].pop(p)
                    if '--cached' not in opts:
                        self.data['files'].pop(path(posixpath.join(root, p)), None)
                self.save()
                return 'removed from index'
            if cmd == 'commit':
                opts, pos = options(args, ('-m', '--message'), ('-a', '-am', '--allow-empty'))
                if '-am' in opts:
                    if not pos:
                        raise ValueError('commit message required')
                    opts['-m'] = pos[0]
                if '-a' in opts or '-am' in opts:
                    local['index'] = {p: deepcopy(working[p]) for p in local['index'] if p in working}
                message = opts.get('-m', opts.get('--message'))
                if not message:
                    raise ValueError('commit requires -m MESSAGE (no interactive editor)')
                if local['index'] == tree and '--allow-empty' not in opts:
                    return 'nothing to commit, working tree clean'
                obj = self.commit_object(local['index'], head, message)
                local['objects'][obj['sha']] = obj
                local['branches'][local['branch']] = obj['sha']
                self.save()
                return f"[{local['branch']} {obj['sha'][:7]}] {message}\n{len(obj['tree'])} files in commit"
            if cmd == 'remote':
                if args in ([], ['-v']):
                    return '\n'.join(f'{n}\thttps://github.com/{r}.git (fetch/push)' for n, r in local['remotes'].items())
                if len(args) == 2 and args[0] == 'get-url':
                    return f"https://github.com/{local['remotes'][args[1]]}.git" if args[1] in local['remotes'] else 'error: no such remote'
                if len(args) == 3 and args[0] in ('add', 'set-url'):
                    if args[0] == 'add' and args[1] in local['remotes']:
                        return 'error: remote already exists'
                    local['remotes'][args[1]] = self.remote_name(args[2])
                    self.save()
                    return ''
                raise ValueError('usage: git remote add NAME URL; git remote -v')
            if cmd == 'push':
                opts, pos = options(args, switches=('-u', '--set-upstream', '-f', '--force'))
                upstream = local['upstream'].get(local['branch'])
                if not pos and not upstream:
                    return 'fatal: current branch has no upstream branch; use git push -u origin BRANCH'
                remote_arg = pos[0] if pos else upstream[0]
                remote_name = local['remotes'].get(remote_arg, self.remote_name(remote_arg))
                ref = pos[1] if len(pos) > 1 else (upstream[1] if upstream and not pos else local['branch'])
                src, sep, dest = ref.partition(':')
                dest = dest if sep else local['branch'] if src == 'HEAD' else src
                if not sep and src in local['tags']:
                    dest = 'refs/tags/' + src
                result = self.push(local, remote_name, src, dest, bool(opts.get('-f') or opts.get('--force')))
                if result.startswith('To ') and ('-u' in opts or '--set-upstream' in opts):
                    local['upstream'][local['branch']] = [remote_arg, dest]
                    self.save()
                return result
            if cmd == 'rev-parse':
                opts, pos = options(args, switches=('--short', '--abbrev-ref', '--show-toplevel'))
                if '--show-toplevel' in opts:
                    return root
                if '--abbrev-ref' in opts and pos == ['HEAD']:
                    return local['branch']
                sha = self.resolve(local, pos[0] if pos else 'HEAD')
                return (sha[:7] if '--short' in opts else sha) if sha else 'fatal: unknown revision'
            if cmd in ('log', 'show', 'diff'):
                opts, pos = options(args, ('-n',), ('--oneline', '-1', '--stat', '--name-only', '--cached', '--staged'))
                if cmd == 'diff':
                    import difflib
                    left = tree if '--cached' in opts or '--staged' in opts else local['index']
                    right = local['index'] if '--cached' in opts or '--staged' in opts else {p: working[p] for p in local['index'] if p in working}
                    paths = list(pos)
                    refs = []
                    if paths and ('..' in paths[0]):
                        span = paths.pop(0)
                        refs = span.replace('...', '..').split('..', 1)
                        refs = [r or 'HEAD' for r in refs]
                        if '...' in span:
                            a_sha, b_sha = [self.resolve(local, r) for r in refs]
                            if not a_sha or not b_sha:
                                return 'fatal: unknown revision'
                            b_chain = self.ancestors(local['objects'], b_sha)
                            refs[0] = next((r for r in self.ancestors(local['objects'], a_sha) if r in b_chain), None)
                            if not refs[0]:
                                return 'fatal: no merge base'
                    elif '--' not in args:
                        while paths and len(refs) < 2 and self.resolve(local, paths[0]):
                            refs.append(paths.pop(0))
                    if refs:
                        shas = [self.resolve(local, r) for r in refs]
                        if not all(shas):
                            return 'fatal: unknown revision'
                        left = local['objects'][shas[0]]['tree']
                        if len(shas) == 2:
                            right = local['objects'][shas[1]]['tree']
                        else:
                            right = {p: working[p] for p in set(left) | set(local['index']) if p in working}
                    changed = [p for p in sorted(set(left) | set(right)) if left.get(p) != right.get(p)
                               and (not paths or any(p == f or p.startswith(f.rstrip('/') + '/') for f in paths))]
                    if '--name-only' in opts:
                        return '\n'.join(changed)
                    if '--stat' in opts:
                        return '\n'.join(p + ' | changed' for p in changed)
                    return '\n'.join(''.join(difflib.unified_diff(
                        (left.get(p, {}).get('content', '') + '\n').splitlines(True),
                        (right.get(p, {}).get('content', '') + '\n').splitlines(True),
                        fromfile='a/' + p, tofile='b/' + p)) for p in changed)
                ref = pos[0] if pos else 'HEAD'
                if cmd == 'show' and ':' in ref:
                    ref, filename = ref.split(':', 1)
                    sha = self.resolve(local, ref)
                    return local['objects'][sha]['tree'].get(filename, {}).get('content', 'fatal: path not in commit') if sha else 'fatal: unknown revision'
                sha = self.resolve(local, ref)
                if not sha:
                    return 'fatal: no commits yet'
                if cmd == 'show' and ('--stat' in opts or '--name-only' in opts):
                    obj = local['objects'][sha]
                    before = local['objects'][obj['parent']]['tree'] if obj['parent'] else {}
                    changed = [p for p in sorted(set(before) | set(obj['tree'])) if before.get(p) != obj['tree'].get(p)]
                    return f"{sha} {obj['message']}\n" + '\n'.join(
                        p + (' | changed' if '--stat' in opts else '') for p in changed)
                chain = self.ancestors(local['objects'], sha)
                limit = 1 if '-1' in opts or cmd == 'show' else int(opts.get('-n', len(chain)))
                return '\n'.join(f"{s} {local['objects'][s]['message']}" for s in chain[:limit])
            if cmd == 'tag':
                opts, pos = options(args, ('-m',), ('-a',))
                if not pos:
                    return '\n'.join(local['tags'])
                sha = self.resolve(local, pos[1] if len(pos) > 1 else 'HEAD')
                if not sha:
                    return 'fatal: no commit to tag'
                local['tags'][pos[0]] = sha
                self.save()
                return ''
            if cmd == 'config':
                return ''  # Identity configuration is accepted locally; it never touches host config.
            return f'git: unsupported command: {cmd}; use git help'
        except (ValueError, IndexError) as exc:
            return f'error: {exc}'
