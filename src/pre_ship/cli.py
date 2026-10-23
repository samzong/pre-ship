import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time

from . import __version__
from .arguments import parse_arguments
from .config import load_config
from .output import print_events, print_receipt
from .agent import guard
from .events import GateError, log_event, event_summary
from .git import agent_process, commit_message, prepare_commit, push_refs

AGE = 300


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True).stdout


def target(cwd, mode):
    if mode not in {'staged', 'committed'}:
        raise ValueError('The review mode must be staged or committed.')
    root = Path(os.fsdecode(git(cwd, 'rev-parse', '--show-toplevel')).strip()).resolve()
    resolved = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'], capture_output=True)
    head = resolved.stdout.decode().strip() if resolved.returncode == 0 else 'UNBORN'
    diff_options = ['--binary', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none', '--no-relative']
    if mode == 'staged':
        tree = git(root, 'write-tree').decode().strip()
        args = ['diff', '--cached', *diff_options] + ([] if head == 'UNBORN' else ['HEAD'])
    elif head == 'UNBORN':
        raise ValueError('A committed review requires an existing HEAD commit.')
    else:
        tree = git(root, 'rev-parse', 'HEAD^{tree}').decode().strip()
        parent = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD~1'], capture_output=True)
        args = ['diff', *diff_options, 'HEAD~1', 'HEAD'] if parent.returncode == 0 else ['diff-tree', '--root', '--no-commit-id', '-p', *diff_options, 'HEAD']
    fingerprint = hashlib.sha256(git(root, *args)).hexdigest()
    return {'root': str(root), 'head': head, 'tree': tree, 'mode': mode, 'fingerprint': fingerprint}


def receipt_path(root):
    digest = hashlib.sha256(root.encode()).hexdigest()[:16]
    return Path.home() / '.cache' / 'pre-ship' / f'{digest}.json'


def review(cwd, mode):
    command = load_config()['review']['command']
    if not command:
        raise GateError('review', 'No review command is configured. Set review.command in the global pre-ship configuration. '
            f'For an external review workflow, run pre-ship record {mode} after that review passes.')
    current = target(cwd, mode)
    path = receipt_path(current['root'])
    path.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment.update({f'PRE_SHIP_{key.upper()}': value for key, value in current.items()})
    accepted = False
    handlers = {}

    def interrupted(signum, frame):
        raise GateError('review', f'The review was interrupted by {signal.Signals(signum).name}. No receipt was recorded.')

    try:
        for signum in [signal.SIGTERM, signal.SIGHUP]:
            if signal.getsignal(signum) != signal.SIG_IGN:
                handlers[signum] = signal.signal(signum, interrupted)
        result = subprocess.run(command, cwd=current['root'], env=environment, input=json.dumps(current).encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise GateError('review', f'The review command exited {result.returncode}. No receipt was recorded. Run the reviewer directly for diagnostics.')
        recorded = record(cwd, mode, expected=current)
        accepted = True
        return recorded
    finally:
        try:
            if not accepted:
                path.unlink(missing_ok=True)
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def record(cwd, mode, *, verdict='PASS', reply=None, reason=None, expected=None):
    if verdict not in {'PASS', 'EXEMPTED'}:
        raise ValueError('Record only PASS or EXEMPTED.')
    if verdict == 'EXEMPTED':
        if reply != load_config()['exemption_reply'] or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('EXEMPTED requires the configured --reply and a nonempty --reason of at most 2000 characters. Do not include secrets.')
    elif reply is not None or reason is not None:
        raise ValueError('Exemption fields require --verdict EXEMPTED.')
    value = target(cwd, mode)
    if expected is not None and value != expected:
        raise GateError('review', 'The review target changed. Review the new target before recording a receipt.')
    value.update(version=2, verdict=verdict, created_at=time.time())
    if verdict == 'EXEMPTED':
        value.update(reply=reply, reason=reason.strip())
    path = receipt_path(value['root'])
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


def check(cwd, mode, *, freshness=True):
    current = target(cwd, mode)
    try:
        value = json.loads(receipt_path(current['root']).read_text())
        valid = value['version'] == 2 and value['verdict'] in {'PASS', 'EXEMPTED'}
        if value.get('verdict') == 'EXEMPTED':
            valid = valid and value.get('reply') == load_config()['exemption_reply'] and isinstance(value.get('reason'), str) and 0 < len(value['reason'].strip()) <= 2000
        if freshness:
            age = time.time() - value['created_at']
            valid = valid and 0 <= age <= AGE
        valid = valid and all(value.get(key) == item for key, item in current.items())
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        root = shlex.quote(current['root'])
        if load_config()['review']['command']:
            guidance = f'Run pre-ship review {mode} --cwd {root}.'
        else:
            guidance = f'No review command is configured. After an external review passes for this exact target, run pre-ship record {mode} --cwd {root}.'
        raise GateError('receipt',
            f'No current {mode} PASS receipt or EXEMPTED receipt for {root}. '
            f'{guidance} '
            'Do not use --no-verify.'
        )
    return dict(current, verdict=value['verdict'])


def hook(context):
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise ValueError('The hook input must be a JSON object.')
    context['root'] = repository_root(Path(data.get('cwd') or os.getcwd()))
    tool = data.get('tool_input') or {}
    if not isinstance(tool, dict):
        return
    command = tool.get('command') or tool.get('cmd') or ''
    if not isinstance(command, str) or not command:
        return
    if command.lstrip().startswith('*** Begin Patch\n'):
        if command.rstrip().endswith('\n*** End Patch'):
            return
        raise ValueError('Separate complete patch input from shell commands before running the Git bypass guard.')
    cwd = Path(tool.get('workdir') or tool.get('cwd') or data.get('cwd') or os.getcwd()).expanduser().resolve()
    context['root'] = repository_root(cwd)
    guard(command, cwd)


def repository_root(cwd):
    try:
        return os.fsdecode(git(cwd, 'rev-parse', '--show-toplevel')).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main():
    args = parse_arguments(sys.argv[1:], __version__)
    if args.action == 'hook' and sys.stdin.isatty():
        parse_arguments(['--help'], __version__)
    enforced = args.action in {'hook', 'prepare-commit-msg', 'pre-applypatch', 'commit-msg', 'pre-push'}
    context = {'root': None, 'result': 'block', 'reason': 'interrupted'}
    exempted = False

    def checked(cwd, mode, **options):
        nonlocal exempted
        if not agent_process():
            return target(cwd, mode)
        current = check(cwd, mode, **options)
        if current['verdict'] == 'EXEMPTED':
            if not exempted:
                print('Pre-Ship: UNVERIFIED — EXEMPTED', file=sys.stderr)
            exempted = True
        return current

    try:
        if args.action == 'pre-push' and not agent_process():
            context.update(result='allow', reason='human')
            return 0
        cwd = Path(args.cwd).expanduser().resolve()
        if enforced and args.action != 'hook':
            context['root'] = repository_root(cwd)
        if args.action != 'record' and (args.verdict != 'PASS' or args.reply is not None or args.reason is not None):
            raise ValueError('Exemption fields are valid only for record.')
        if args.action == 'events':
            print_events(event_summary(), as_json=args.json)
        elif args.action == 'doctor':
            from .doctor import doctor
            return doctor(cwd, Path(sys.argv[0]).absolute(), as_json=args.json)
        elif args.action in {'install', 'uninstall'}:
            from .install import configure
            return configure(cwd, Path(sys.argv[0]).absolute(), install=args.action == 'install', as_json=args.json)
        elif args.action in {'review', 'record'}:
            mode = args.mode or args.target_mode or 'staged'
            if args.action == 'review':
                path = review(cwd, mode)
            else:
                path = record(cwd, mode, verdict=args.verdict, reply=args.reply, reason=args.reason)
            print_receipt(path, as_json=args.json)
            if args.verdict == 'EXEMPTED' and args.json:
                print('Pre-Ship: UNVERIFIED — EXEMPTED', file=sys.stderr)
        elif args.action == 'hook':
            hook(context)
        elif args.action == 'prepare-commit-msg':
            prepare_commit(cwd, git, checked, args.values)
        elif args.action == 'pre-applypatch':
            message = os.fsdecode(git(cwd, 'rev-parse', '--git-path', 'rebase-apply/final-commit')).strip()
            prepare_commit(cwd, git, checked, [message])
        elif args.action == 'commit-msg':
            if len(args.values) != 1:
                raise ValueError('commit-msg requires the Git message path.')
            checked(cwd, 'staged', freshness=False)
            commit_message(cwd, git, args.values[0])
        else:
            push_refs(cwd, git, checked, sys.stdin.read(), args.values)
        context.update(result='exempt' if exempted else 'allow', reason='receipt_exempted' if exempted else ('guard' if args.action == 'hook' else 'checks'))
        return 0
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError) as error:
        category = getattr(error, 'category', {'hook': 'guard', 'pre-push': 'push_policy'}.get(args.action, 'git_target'))
        context.update(result='block', reason=category)
        reason = str(error) if not isinstance(error, subprocess.CalledProcessError) else 'Cannot resolve the Git target or index.'
        if isinstance(error, subprocess.CalledProcessError) and args.action in {'install', 'uninstall'}:
            from .install import global_path
            reason = f'Cannot update Git hook configuration at {global_path()}. Registration may be partial. Check permissions and lock files, then rerun {args.action} and doctor.'
        if args.action == 'hook':
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}))
            return 0
        print(f'pre-ship: {reason}', file=sys.stderr)
        return 1
    finally:
        if enforced:
            log_event(context['root'], 'PreToolUse' if args.action == 'hook' else args.action, context['result'], context['reason'])


if __name__ == '__main__':
    raise SystemExit(main())
