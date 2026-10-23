import json
from types import SimpleNamespace
import os
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_pre_ship_gate import SCRIPT
from test_pre_ship_git import GitHookFixture
from pre_ship.doctor import git_version


class RegistrationTests(GitHookFixture):
    def test_configuration_alone_cannot_pass_when_git_cannot_list_native_hooks(self):
        self.settings('claude')
        self.settings('codex')
        shim = self.root / 'shim'
        shim.mkdir()
        program = shim / 'git'
        program.write_text('#!/bin/sh\nif [ "$3" = hook ] && [ "$4" = list ]; then exit 129; fi\nexec ' + shlex.quote(self.git_bin) + ' "$@"\n')
        program.chmod(0o755)
        self.env['PATH'] = str(shim) + os.pathsep + self.env['PATH']
        data = self.doctor(1)
        rows = {row['component']: row['status'] for row in data['checks']}
        self.assertEqual('registered', rows['claude'])
        self.assertEqual('registered', rows['codex'])
        self.assertTrue(all(rows['hook.preship-' + name] == 'missing_or_different' for name in ['prepare', 'message', 'apply', 'push']))

    def settings(self, host, **overrides):
        path = self.root / ('.claude/settings.json' if host == 'claude' else '.codex/hooks.json')
        path.parent.mkdir(exist_ok=True)
        command = {'type': 'command', 'command': f'"{SCRIPT}" hook', **({'onFailure': 'block'} if host == 'claude' else {})}
        command.update(overrides)
        entry = {'hooks': [command], **({'matcher': 'Bash'} if host == 'claude' else {})}
        data = {'hooks': {'PreToolUse': [entry]}}
        path.write_text(json.dumps(data))
        return path

    def doctor(self, code):
        result = subprocess.run([str(SCRIPT), 'doctor', '--json', '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
        self.assertEqual(code, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_scanner_repairs_only_describe_required_missing_scanners(self):
        self.settings('claude')
        self.settings('codex')
        commands = self.root / 'commands'
        commands.mkdir()
        (commands / 'git').symlink_to(self.git_bin)
        (commands / 'python3').symlink_to(sys.executable)
        self.env['PATH'] = str(commands)
        self.env['NO_COLOR'] = '1'
        configuration = self.root / '.config/pre-ship/config.toml'
        configuration.parent.mkdir(parents=True)
        for mode, code in [('off', 0), ('optional', 0), ('required', 1)]:
            with self.subTest(mode=mode):
                configuration.write_text(f'[gitleaks]\nmode = "{mode}"\n')
                result = subprocess.run([str(SCRIPT), 'doctor'], cwd=self.repo, env=self.env, capture_output=True, text=True)
                self.assertEqual(code, result.returncode, result.stderr)
                if mode == 'required':
                    self.assertIn('Install gitleaks', result.stdout)
                else:
                    self.assertNotIn('Install gitleaks', result.stdout)
                self.assertIn('Set review.command', result.stdout)

    def test_disabled_host_is_reported_as_failure_in_human_diagnostics(self):
        path = self.settings('claude')
        self.settings('codex')
        self.env['NO_COLOR'] = '1'
        result = subprocess.run([str(SCRIPT), 'doctor'], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('[info] review: not configured', result.stdout)
        self.assertNotIn('[fail]', result.stdout)
        data = json.loads(path.read_text())
        data['disableAllHooks'] = True
        path.write_text(json.dumps(data))
        result = subprocess.run([str(SCRIPT), 'doctor'], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn('[fail] claude:', result.stdout)

    def test_global_hooks_and_agent_entries_are_checked_without_writes(self):
        claude = self.settings('claude')
        codex = self.settings('codex')
        before = {path: path.read_bytes() for path in [claude, codex, self.root / 'gitconfig']}
        data = self.doctor(0)
        rows = {row['component']: row['status'] for row in data['checks']}
        self.assertEqual('supported', rows['git'])
        self.assertTrue(all(rows['hook.preship-' + name] == 'registered' for name in ['prepare', 'message', 'apply', 'push']))
        self.assertEqual('registered', rows['claude'])
        self.assertEqual('registered', rows['codex'])
        for path, content in before.items():
            self.assertEqual(content, path.read_bytes())
        self.run_git('config', 'hook.preship-prepare.enabled', 'false')
        data = self.doctor(1)
        row = next(row for row in data['checks'] if row['component'] == 'hook.preship-prepare')
        self.assertEqual('missing_or_different', row['status'])
        self.assertTrue(row['repair'])

    def test_event_disable_is_detected_even_when_named_hook_is_registered(self):
        self.settings('claude')
        self.settings('codex')
        self.stage('file', 'first\n')
        self.run_git('hook', 'run', 'prepare-commit-msg', '--', '.git/COMMIT_EDITMSG', code=1)
        self.run_git('config', 'hook.prepare-commit-msg.enabled', 'false')
        supports_event_switch = 'hook.<event>.enabled' in self.run_git('help', '--config').stdout
        self.run_git('hook', 'run', 'prepare-commit-msg', '--', '.git/COMMIT_EDITMSG', code=0 if supports_event_switch else 1)
        rows = {row['component']: row['status'] for row in self.doctor(1)['checks']}
        self.assertEqual('missing_or_different', rows['hook.preship-prepare'])

    def test_missing_different_duplicate_disabled_and_async_registration(self):
        self.settings('claude')
        self.settings('codex')
        for key, value in [('hook.preship-push.command', '/wrong/helper pre-push'), ('hook.preship-push.event', 'commit-msg'), ('hook.preship-push.enabled', 'false')]:
            with self.subTest(key=key):
                self.run_git('config', key, value)
                rows = {row['component']: row['status'] for row in self.doctor(1)['checks']}
                self.assertEqual('missing_or_different', rows['hook.preship-push'])
                self.run_git('config', '--unset', key)
        self.run_git('config', '--global', '--add', 'hook.preship-push.event', 'pre-push')
        rows = {row['component']: row['status'] for row in self.doctor(1)['checks']}
        self.assertEqual('missing_or_different', rows['hook.preship-push'])
        self.run_git('config', '--global', '--unset-all', 'hook.preship-push.event')
        self.assertTrue(next(row for row in self.doctor(1)['checks'] if row['component'] == 'hook.preship-push')['repair'])
        self.run_git('config', '--global', 'hook.preship-push.event', 'pre-push')
        for host, options in [('claude', {'onFailure': 'ignore'}), ('codex', {'async': True}), ('codex', {'disabled': True})]:
            with self.subTest(host=host, options=options):
                self.settings(host, **options)
                rows = {row['component']: row for row in self.doctor(1)['checks']}
                self.assertNotEqual('registered', rows[host]['status'])
                self.assertIn('entry', rows[host])
                self.settings(host)

    def test_repair_does_not_disclose_unrelated_settings_or_commands(self):
        private = 'private-value-not-for-diagnostics'
        path = self.settings('claude', command='echo ' + private)
        data = json.loads(path.read_text())
        data['unrelated'] = private
        path.write_text(json.dumps(data))
        self.settings('codex')
        result = self.doctor(1)
        self.assertNotIn(private, json.dumps(result))
        self.assertEqual(data, json.loads(path.read_text()))


class GitVersionTests(unittest.TestCase):
    def test_git_2_54_is_the_minimum_supported_version(self):
        for version, supported in [('2.53.9', False), ('2.54.0', True), ('2.55.0', True)]:
            with self.subTest(version=version), patch('pre_ship.doctor.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=f'git version {version}\n')):
                self.assertEqual((supported, f'git version {version}'), git_version())


if __name__ == '__main__':
    unittest.main()
