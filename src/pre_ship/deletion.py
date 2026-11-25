import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .config import load_config
from .events import GateError


def deletion_target(cwd, git, url, ref, oid):
    if not ref.startswith('refs/heads/') or ref.removeprefix('refs/heads/') in load_config()['push']['protected_branches']:
        raise GateError('push_policy', 'Branch deletion is blocked for protected branches and non-branch refs.')
    git(cwd, 'check-ref-format', ref)
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid) or set(oid) == {'0'}:
        raise ValueError('Branch deletion requires a nonzero expected object ID.')
    if not url or git(cwd, 'ls-remote', '--get-url', '--', url).decode().removesuffix('\n') != url:
        raise ValueError('URL rewriting is active for the deletion target. Branch deletion is blocked.')
    root = Path(os.fsdecode(git(cwd, 'rev-parse', '--show-toplevel')).strip()).resolve()
    return {'root': str(root), 'mode': 'deletion', 'remote': hashlib.sha256(url.encode()).hexdigest(), 'ref': ref, 'oid': oid}


def receipt_path(root):
    digest = hashlib.sha256(root.encode()).hexdigest()[:16]
    return Path.home() / '.cache' / 'pre-ship' / f'{digest}-deletion.json'


def record_deletion(cwd, git, remote, ref, oid):
    urls = git(cwd, 'remote', 'get-url', '--push', '--all', remote).decode().splitlines()
    if len(urls) != 1:
        raise ValueError('Branch deletion requires exactly one push URL for the named remote.')
    value = deletion_target(cwd, git, urls[0], ref, oid)
    path = receipt_path(value['root'])
    path.unlink(missing_ok=True)
    rows = [line.split() for line in git(cwd, 'ls-remote', '--symref', '--', urls[0], 'HEAD', ref).decode().splitlines()]
    if any(row == ['ref:', ref, 'HEAD'] for row in rows):
        raise GateError('push_policy', 'Deletion of the remote default branch is blocked.')
    if [row for row in rows if len(row) == 2 and row[1] == ref] != [[oid, ref]]:
        raise GateError('push_policy', 'The remote branch does not match the expected object ID. Review the current target.')
    value.update(version=1, verdict='PASS', created_at=time.time())
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def check_deletion(cwd, git, local_ref, local_oid, remote_ref, remote_oid, arguments):
    if local_ref != '(delete)' or len(local_oid) != len(remote_oid) or len(arguments) != 2:
        raise ValueError('Branch deletion requires a valid pre-push deletion target and remote URL.')
    current = deletion_target(cwd, git, arguments[1], remote_ref, remote_oid)
    try:
        value = json.loads(receipt_path(current['root']).read_text())
        valid = value['version'] == 1 and value['verdict'] == 'PASS' and 0 <= time.time() - value['created_at'] <= 300
        valid = valid and all(value.get(key) == item for key, item in current.items())
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise GateError('receipt', 'No current deletion PASS receipt for this exact remote branch and object ID. '
            'After an external review passes, run pre-ship record-deletion with the reviewed remote, ref, and expected object ID. '
            'Do not use --no-verify.')
    rows = [line.split() for line in git(cwd, 'ls-remote', '--symref', '--', arguments[1], 'HEAD').decode().splitlines()]
    if any(row == ['ref:', remote_ref, 'HEAD'] for row in rows):
        raise GateError('push_policy', 'Deletion of the remote default branch is blocked.')
