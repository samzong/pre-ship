import json
import os
import sys


def print_receipt(path, *, as_json):
    value = json.loads(path.read_text())
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
        return
    status = 'PASS' if value['verdict'] == 'PASS' else 'UNVERIFIED — EXEMPTED'
    print(f'Pre-Ship: {status} ({value["mode"]})')
    print(f'Repository: {value["root"]}')
    print(f'Receipt: {path}')


def print_events(value, *, as_json):
    if as_json:
        print(json.dumps(value))
        return
    print('Retained gate observations')
    if value['counts']:
        print(f'{"RESULT":<8} {"COUNT":>7}  REASON')
        for row in value['counts']:
            print(f'{row["result"]:<8} {row["count"]:>7}  {row["reason"]}')
    else:
        print('No observations recorded.')
    print(f'Skipped invalid rows: {value["skipped"]}')


def print_doctor(value, *, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
        return
    print(f'Configuration: {value["configuration"]}')
    repairs = {}
    color = sys.stdout.isatty() and 'NO_COLOR' not in os.environ
    for row in value['checks']:
        status = row['status']
        ok = status in {'supported', 'registered', 'available', 'configured'}
        informational = (row['component'] == 'review' and status == 'not_configured') or (row['component'] == 'gitleaks' and row.get('mode') in {'off', 'optional'})
        label = 'ok' if ok else ('info' if informational else 'fail')
        if color and label in {'ok', 'fail'}:
            label = f'\033[{32 if ok else 31}m{label}\033[0m'
        detail = row.get('version', row.get('mode', row.get('path', '')))
        print(f'[{label}] {row["component"]}: {status.replace("_", " ")} {detail}'.rstrip())
        if not ok and not informational:
            component = row['component']
            if component == 'git':
                repairs['git'] = 'Upgrade Git to 2.54.0 or later.'
            elif component.startswith('hook.preship-') or component in {'claude', 'codex'}:
                repairs['registration'] = 'Run pre-ship install. If checks still fail, repair disabled or conflicting Git or host hooks. Preserve unrelated hooks.'
            elif component == 'gitleaks':
                repairs['gitleaks'] = 'Install gitleaks to scan commits, or set gitleaks.mode in the configuration to match your scan policy. ' + row['note']
    if repairs:
        print('Next steps:')
        for repair in repairs.values():
            print(f'  {repair}')
    print(value['scope'])
