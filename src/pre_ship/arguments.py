import argparse

HOOK_ACTIONS = ('hook', 'prepare-commit-msg', 'pre-applypatch', 'commit-msg', 'pre-push')
MODES = ('staged', 'committed')


def legacy_parser(version):
    parser = argparse.ArgumentParser(prog='pre-ship')
    parser.set_defaults(json=False)
    parser.add_argument('action', choices=HOOK_ACTIONS)
    parser.add_argument('values', nargs='*')
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--verdict', choices=('PASS', 'EXEMPTED'), default='PASS')
    parser.add_argument('--reply')
    parser.add_argument('--reason')
    parser.add_argument('--version', action='version', version=f'pre-ship {version}')
    return parser


def parse_arguments(argv, version):
    argv = argv or ['--help']
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    for option in ('--cwd', '--mode', '--verdict', '--reply', '--reason'):
        probe.add_argument(option)
    _, remaining = probe.parse_known_args(argv)
    if remaining and remaining[0] in HOOK_ACTIONS:
        return legacy_parser(version).parse_args(argv)
    parser = argparse.ArgumentParser(
        prog='pre-ship',
        usage='%(prog)s [-h] [--version] <command> ...',
        description='Review changes before commits and pushes. Inspect and manage the gate installation.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Review and receipts:\n  review       Run the configured reviewer and save a receipt.\n  record       Save a receipt after an external review passes.\n\nDiagnostics:\n  doctor       Inspect registration and show repair directions.\n  events       Summarize retained gate observations.\n\nInstallation:\n  install      Register Git and agent hooks for this helper.\n  uninstall    Remove registrations for this helper.\n\nStart here: pre-ship doctor\nCommand help: pre-ship <command> --help',
    )
    parser.set_defaults(cwd='.', verdict='PASS', reply=None, reason=None, json=False)
    parser.add_argument('--version', action='version', version=f'pre-ship {version}')
    parser.add_argument('--cwd', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(prog=parser.prog, dest='action', metavar='<command>', required=True, help=argparse.SUPPRESS)
    descriptions = {
        'review': ('Run the configured reviewer and save a receipt for the unchanged target.', 'pre-ship review staged\n  pre-ship review committed --json'),
        'record': ('Save a receipt only after an external review passes for this exact target.', 'pre-ship record staged\n  pre-ship record committed --json'),
        'doctor': ('Inspect registration and show repair directions without changing configuration.', 'pre-ship doctor\n  pre-ship doctor --cwd /path/to/repository --json'),
        'events': ('Summarize retained gate observations by result and reason.', 'pre-ship events\n  pre-ship events --json'),
        'install': ('Register Git and agent hooks for this installed helper.', 'pre-ship install\n  pre-ship install --json'),
        'uninstall': ('Remove only the registrations owned by this helper.', 'pre-ship uninstall\n  pre-ship uninstall --json'),
    }
    for action, (description, example) in descriptions.items():
        command = commands.add_parser(action, description=description, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=f'Examples:\n  {example}')
        command.add_argument('--cwd', default=argparse.SUPPRESS, metavar='PATH', help='Repository directory (default: current directory).')
        command.add_argument('--json', action='store_true', help='Write the result as JSON. Errors stay on stderr.')
        if action in {'review', 'record'}:
            command.add_argument('target_mode', nargs='?', choices=MODES, metavar='MODE', help='staged or committed (default: staged).')
            command.add_argument('--mode', choices=MODES, help='Alternative to positional MODE.')
        if action == 'record':
            command.add_argument('--verdict', choices=('PASS', 'EXEMPTED'), default='PASS', help='Receipt verdict (default: PASS).')
            command.add_argument('--reply', help='Configured owner reply, required for EXEMPTED.')
            command.add_argument('--reason', help='Missing evidence for EXEMPTED, without secrets (at most 2,000 characters).')
    args = parser.parse_args(argv)
    if args.action in {'review', 'record'} and args.mode and args.target_mode and args.mode != args.target_mode:
        commands.choices[args.action].error('MODE and --mode must select the same target')
    return args
