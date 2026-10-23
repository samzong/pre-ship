import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .config import DEFAULTS, load_config

from .events import GateError

TOKEN_LIMIT = 5000
LOCKFILES = {'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'bun.lockb', 'packages.lock.json', 'Package.resolved', 'go.sum', 'gradle.lockfile', '.terraform.lock.hcl'}


def agent_process():
    try:
        markers = load_config()['agent_markers']
    except ValueError:
        if any(os.environ.get(name) for name in DEFAULTS['agent_markers']):
            raise
        return False
    return any(os.environ.get(name) for name in markers)


def split_boundary(path):
    name = path.name.lower()
    if 'test' in path.parts or 'tests' in path.parts or name.startswith('test_') or re.search(r'[._](test|spec)\.', name):
        return 'Split tests by the unit under test.'
    if path.suffix.lower() in {'.md', '.mdx', '.rst', '.txt', '.adoc'}:
        return 'Split documents by section.'
    if path.suffix.lower() in {'.jsx', '.tsx', '.vue', '.svelte', '.swift'}:
        return 'Split components by child component or view.'
    return 'Split code by responsibility and its public interface.'


def size_gate(cwd, git):
    entries = git(cwd, 'diff', '--cached', '--raw', '-z', '--find-renames', '--no-abbrev', '--no-ext-diff', '--no-relative').split(b'\0')
    changed = []
    i = 0
    while i < len(entries) - 1:
        metadata = entries[i].split()
        old_mode, new_mode, old_oid, new_oid, status = metadata
        path = Path(entries[i + 2 if status.startswith(b'R') else i + 1].decode(errors='surrogateescape'))
        i += 3 if status.startswith(b'R') else 2
        if status == b'D' or new_mode == b'160000':
            continue
        changed.append((path, old_oid, new_oid))
    if not changed:
        return
    names = b'\0'.join(str(path).encode(errors='surrogateescape') for path, _, _ in changed) + b'\0'
    empty_tree = git(cwd, 'hash-object', '-t', 'tree', '/dev/null').decode().strip()
    result = subprocess.run(['git', '-C', str(cwd), '-c', 'core.attributesFile=', 'check-attr', f'--source={empty_tree}', '-z', '--stdin', 'pre-ship-large-file'], input=names, capture_output=True, check=True)
    attributes = result.stdout.split(b'\0')
    exclusions = {attributes[i].decode(errors='surrogateescape') for i in range(0, len(attributes) - 1, 3) if attributes[i + 2] == b'set'}
    failures = []
    for path, old_oid, new_oid in changed:
        if path.suffix == '.lock' or path.name in LOCKFILES or str(path) in exclusions:
            continue
        size = int(git(cwd, 'cat-file', '-s', new_oid.decode()))
        old_size = 0 if set(old_oid) == {48} else int(git(cwd, 'cat-file', '-s', old_oid.decode()))
        if size > TOKEN_LIMIT * 4 and size > old_size:
            failures.append(f'{str(path)!r}: about {(size + 3) // 4:,} tokens (bytes/4), limit {TOKEN_LIMIT:,}. {split_boundary(path)}')
    if failures:
        local = git(cwd, 'rev-parse', '--path-format=absolute', '--git-path', 'info/attributes').decode().strip()
        raise GateError('size', '\n'.join(failures) + f'\nDo not use --no-verify. For a local exception, add "<path> pre-ship-large-file" to {local}. Never commit it to .gitattributes; the gate ignores repository attributes.')


def secrets_gate(cwd):
    mode = load_config()['gitleaks']['mode']
    if mode == 'off':
        return
    try:
        result = subprocess.run(['gitleaks', 'git', '--pre-commit', '--staged', '--redact=100', '--no-banner', '--no-color', '--log-level', 'error', '--report-format', 'json', '--report-path', '-'], cwd=cwd, capture_output=True)
    except FileNotFoundError as error:
        if mode == 'optional':
            print('pre-ship: gitleaks is not installed. Secret scanning was skipped. Install gitleaks or set gitleaks.mode to required.', file=sys.stderr)
            return
        raise GateError('secrets', 'Cannot run gitleaks. Install gitleaks or change gitleaks.mode in the global configuration.') from error
    except OSError as error:
        raise GateError('secrets', 'Cannot run gitleaks. Repair the executable and retry.') from error
    if result.returncode:
        try:
            findings = json.loads(result.stdout)
        except (ValueError, TypeError):
            findings = []
        locations = [f'{item.get("File", "unknown")!r}:{item.get("StartLine", "?")} ({item.get("RuleID", "unknown")})' for item in findings if isinstance(item, dict)] if isinstance(findings, list) else []
        detail = '\n'.join(locations) or f'gitleaks exited {result.returncode}. Make sure that gitleaks is installed and its configuration is valid.'
        raise GateError('secrets', f'Staged secret scan failed.\n{detail}\nRemove secrets from the index. Do not use --no-verify.')


def commit_message(cwd, git, message):
    raw = Path(message).read_bytes()
    parsed = subprocess.run(['git', '-C', str(cwd), 'interpret-trailers', '--parse'], input=raw, check=True, capture_output=True).stdout.decode(errors='replace')
    if not any(re.fullmatch(r'Signed-off-by:\s+.+\s+<[^<>\s@]+@[^<>\s]+>', line, re.I) for line in parsed.splitlines()):
        raise GateError('signoff', 'The commit message requires a Signed-off-by trailer. Use git commit -s. Do not use --no-verify.')


def whitespace_gate(cwd):
    command = ['git', '-C', str(cwd)]
    merge_path = subprocess.run([*command, 'rev-parse', '--git-path', 'MERGE_HEAD'], capture_output=True, check=True).stdout.decode(errors='surrogateescape').strip()
    merge_head = Path(cwd) / merge_path
    parents = [[]]
    if merge_head.exists():
        heads = merge_head.read_text().splitlines()
        if not heads or any(not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head) for head in heads):
            raise GateError('whitespace', 'Cannot resolve merge parents for the staged whitespace check.')
        parents = [['HEAD'], *[[head] for head in heads]]
    common = None
    for parent in parents:
        result = subprocess.run([*command, 'diff', '--cached', '--check', '--no-ext-diff', '--no-textconv', '--no-color', *parent], capture_output=True, env={**os.environ, 'LC_ALL': 'C'})
        if not parent and result.returncode:
            raise GateError('whitespace', 'Staged whitespace check failed. Run git diff --cached --check and resolve the reported errors.')
        locations = set()
        lines = iter(result.stdout.rstrip(b'\n').split(b'\n') if result.stdout else [])
        for line in lines:
            diagnostic = re.fullmatch(rb'(.+):([0-9]+): (.+)', line)
            if diagnostic is None:
                raise GateError('whitespace', 'Cannot read the staged whitespace check results.')
            locations.add(diagnostic.group(1, 2))
            if diagnostic.group(3) not in {b'new blank line at EOF.', b'leftover conflict marker'}:
                if not next(lines, b'').startswith(b'+'):
                    raise GateError('whitespace', 'Cannot read the staged whitespace check results.')
        if result.returncode not in {0, 2} or (result.returncode and not locations):
            raise GateError('whitespace', 'Cannot complete the staged whitespace check.')
        common = locations if common is None else common & locations
    if common:
        raise GateError('whitespace', 'Staged whitespace check failed. Run git diff --cached --check and resolve the reported errors.')


