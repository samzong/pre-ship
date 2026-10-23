import copy
import json
import os
from pathlib import Path
import shlex
import re
import stat
import subprocess
import tempfile

from .doctor import HOOKS, agent_registration, doctor, same_command
from .config import config_home


def validate_helper(helper):
    refusal = 'Cannot register this helper. Use uv tool install to install pre-ship, then run pre-ship install. No configuration was changed.'
    resolved = helper.resolve()
    if helper.name != 'pre-ship' or resolved.name != 'pre-ship' or not helper.is_file() or not os.access(helper, os.X_OK):
        raise ValueError(refusal)
    caches = [Path.home() / name for name in ['.cache/uv', 'Library/Caches/uv', '.cache/pipx', 'Library/Caches/pipx', '.local/pipx/.cache', 'Library/Application Support/pipx/.cache']]
    for name in ['PIPX_HOME', 'PIPX_GLOBAL_HOME']:
        if os.environ.get(name):
            caches.append(Path(os.environ[name]).expanduser() / '.cache')
    if os.environ.get('UV_CACHE_DIR'):
        caches.append(Path(os.environ['UV_CACHE_DIR']).expanduser())
    if os.environ.get('XDG_CACHE_HOME'):
        caches.extend(Path(os.environ['XDG_CACHE_HOME']).expanduser() / name for name in ['uv', 'pipx'])
    for command in [['uv', 'cache', 'dir'], ['pipx', 'environment', '--skip-maintenance', '--value', 'PIPX_VENV_CACHEDIR']]:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                caches.append(Path(result.stdout.strip()))
        except (OSError, subprocess.TimeoutExpired):
            pass
    if any(resolved.is_relative_to(cache.resolve()) for cache in caches) or any(re.fullmatch(r'archive-v[0-9]+', parent.name) for parent in resolved.parents):
        raise ValueError(refusal)


def global_path():
    if 'GIT_CONFIG_GLOBAL' in os.environ:
        return Path(os.environ['GIT_CONFIG_GLOBAL']).expanduser()
    home = Path.home() / '.gitconfig'
    xdg = config_home() / 'git/config'
    return xdg if not home.exists() and xdg.exists() else home


def file_values(path, key):
    result = subprocess.run(['git', 'config', '--file', str(path), '--no-includes', '--null', '--get-all', key], capture_output=True)
    if result.returncode not in {0, 1}:
        raise ValueError('Cannot read the global Git configuration. No configuration values were printed.')
    return result.stdout.decode().removesuffix('\0').split('\0') if result.stdout else []


def read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def host_plan(path, helper, host, install):
    before = read_bytes(path)
    try:
        data = json.loads(before) if before is not None else {}
        if not isinstance(data, dict):
            raise ValueError()
        hooks = data.get('hooks', {})
        entries = hooks.get('PreToolUse', [])
        if not isinstance(hooks, dict) or not isinstance(entries, list):
            raise ValueError()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('hooks', []), list):
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f'Cannot edit {host} hook configuration. Preserve the file and repair its JSON structure.') from None
    desired = copy.deepcopy(data)
    owned = lambda command: isinstance(command, dict) and command.get('type') == 'command' and same_command(command.get('command'), helper, 'hook')
    found = any(owned(command) for entry in entries for command in entry.get('hooks', []))
    if install:
        for entry in entries:
            for command in entry.get('hooks', []):
                if not isinstance(command, dict) or not isinstance(command.get('command'), str):
                    continue
                try:
                    words = shlex.split(command['command'])
                except ValueError:
                    continue
                if any(Path(word).name == 'pre-ship' for word in words) and not owned(command):
                    raise ValueError(f'The {host} configuration contains another gate helper. Run {shlex.quote(next(word for word in words if Path(word).name == "pre-ship"))} uninstall, then run pre-ship install and pre-ship doctor. No configuration was changed.')
        if found and agent_registration(path, helper, host) != 'registered':
            separate = []
            for entry in desired['hooks']['PreToolUse']:
                commands = entry.get('hooks', [])
                managed = [command for command in commands if owned(command)]
                if not managed:
                    continue
                if data.get('disableAllHooks') is True or entry.get('disabled') is True or any(command.get('disabled') is True or command.get('async') is True or (host == 'claude' and command.get('onFailure') != 'block') for command in managed):
                    raise ValueError(f'The {host} gate entry is disabled or differs from the required registration. Run doctor before installing.')
                other = [command for command in commands if not owned(command)]
                if other:
                    entry['hooks'] = other
                    separate.append({'matcher': 'Bash' if host == 'claude' else '.*', 'hooks': managed})
                else:
                    entry['matcher'] = 'Bash' if host == 'claude' else '.*'
            desired['hooks']['PreToolUse'].extend(separate)
        for entry in desired.get('hooks', {}).get('PreToolUse', []):
            for command in entry.get('hooks', []):
                if owned(command):
                    command['command'] = shlex.join([str(helper), 'hook'])
        if not found:
            desired.setdefault('hooks', {}).setdefault('PreToolUse', []).append({'matcher': 'Bash' if host == 'claude' else '.*', 'hooks': [{'type': 'command', 'command': shlex.join([str(helper), 'hook']), **({'onFailure': 'block'} if host == 'claude' else {})}]})
    elif found:
        remaining = []
        for entry in desired['hooks']['PreToolUse']:
            commands = entry.get('hooks', [])
            kept = [command for command in commands if not owned(command)]
            if kept == commands:
                remaining.append(entry)
            elif kept:
                entry['hooks'] = kept
                remaining.append(entry)
        if remaining:
            desired['hooks']['PreToolUse'] = remaining
        else:
            del desired['hooks']['PreToolUse']
            if not desired['hooks']:
                del desired['hooks']
    return path, before, desired if desired != data else None


