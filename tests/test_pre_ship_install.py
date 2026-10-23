import json
import os
import shlex
import sys
from unittest.mock import patch

from pre_ship.install import configure
import subprocess
import unittest

from test_pre_ship_gate import SCRIPT
from test_pre_ship_git import GitHookFixture


class InstallationTests(GitHookFixture):
    def command(self, action, code=0):
        result = subprocess.run([str(SCRIPT), action, '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def paths(self):
        return [self.root / 'gitconfig', self.root / '.claude/settings.json', self.root / '.codex/hooks.json']

    def test_rejected_helpers_leave_all_configuration_unchanged(self):
        self.paths()[0].write_text('[user]\n\tname = Existing Owner\n')
        for path in self.paths()[1:]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"unrelated": true}\n')
        before = {path: path.read_bytes() for path in self.paths()}
        directory = self.root / 'candidates'
        directory.mkdir()
        missing = directory / 'missing' / 'pre-ship'
        nonexecutable = directory / 'not-executable' / 'pre-ship'
        nonexecutable.parent.mkdir()
        nonexecutable.write_text('not executable')
        module = directory / '__main__.py'
        module.write_text('#!/bin/sh\nexit 0\n')
        module.chmod(0o755)
        wrong = directory / 'other-helper'
        wrong.write_text('#!/bin/sh\nexit 0\n')
        wrong.chmod(0o755)
        cache = self.root / 'custom uv cache'
        cached = cache / 'archive-v0' / 'hash' / 'bin' / 'pre-ship'
        cached.parent.mkdir(parents=True)
        cached.write_text('#!/bin/sh\nexit 0\n')
        cached.chmod(0o755)
        cache_plain = cache / 'plain-environment' / 'bin' / 'pre-ship'
        cache_plain.parent.mkdir(parents=True)
        cache_plain.write_text('#!/bin/sh\nexit 0\n')
        cache_plain.chmod(0o755)
        with patch.dict(os.environ, dict(self.env, UV_CACHE_DIR=str(cache)), clear=True):
            for helper in [missing, nonexecutable, module, wrong, cached, cache_plain]:
                with self.subTest(helper=helper), self.assertRaises(ValueError):
                    configure(self.repo, helper, install=True)
                self.assertEqual(before, {path: path.read_bytes() for path in self.paths()})
        result = subprocess.run([sys.executable, '-m', 'pre_ship', 'install', '--cwd', str(self.repo)], env=dict(self.env, PYTHONPATH=str(SCRIPT.parent.parent / 'src')), capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertIn('uv tool install', result.stderr)
        self.assertIn('No configuration was changed', result.stderr)
        self.assertEqual(before, {path: path.read_bytes() for path in self.paths()})

    def test_stable_symlink_is_registered_and_target_identity_works(self):
        stable = self.root / 'stable bin' / 'pre-ship'
        stable.parent.mkdir()
        stable.symlink_to(SCRIPT)
        result = subprocess.run([str(stable), 'install', '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        command = self.run_git('config', '--global', 'hook.preship-prepare.command').stdout.strip()
        self.assertEqual(str(stable), shlex.split(command)[0])
        for path in self.paths()[1:]:
            data = json.loads(path.read_text())
            command = data['hooks']['PreToolUse'][0]['hooks'][0]['command']
            self.assertEqual(str(stable), shlex.split(command)[0])
        self.command('doctor')
        self.command('uninstall')
        self.assertTrue(all(not path.exists() for path in self.paths()))
        self.command('install')
        for action in ['doctor', 'uninstall']:
            result = subprocess.run([str(stable), action, '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(all(not path.exists() for path in self.paths()))

    def test_pipx_run_cache_helpers_are_rejected_without_configuration_writes(self):
        self.paths()[0].write_text('[user]\n\tname = Existing Owner\n')
        for path in self.paths()[1:]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"unrelated": true}\n')
        before = {path: path.read_bytes() for path in self.paths()}
        cases = [
            (self.root / '.cache/pipx', {}),
            (self.root / 'Library/Caches/pipx', {}),
            (self.root / '.local/pipx/.cache', {}),
            (self.root / 'Library/Application Support/pipx/.cache', {}),
            (self.root / 'xdg-cache/pipx', {'XDG_CACHE_HOME': str(self.root / 'xdg-cache')}),
            (self.root / 'custom-pipx/.cache', {'PIPX_HOME': str(self.root / 'custom-pipx')}),
        ]
        for cache, overrides in cases:
            for path, data in before.items():
                path.write_bytes(data)
            helper = cache / 'hash/bin/pre-ship'
            helper.parent.mkdir(parents=True)
            helper.write_text('#!/bin/sh\nexit 0\n')
            helper.chmod(0o755)
            with self.subTest(cache=cache), patch.dict(os.environ, dict(self.env, **overrides), clear=True):
                with self.assertRaises(ValueError):
                    configure(self.repo, helper, install=True)
                self.assertEqual(before, {path: path.read_bytes() for path in self.paths()})

    def test_reinstall_via_symlink_updates_both_hosts_before_uninstall(self):
        self.command('install')
        stable = self.root / 'stable bin/pre-ship'
        stable.parent.mkdir()
        stable.symlink_to(SCRIPT)
        result = subprocess.run([str(stable), 'install', '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(str(stable), shlex.split(self.run_git('config', '--global', 'hook.preship-prepare.command').stdout)[0])
        for path in self.paths()[1:]:
            data = json.loads(path.read_text())
            command = data['hooks']['PreToolUse'][0]['hooks'][0]['command']
            self.assertEqual(str(stable), shlex.split(command)[0])
        result = subprocess.run([str(stable), 'uninstall', '--cwd', str(self.repo)], env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(all(not path.exists() for path in self.paths()))

    def test_empty_home_round_trip_and_idempotence(self):
        self.paths()[0].unlink()
        self.command('install')
        self.command('doctor')
        installed = {p: p.read_bytes() for p in self.paths()}
        self.command('install')
        self.assertEqual(installed, {p: p.read_bytes() for p in self.paths()})
        self.command('uninstall')
        self.assertTrue(all(not p.exists() for p in self.paths()))
        self.command('doctor', code=1)
        self.command('uninstall')

    def test_unrelated_configuration_and_mixed_groups_survive(self):
        self.run_git('config', '--global', 'user.name', 'Existing Owner')
        self.run_git('config', '--global', 'hook.other.event', 'pre-push')
        self.run_git('config', '--global', 'hook.other.command', 'true')
        self.run_git('config', '--global', 'hook.preship-push.note', 'keep')
        keep = {'type': 'command', 'command': 'true'}
        settings = {'unrelated': {'keep': True}, 'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [keep, {'type': 'command', 'command': str(SCRIPT) + ' hook'}]}], 'Stop': []}}
        for p in self.paths()[1:]:
            settings['hooks']['PreToolUse'][0]['matcher'] = 'Bash' if p.parent.name == '.claude' else '.*'
            settings['hooks']['PreToolUse'][0]['hooks'][1]['onFailure'] = 'block'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(settings))
        self.command('install')
        self.command('doctor')
        self.command('uninstall')
        for p in self.paths()[1:]:
            data = json.loads(p.read_text())
            self.assertEqual({'keep': True}, data['unrelated'])
            self.assertEqual([], data['hooks']['Stop'])
            self.assertEqual([{'matcher': 'Bash' if p.parent.name == '.claude' else '.*', 'hooks': [keep]}], data['hooks']['PreToolUse'])
        self.assertEqual('Existing Owner', self.run_git('config', '--global', 'user.name').stdout.strip())
        self.assertEqual('true', self.run_git('config', '--global', 'hook.other.command').stdout.strip())
        self.assertEqual('keep', self.run_git('config', '--global', 'hook.preship-push.note').stdout.strip())

    def test_install_rejects_conflicting_owned_namespace_without_partial_writes(self):
        self.run_git('config', '--global', 'hook.preship-push.command', 'user-command')
        before = self.paths()[0].read_bytes()
        self.command('install', code=1)
        self.assertEqual(before, self.paths()[0].read_bytes())
        self.assertFalse(self.paths()[1].exists())
        self.assertFalse(self.paths()[2].exists())
        self.command('uninstall')
        self.assertEqual('user-command', self.run_git('config', '--global', 'hook.preship-push.command').stdout.strip())
        self.run_git('config', '--global', 'hook.preship-push.command', str(SCRIPT) + ' pre-push')
        self.run_git('config', '--global', '--add', 'hook.preship-push.command', '')
        before = self.paths()[0].read_bytes()
        self.command('install', code=1)
        self.assertEqual(before, self.paths()[0].read_bytes())

    def test_uninstall_preserves_edits_to_managed_registration(self):
        self.command('install')
        self.run_git('config', '--global', 'hook.preship-push.command', 'user-command')
        path = self.paths()[2]
        data = json.loads(path.read_text())
        data['hooks']['PreToolUse'][0]['hooks'][0]['command'] = 'user-command'
        path.write_text(json.dumps(data))
        self.command('uninstall')
        self.assertEqual('user-command', self.run_git('config', '--global', 'hook.preship-push.command').stdout.strip())
        self.assertEqual(data, json.loads(path.read_text()))

    def test_symlink_targets_and_shared_disable_flags_are_preserved(self):
        path = self.paths()[1]
        path.parent.mkdir()
        target = self.root / 'claude-settings.json'
        target.write_text('{"keep": true}\n')
        target.chmod(0o640)
        path.symlink_to(target)
        self.run_git('config', '--global', 'hook.pre-push.enabled', 'false')
        self.command('install', code=1)
        self.assertTrue(path.is_symlink())
        self.assertEqual(0o640, target.stat().st_mode & 0o777)
        self.command('uninstall')
        self.assertTrue(path.is_symlink())
        self.assertEqual({'keep': True}, json.loads(target.read_text()))
        self.assertEqual('false', self.run_git('config', '--global', 'hook.pre-push.enabled').stdout.strip())

    def test_legacy_matcher_is_repaired_without_expanding_unrelated_commands(self):
        path = self.paths()[2]
        path.parent.mkdir()
        keep = {'type': 'command', 'command': 'true'}
        own = {'type': 'command', 'command': str(SCRIPT) + ' hook', 'timeout': 7}
        data = {'hooks': {'PreToolUse': [{'matcher': '^Bash$', 'hooks': [keep, own]}]}}
        path.write_text(json.dumps(data))
        self.command('install')
        actual = json.loads(path.read_text())
        self.assertEqual({'matcher': '^Bash$', 'hooks': [keep]}, actual['hooks']['PreToolUse'][0])
        self.assertEqual({'matcher': '.*', 'hooks': [own]}, actual['hooks']['PreToolUse'][1])
        snapshot = path.read_bytes()
        self.command('install')
        self.assertEqual(snapshot, path.read_bytes())
        self.command('uninstall')
        self.assertEqual({'hooks': {'PreToolUse': [{'matcher': '^Bash$', 'hooks': [keep]}]}}, json.loads(path.read_text()))

    def test_invalid_host_json_is_rejected_before_any_configuration_write(self):
        path = self.paths()[2]
        path.parent.mkdir()
        path.write_text('{invalid')
        before = self.paths()[0].read_bytes()
        self.command('install', code=1)
        self.assertEqual(before, self.paths()[0].read_bytes())
        self.assertEqual('{invalid', path.read_text())
        self.assertFalse(self.paths()[1].exists())

    def test_xdg_global_configuration_is_used_without_creating_gitconfig(self):
        self.paths()[0].unlink()
        self.env.pop('GIT_CONFIG_GLOBAL')
        self.env['XDG_CONFIG_HOME'] = str(self.root / 'xdg')
        path = self.root / 'xdg/git/config'
        path.parent.mkdir(parents=True)
        original = '[user]\n\tname = Existing Owner\n'
        path.write_text(original)
        self.command('install')
        self.command('doctor')
        self.command('uninstall')
        self.assertEqual(original, path.read_text())
        self.assertFalse((self.root / '.gitconfig').exists())


if __name__ == '__main__':
    unittest.main()