def prepare_commit(cwd, git, check, arguments):
    if not arguments:
        raise ValueError('prepare-commit-msg requires the Git message path.')
    current = check(cwd, 'staged')
    whitespace_gate(cwd)
    size_gate(cwd, git)
    secrets_gate(cwd)
    commit_message(cwd, git, arguments[0])
    if check(cwd, 'staged') != current:
        raise ValueError('The index changed during the hook. Review the new target before committing.')


def push_release_tag(cwd, git, local_oid, remote_ref, remote_oid, arguments):
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', remote_oid) or set(remote_oid) != {'0'}:
        raise ValueError('Only new release tags are allowed. Moving or overwriting an existing tag is blocked.')
    number = r'(?:0|[1-9][0-9]*)'
    prerelease = rf'(?:{number}|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
    if not re.fullmatch(rf'v{number}\.{number}\.{number}(?:-{prerelease}(?:\.{prerelease})*)?', remote_ref.removeprefix('refs/tags/')):
        raise ValueError('Release tag names must use SemVer vMAJOR.MINOR.PATCH[-PRERELEASE], without build metadata.')
    try:
        annotated = git(cwd, '--no-replace-objects', 'cat-file', '-t', local_oid).strip() == b'tag'
    except subprocess.CalledProcessError as error:
        raise ValueError('Release tags require a local annotated tag object. Lightweight tags are blocked.') from error
    if not annotated:
        raise ValueError('Release tags require an annotated tag object. Lightweight tags are blocked.')
    try:
        commit = git(cwd, '--no-replace-objects', 'rev-parse', '--verify', f'{local_oid}^{{commit}}').decode().strip()
    except subprocess.CalledProcessError as error:
        raise ValueError('The annotated release tag must resolve to a commit.') from error
    if len(arguments) != 2 or not arguments[1]:
        raise ValueError('Release tag pushes require the remote name and URL from the pre-push hook.')
    try:
        resolved_url = git(cwd, 'ls-remote', '--get-url', '--', arguments[1]).decode().removesuffix('\n')
    except subprocess.CalledProcessError as error:
        raise ValueError('Cannot resolve the pre-push URL. Release tag ancestry cannot be proven.') from error
    if resolved_url != arguments[1]:
        raise ValueError('URL rewriting is active for the push target. Release tag ancestry cannot be proven.')
    for branch in ['main', 'master']:
        ref = f'refs/heads/{branch}'
        try:
            advertised = [line.split() for line in git(cwd, 'ls-remote', '--', arguments[1], ref).decode().splitlines()]
        except subprocess.CalledProcessError as error:
            raise ValueError('Cannot read the remote default branch. Make sure that remote access works, then retry.') from error
        if any(len(row) != 2 or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', row[0]) or not row[1].startswith('refs/') for row in advertised):
            raise ValueError('The remote default branch returned an invalid tip. Release tag push is blocked.')
        tips = [oid for oid, name in advertised if name == ref]
        if len(tips) > 1:
            raise ValueError(f'The remote returned duplicate tips for {ref}. Release tag push is blocked.')
        if not tips:
            continue
        tip = tips[0]
        try:
            present = git(cwd, '--no-replace-objects', 'cat-file', '-t', tip).strip() == b'commit'
        except subprocess.CalledProcessError:
            present = False
        if not present:
            raise ValueError(f'The remote {branch} tip is not a local commit. Fetch the remote before pushing release tags.')
        try:
            git(cwd, '--no-replace-objects', 'merge-base', '--is-ancestor', commit, tip)
        except subprocess.CalledProcessError as error:
            if error.returncode == 1:
                raise ValueError(f'The release tag commit must be an ancestor of the current remote {branch} tip.') from error
            raise ValueError('Cannot prove release tag ancestry. Fetch the remote and retry.') from error
        return
    raise ValueError('The remote has no main or master branch. Release tag push is blocked.')


def contains(cwd, ancestor, descendant):
    return subprocess.run(['git', '-C', str(cwd), '--no-replace-objects', 'merge-base', '--is-ancestor', ancestor, descendant], capture_output=True).returncode == 0


def published_elsewhere(cwd, git, local_oid, remote_ref, remote_oid, arguments):
    if set(remote_oid) != {'0'} and not contains(cwd, remote_oid, local_oid):
        return False
    branch = remote_ref.removeprefix('refs/heads/')
    for remote in git(cwd, 'remote').decode(errors='surrogateescape').split():
        if arguments and remote == arguments[0]:
            continue
        tip = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{branch}^{{commit}}'], capture_output=True)
        if tip.returncode == 0 and contains(cwd, local_oid, tip.stdout.decode().strip()):
            return True
    return False


def push_refs(cwd, git, check, data, arguments=()):
    lines = [line.split() for line in data.splitlines() if line.strip()]
    if not lines:
        return
    if len(lines) != 1 or len(lines[0]) != 4:
        raise ValueError('Push exactly one ref. Multiple refs and malformed ref updates are blocked. Do not use --no-verify.')
    local_ref, local_oid, remote_ref, remote_oid = lines[0]
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', local_oid) or set(local_oid) == {'0'}:
        raise ValueError('Ref deletion or an invalid local object ID is blocked. Do not use --no-verify.')
    if remote_ref.startswith('refs/tags/'):
        push_release_tag(cwd, git, local_oid, remote_ref, remote_oid, arguments)
        return
    if remote_ref.startswith('refs/heads/') and published_elsewhere(cwd, git, local_oid, remote_ref, remote_oid, arguments):
        return
    if not remote_ref.startswith('refs/heads/') or (not load_config()['push']['allow_protected'] and remote_ref.removeprefix('refs/heads/') in load_config()['push']['protected_branches']):
        raise ValueError(f'Direct push to {remote_ref!r} is blocked. Push a feature branch and open a PR. Do not use --no-verify.')
    try:
        branch = git(cwd, 'symbolic-ref', '--quiet', 'HEAD').decode().strip()
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
        raise ValueError('Push from detached HEAD is blocked. Switch to a feature branch and retry. Do not use --no-verify.') from error
    head = git(cwd, 'rev-parse', 'HEAD').decode().strip()
    if local_ref not in {'HEAD', branch} or local_oid != head:
        raise ValueError('The push must use the current branch at the reviewed HEAD. Do not use --no-verify.')
    check(cwd, 'committed')
