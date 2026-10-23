import contextlib
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pre_ship import cli, config
from pre_ship import git as checks

SCRIPT = Path(os.environ.get('PRE_SHIP_TEST_CLI', Path(__file__).resolve().parents[1] / 'bin/pre-ship'))


class ConfigurationReviewTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo = self.root / 'repo with spaces'
        self.repo.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'PIPX_'))}
        env.update(HOME=str(self.root), CODEX_HOME=str(self.root / '.codex'), XDG_CONFIG_HOME=str(self.root / '.config'), XDG_CACHE_HOME=str(self.root / '.cache'), XDG_DATA_HOME=str(self.root / '.local/share'), XDG_STATE_HOME=str(self.root / '.local/state'), GIT_CONFIG_GLOBAL=str(self.root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1', AI_AGENT='fixture')
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git('init', '-q', '-b', 'feature')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        (self.repo / 'file').write_text('staged\n')
        self.git('add', 'file')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def configure(self, text):
        path = config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def reviewer(self, program):
        self.configure('[gitleaks]\nmode = "off"\n[review]\ncommand = ' + json.dumps([sys.executable, '-c', program]) + '\n')

    def command(self, *args):
        return subprocess.run([str(SCRIPT), *args, '--cwd', str(self.repo)], capture_output=True, text=True)

    def install(self):
        result = self.command('install')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_reviewer_receives_exact_target_and_environment(self):
        output = self.root / 'target.json'
        self.reviewer('import json, os, pathlib, sys; target=json.load(sys.stdin); assert all(os.environ["PRE_SHIP_"+key.upper()]==value for key,value in target.items()); assert os.getcwd()==target["root"]; pathlib.Path(' + repr(str(output)) + ').write_text(json.dumps(target))')
        before = cli.target(self.repo, 'staged')
        result = self.command('review', 'staged')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, json.loads(output.read_text()))
        self.assertEqual('PASS', cli.check(self.repo, 'staged')['verdict'])

    def test_missing_and_failed_review_do_not_authorize_commit(self):
        self.install()
        missing = self.command('review')
        self.assertEqual(1, missing.returncode)
        self.assertIn('No review command', missing.stderr)
        rejected = subprocess.run(['git', '-C', str(self.repo), 'commit', '-sm', 'blocked'], capture_output=True, text=True)
        self.assertNotEqual(0, rejected.returncode)
        self.reviewer('import sys; print("private-review-sentinel"); print("private-review-sentinel", file=sys.stderr); sys.exit(7)')
        cli.record(self.repo, 'staged')
        failed = self.command('review')
        self.assertEqual(1, failed.returncode)
        self.assertIn('exited 7', failed.stderr)
        self.assertNotIn('private-review-sentinel', failed.stdout + failed.stderr)
        self.assertFalse(cli.receipt_path(str(self.repo)).exists())
        with self.assertRaises(ValueError):
            cli.check(self.repo, 'staged')

    def test_failed_reviewer_cannot_leave_a_receipt_that_it_recorded(self):
        self.reviewer('import subprocess, sys; subprocess.run([' + repr(str(SCRIPT)) + ', "record"], check=True); sys.exit(1)')
        self.assertEqual(1, self.command('review').returncode)
        self.assertFalse(cli.receipt_path(str(self.repo)).exists())

    def test_terminated_review_cleans_receipt_and_stops_direct_reviewer(self):
        for termination in [signal.SIGTERM, signal.SIGHUP]:
            with self.subTest(signal=termination):
                ready = self.root / 'reviewer-ready'
                ready.unlink(missing_ok=True)
                self.reviewer('import os, pathlib, subprocess, sys, time; subprocess.run([' + repr(str(SCRIPT)) + ', "record"], check=True); pathlib.Path(' + repr(str(ready)) + ').write_text(str(os.getpid())); time.sleep(30); sys.exit(1)')
                process = subprocess.Popen([str(SCRIPT), 'review', '--cwd', str(self.repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                reviewer_pid = None
                try:
                    deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), 'Reviewer did not reach the recorded-receipt checkpoint')
                    reviewer_pid = int(ready.read_text())
                    self.assertTrue(cli.receipt_path(str(self.repo)).exists())
                    process.send_signal(termination)
                    process.communicate(timeout=5)
                    self.assertNotEqual(0, process.returncode)
                    self.assertFalse(cli.receipt_path(str(self.repo)).exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(reviewer_pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                    if reviewer_pid is not None:
                        try:
                            os.kill(reviewer_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_review_preserves_inherited_ignored_sighup_in_parent_and_reviewer(self):
        ready = self.root / 'ignored-hup-ready'
        finished = self.root / 'ignored-hup-finished'
        program = 'import os, pathlib, signal, time; assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN; pathlib.Path(' + repr(str(ready)) + ').write_text(str(os.getpid()))\nwhile not pathlib.Path(' + repr(str(finished)) + ').exists(): time.sleep(0.02)\n'
        self.reviewer(program)
        wrapper = 'import os, signal; signal.signal(signal.SIGHUP, signal.SIG_IGN); os.execv(' + repr(str(SCRIPT)) + ', [' + repr(str(SCRIPT)) + ', "review", "--cwd", ' + repr(str(self.repo)) + '])'
        process = subprocess.Popen([sys.executable, '-c', wrapper], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), 'The reviewer did not inherit ignored SIGHUP')
            process.send_signal(signal.SIGHUP)
            os.kill(int(ready.read_text()), signal.SIGHUP)
            finished.touch()
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, stdout + stderr)
            self.assertEqual('PASS', cli.check(self.repo, 'staged')['verdict'])
        finally:
            finished.touch()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_changed_index_or_head_cannot_get_a_review_receipt(self):
        for mutation in ['import pathlib, subprocess; pathlib.Path("file").write_text("changed\\n"); subprocess.run(["git", "add", "file"], check=True)', 'import subprocess; subprocess.run(["git", "commit", "-qm", "new head"], check=True)']:
            with self.subTest(mutation=mutation):
                self.reviewer(mutation)
                result = self.command('review')
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn('target changed', result.stderr)
                self.assertFalse(cli.receipt_path(str(self.repo)).exists())

    def test_unstaged_edits_preserve_staged_review(self):
        self.reviewer('from pathlib import Path; Path("file").write_text("unstaged\\n")')
        self.assertEqual(0, self.command('review').returncode)
        self.assertEqual('PASS', cli.check(self.repo, 'staged')['verdict'])
        self.git('add', 'file')
        with self.assertRaises(ValueError):
            cli.check(self.repo, 'staged')

    def test_committed_review_binds_head_and_cannot_authorize_staged_changes(self):
        self.git('commit', '-qm', 'initial')
        self.reviewer('import json, sys; assert json.load(sys.stdin)["mode"] == "committed"')
        self.assertEqual(0, self.command('review', 'committed').returncode)
        self.assertEqual('PASS', cli.check(self.repo, 'committed')['verdict'])
        with self.assertRaises(ValueError):
            cli.check(self.repo, 'staged')
        self.git('commit', '--amend', '--no-edit', '--date=2000-01-01T00:00:00Z')
        with self.assertRaises(ValueError):
            cli.check(self.repo, 'committed')

    def test_review_then_real_commit_and_push_and_configured_protected_policy(self):
        self.reviewer('import json, sys; assert json.load(sys.stdin)["mode"] in ("staged", "committed")')
        self.install()
        self.assertEqual(0, self.command('review').returncode)
        self.git('commit', '-qsm', 'reviewed')
        remote = self.root / 'remote.git'
        self.git('init', '--bare', '-q', str(remote))
        blocked = subprocess.run(['git', '-C', str(self.repo), 'push', str(remote), 'HEAD:main'], capture_output=True, text=True)
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn('Direct push', blocked.stderr)
        self.assertEqual(0, self.command('review', 'committed').returncode)
        text = config.config_path().read_text()
        self.configure(text + '\n[push]\nallow_protected = true\n')
        self.git('push', str(remote), 'HEAD:main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('--git-dir', str(remote), 'rev-parse', 'main'))

    def test_custom_markers_control_the_actual_git_entrypoint(self):
        self.configure('agent_markers = ["CUSTOM_AGENT"]\n[gitleaks]\nmode = "off"\n')
        self.install()
        self.assertFalse(checks.agent_process())
        with patch.dict(os.environ, CUSTOM_AGENT='active'):
            self.assertTrue(checks.agent_process())
            blocked = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'blocked'], capture_output=True, text=True)
            self.assertNotEqual(0, blocked.returncode)
        self.git('commit', '-qm', 'human without sign-off')

    def test_invalid_configuration_blocks_default_agents_but_not_human_commits(self):
        self.install()
        self.configure('not valid toml')
        blocked = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'agent blocked'], capture_output=True, text=True)
        self.assertNotEqual(0, blocked.returncode)
        with patch.dict(os.environ, {key: '' for key in config.DEFAULTS['agent_markers']}):
            self.git('commit', '-qm', 'human without sign-off')

    def test_configured_hooks_path_runs_alongside_named_review_hooks(self):
        hooks = self.root / 'existing hooks'
        hooks.mkdir()
        marker = self.repo / 'existing-hook-ran'
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nprintf existing > existing-hook-ran\n')
        hook.chmod(0o755)
        self.git('config', 'core.hooksPath', str(hooks))
        self.reviewer('import json, sys; json.load(sys.stdin)')
        self.install()
        self.assertEqual(str(hooks), self.git('config', 'core.hooksPath'))
        self.assertEqual(0, self.command('review').returncode)
        self.git('commit', '-qsm', 'reviewed with existing hook')
        self.assertEqual('existing', marker.read_text())

    def test_custom_exemption_reply_and_configuration_validation(self):
        self.configure('exemption_reply = "OWNER APPROVED"\n')
        with self.assertRaises(ValueError):
            cli.record(self.repo, 'staged', verdict='EXEMPTED', reply='EXEMPT', reason='Evidence pending')
        cli.record(self.repo, 'staged', verdict='EXEMPTED', reply='OWNER APPROVED', reason='Evidence pending')
        self.assertEqual('EXEMPTED', cli.check(self.repo, 'staged')['verdict'])
        for text in ['unknown = true', 'agent_markers = [1]', 'exemption_reply = ""', '[review]\ncommand = "sh -c true"', '[gitleaks]\nmode = "silent"', '[push]\nallow_protected = "yes"', '[review]\nunknown = true', 'not valid toml']:
            with self.subTest(text=text):
                self.configure(text)
                with self.assertRaises(ValueError):
                    config.load_config()

    def test_missing_optional_gitleaks_warns_required_blocks_and_off_skips(self):
        with patch.object(checks.subprocess, 'run', side_effect=FileNotFoundError), contextlib.redirect_stderr(io.StringIO()) as output:
            checks.secrets_gate(self.repo)
            self.assertIn('Secret scanning was skipped', output.getvalue())
            self.configure('[gitleaks]\nmode = "required"\n')
            with self.assertRaises(ValueError):
                checks.secrets_gate(self.repo)
        self.configure('[gitleaks]\nmode = "off"\n')
        with patch.object(checks.subprocess, 'run') as run:
            checks.secrets_gate(self.repo)
            run.assert_not_called()

    def test_foreign_agent_helpers_are_rejected_before_any_install_write(self):
        path = self.root / '.codex/hooks.json'
        path.parent.mkdir()
        for helper in ['/opt/private/.agents/bin/pre-ship-gate', '/opt/private/.agents/bin/pre-ship']:
            with self.subTest(helper=helper):
                data = {'unrelated': {'keep': True}, 'hooks': {'PreToolUse': [{'hooks': [{'type': 'command', 'command': helper + ' hook'}]}]}}
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                result = self.command('install')
                self.assertEqual(1, result.returncode)
                self.assertIn(helper + ' uninstall', result.stderr)
                self.assertEqual(before, path.read_bytes())
                self.assertFalse((self.root / 'gitconfig').exists())
                self.assertFalse((self.root / '.claude/settings.json').exists())

    def test_foreign_git_registration_is_preserved_with_executable_repair(self):
        self.git('config', '--global', 'hook.preship-prepare.command', '/opt/private/pre-ship-gate prepare-commit-msg')
        path = self.root / 'gitconfig'
        before = path.read_bytes()
        result = self.command('install')
        self.assertEqual(1, result.returncode)
        self.assertIn('git config --global --get hook.preship-prepare.command', result.stderr)
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((self.root / '.claude/settings.json').exists())
        self.assertFalse((self.root / '.codex/hooks.json').exists())

    def test_empty_or_relative_xdg_never_loads_repository_configuration(self):
        for value in ['', 'relative']:
            with self.subTest(value=value), patch.dict(os.environ, XDG_CONFIG_HOME=value):
                self.assertEqual(self.root / '.config/pre-ship/config.toml', config.config_path())


if __name__ == '__main__':
    unittest.main()