def write_host(path, before, data):
    if data is None:
        return
    linked = path.is_symlink()
    destination = path.resolve()
    if read_bytes(path) != before:
        raise ValueError('Host configuration changed during installation. Retry after the other writer finishes.')
    if not data and not linked:
        path.unlink()
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=destination.name + '.', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), mode)
            json.dump(data, output, indent=2)
            output.write('\n')
        if read_bytes(path) != before:
            raise ValueError('Host configuration changed during installation. No replacement was written.')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(cwd, helper, *, install):
    helper = helper.absolute()
    if install:
        validate_helper(helper)
        from .doctor import git_version
        if not git_version()[0]:
            raise ValueError('Git 2.54.0 or later is required for configuration-based hooks. Upgrade Git, then run pre-ship install.')
    path = global_path()
    plans = []
    for name, event in HOOKS.items():
        prefix = f'hook.preship-{name}'
        commands = file_values(path, prefix + '.command')
        events = file_values(path, prefix + '.event')
        owned = [command for command in commands if same_command(command, helper, event)]
        if install:
            if any(command not in owned for command in commands) or events not in ([], [event]):
                raise ValueError(f'{prefix} already has a different registration. Run its original helper with uninstall, then run pre-ship install and pre-ship doctor. Use git config --global --get {prefix}.command to locate the helper. No configuration was changed.')
            for key, old, value in [(prefix + '.event', events, event), (prefix + '.command', commands, shlex.join([str(helper), event]))]:
                if old != [value]:
                    plans.append((key, old, value, []))
        elif owned:
            plans.append((prefix + '.command', commands, None, owned))
            if len(owned) == len(commands) and events == [event]:
                plans.append((prefix + '.event', events, None, events))
    hosts = [(Path.home() / '.claude/settings.json', 'claude'), (Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'hooks.json', 'codex')]
    host_plans = [host_plan(host_path, helper, host, install) for host_path, host in hosts]
    for key, old, value, removed in plans:
        if file_values(path, key) != old:
            raise ValueError('Global Git registration changed during installation. Run doctor before retrying.')
        path.resolve().parent.mkdir(parents=True, exist_ok=True)
        command = ['git', 'config', '--file', str(path)]
        if value is not None:
            subprocess.run([*command, '--replace-all', key, value], capture_output=True, check=True)
        else:
            for item in set(removed):
                subprocess.run([*command, '--fixed-value', '--unset-all', key, item], capture_output=True, check=True)
    if not install and plans:
        for prefix in {key.rsplit('.', 1)[0] for key, *_ in plans}:
            result = subprocess.run(['git', 'config', '--file', str(path), '--no-includes', '--get-regexp', '^' + prefix.replace('.', r'\.') + r'\.'], capture_output=True)
            if result.returncode == 1:
                subprocess.run(['git', 'config', '--file', str(path), '--remove-section', prefix], capture_output=True)
            elif result.returncode != 0:
                raise ValueError('Cannot inspect the remaining Git hook keys.')
        if not path.is_symlink() and path.exists() and not path.read_bytes().strip():
            path.unlink()
    for host_path, before, desired in host_plans:
        write_host(host_path, before, desired)
    if install:
        return doctor(cwd, helper)
    print(json.dumps({'action': 'uninstall', 'scope': 'Only registrations for this exact helper were removed. Other configuration was preserved.'}))
    return 0
