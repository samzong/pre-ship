import json
import os
from pathlib import Path
import shlex
import subprocess
import re
import shutil

from .config import config_path, load_config
from .output import print_doctor

HOOKS = {'prepare': 'prepare-commit-msg', 'message': 'commit-msg', 'apply': 'pre-applypatch', 'push': 'pre-push'}


def same_command(command, helper, action):
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
        return len(words) == 2 and Path(words[0]).expanduser().resolve() == helper.resolve() and words[1] == action
    except (TypeError, ValueError, OSError):
        return False


def config_values(cwd, key):
    result = subprocess.run(['git', '-C', str(cwd), 'config', '--null', '--get-all', key], capture_output=True)
    if result.returncode not in {0, 1}:
        raise ValueError('Cannot read Git hook configuration.')
    return result.stdout.decode().removesuffix('\0').split('\0') if result.stdout else []


def config_enabled(cwd, key):
    result = subprocess.run(['git', '-C', str(cwd), 'config', '--type=bool', '--get', key], capture_output=True, text=True)
    return result.returncode == 1 or (result.returncode == 0 and result.stdout.strip() == 'true')


def native_registered(cwd, name, event):
    result = subprocess.run(['git', '-C', str(cwd), 'hook', 'list', '-z', event], capture_output=True)
    return result.returncode == 0 and name.encode() in result.stdout.split(b'\0')


def agent_registration(path, helper, host):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return 'missing'
    except (OSError, ValueError):
        return 'unreadable'
    if not isinstance(data, dict):
        return 'invalid'
    if data.get('disableAllHooks') is True:
        return 'disabled'
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        return 'missing'
    entries = hooks.get('PreToolUse')
    if not isinstance(entries, list):
        return 'missing'
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('disabled') is True:
            continue
        matcher = entry.get('matcher')
        if matcher is not None and not isinstance(matcher, str):
            continue
        supported = {'Bash', '*', '.*', ''} if host == 'claude' else {'.*', ''}
        if matcher not in supported and matcher is not None:
            continue
        commands = entry.get('hooks')
        if not isinstance(commands, list):
            continue
        for command in commands:
            if not isinstance(command, dict) or command.get('type') != 'command' or command.get('disabled') is True or command.get('async') is True:
                continue
            if same_command(command.get('command'), helper, 'hook'):
                if host == 'claude' and command.get('onFailure') != 'block':
                    continue
                return 'registered'
    return 'missing_or_different'


def git_version():
    result = subprocess.run(['git', '--version'], capture_output=True, text=True)
    match = re.search(r'git version (\d+)\.(\d+)\.(\d+)', result.stdout)
    supported = result.returncode == 0 and match is not None and tuple(map(int, match.groups())) >= (2, 54, 0)
    return supported, result.stdout.strip() if match else 'unknown'


def doctor(cwd, helper, *, as_json=False):
    configuration = load_config()
    supported, version = git_version()
    rows = [{'component': 'git', 'status': 'supported' if supported else 'unsupported', 'version': version, 'minimum': '2.54.0'}]
    for name, event in HOOKS.items():
        key = f'hook.preship-{name}'
        events = config_values(cwd, key + '.event')
        commands = config_values(cwd, key + '.command')
        active = config_enabled(cwd, key + '.enabled') and config_enabled(cwd, f'hook.{event}.enabled')
        listed = native_registered(cwd, f'preship-{name}', event)
        status = 'registered' if events == [event] and len(commands) == 1 and same_command(commands[0], helper, event) and active and listed and os.access(helper, os.X_OK) else 'missing_or_different'
        row = {'component': key, 'status': status}
        if status != 'registered':
            row['repair'] = [f'git config --global --replace-all {key}.event {event}', f'git config --global --replace-all {key}.command {shlex.quote(shlex.join([str(helper), event]))}', f'git config --global {key}.enabled true', f'git config --global hook.{event}.enabled true']
            row['note'] = 'Remove conflicting repository or worktree values if needed. Enabling an event restores every hook for that event.'
        rows.append(row)
    for host, path in [('claude', Path.home() / '.claude' / 'settings.json'), ('codex', Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'hooks.json')]:
        status = agent_registration(path, helper, host)
        row = {'component': host, 'status': status, 'path': str(path)}
        if status != 'registered':
            row['repair'] = 'pre-ship install'
            row['note'] = 'Run install for a missing entry. For a disabled or conflicting entry, edit this file and preserve unrelated hooks. Enable hooks if disabled.'
            row['entry'] = {'matcher': 'Bash' if host == 'claude' else '.*', 'hooks': [{'type': 'command', 'command': shlex.join([str(helper), 'hook']), **({'onFailure': 'block'} if host == 'claude' else {})}]}
        rows.append(row)
    registration_ok = all(row['status'] in {'registered', 'supported'} for row in rows)
    mode = configuration['gitleaks']['mode']
    available = shutil.which('gitleaks') is not None
    rows.append({'component': 'gitleaks', 'status': 'disabled' if mode == 'off' else ('available' if available else 'missing'), 'mode': mode, 'note': 'Optional mode skips scanning with a warning when gitleaks is missing. Required mode blocks commits when gitleaks is missing.'})
    rows.append({'component': 'review', 'status': 'configured' if configuration['review']['command'] else 'not_configured', 'note': 'pre-ship review runs review.command and fails when it is empty. pre-ship record attests that an external review passed for the exact target; it does not run a review.'})
    print_doctor({'configuration': str(config_path()), 'checks': rows, 'scope': 'Static registration only. Codex trust and native host execution require a separate smoke check.'}, as_json=as_json)
    return 0 if registration_ok and not (mode == 'required' and not available) else 1
