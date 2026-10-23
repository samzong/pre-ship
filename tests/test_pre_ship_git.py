import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_pre_ship_gate import SCRIPT, isolated_environment


class GitHookFixture(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.env = isolated_environment(self.root)
        self.env['AI_AGENT'] = 'fixture'
        self.repo = self.root / 'repo with spaces'
        self.repo.mkdir()
        self.git_bin = shutil.which('git')
        self.run_git('init', '-q', '-b', 'feat/probe')
        self.run_git('config', 'user.name', 'Fixture')
        self.run_git('config', 'user.email', 'fixture@example.invalid')
        self.run_git('config', 'commit.gpgsign', 'false')
        for name, event in [('prepare', 'prepare-commit-msg'), ('apply', 'pre-applypatch'), ('message', 'commit-msg'), ('push', 'pre-push')]:
            self.run_git('config', '--global', f'hook.preship-{name}.event', event)
            self.run_git('config', '--global', f'hook.preship-{name}.command', f'"{SCRIPT}" {event}')

    def run_git(self, *args, code=0):
        result = subprocess.run([self.git_bin, '-C', str(self.repo), *args], env=self.env, capture_output=True, text=True)
        if code is None:
            self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        else:
            self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def stage(self, path, content):
        (self.repo / path).write_text(content)
        self.run_git('add', '--', path)

    def record(self, mode='staged'):
        result = subprocess.run([str(SCRIPT), 'record', mode], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return Path(result.stdout.strip())

    def commit(self, message='fixture'):
        self.record()
        return self.run_git('commit', '-qsm', message)


class GitHookTests(GitHookFixture):
    def manual_environment(self):
        for key in ['CLAUDECODE', 'AI_AGENT', 'CLAUDE_CODE_SESSION_ID', 'LODY_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID']:
            self.env.pop(key, None)

    def test_manual_git_commands_skip_receipts_and_all_gate_policy(self):
        self.manual_environment()
        self.stage('file', 'trailing \n' + 'x' * 20001 + '\naws_access_key_id = AKIA' + 'QWERTYUIOPASDFGH\n')
        self.run_git('commit', '-m', 'unsigned without policy', code=0)
        remote = self.root / 'manual.git'
        self.run_git('init', '--bare', '-q', str(remote))
        self.run_git('push', str(remote), 'HEAD:refs/heads/main')
        self.run_git('push', str(remote), 'HEAD:refs/heads/feat/probe')
        self.run_git('checkout', '--detach', '-q')
        self.run_git('push', str(remote), 'HEAD:refs/heads/feat/detached')

    def test_each_agent_session_marker_requires_receipts(self):
        self.manual_environment()
        self.stage('file', 'first\n')
        for marker in ['CLAUDECODE', 'AI_AGENT', 'CLAUDE_CODE_SESSION_ID', 'LODY_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID']:
            with self.subTest(marker=marker):
                self.env[marker] = 'fixture'
                self.assertIn('PASS receipt', self.run_git('commit', '-sm', 'agent commit', code=1).stderr)
                self.env.pop(marker)

    def test_real_commit_requires_receipt_and_signoff_and_rechecks_final_index(self):
        self.stage('file', 'first\n')
        self.assertIn('PASS receipt', self.run_git('commit', '-sm', 'first', code=1).stderr)
        receipt = self.record()
        data = json.loads(receipt.read_text())
        data['created_at'] -= 301
        receipt.write_text(json.dumps(data))
        self.assertIn('PASS receipt', self.run_git('commit', '-sm', 'first', code=1).stderr)
        self.record()
        self.assertIn('Signed-off-by', self.run_git('commit', '-m', 'first', code=1).stderr)
        (self.repo / 'file').write_text('unstaged\n')
        self.assertIn('PASS receipt', self.run_git('commit', '-asm', 'first', code=1).stderr)
        self.run_git('commit', '-qsm', 'first')
        self.assertEqual('first\n', self.run_git('show', 'HEAD:file').stdout)
        self.record()
        self.run_git('commit', '--amend', '--no-edit', '-s', '-q')

    def test_size_growth_boundary_and_local_attribute_exclusion(self):
        local = self.repo / '.git' / 'info' / 'attributes'
        local.parent.mkdir(exist_ok=True)
        self.stage('large.md', 'x' * 20001)
        self.record()
        error = self.run_git('commit', '-sm', 'oversized', code=1).stderr
        self.assertIn('5,001 tokens', error)
        self.assertIn('Split documents by section.', error)
        self.assertIn('Do not use --no-verify', error)
        self.assertIn('/.git/info/attributes', error)
        self.stage('.gitattributes', 'large.md pre-ship-large-file\n')
        self.record()
        self.assertIn('5,001 tokens', self.run_git('commit', '-sm', 'repository exclusion', code=1).stderr)
        local.write_text('large.md pre-ship-large-file\n')
        self.commit('excluded baseline')
        local.write_text('')
        self.stage('large.md', 'x' * 20000)
        self.commit('shrink to threshold')
        self.stage('large.md', 'x' * 20001)
        self.record()
        self.assertIn('5,001 tokens', self.run_git('commit', '-sm', 'cross threshold', code=1).stderr)
        local.write_text('large.md pre-ship-large-file\n')
        self.commit('excluded large baseline')
        local.write_text('')
        self.stage('large.md', 'y' * 20001)
        self.commit('same size above threshold')
        self.stage('large.md', 'y' * 20002)
        self.record()
        self.assertIn('5,001 tokens', self.run_git('commit', '-sm', 'grow oversized', code=1).stderr)
        self.stage('large.md', 'y' * 20001)
        self.run_git('mv', 'large.md', 'renamed.md')
        self.commit('rename without growth')

    def test_lockfile_exclusion_and_secret_scan_use_index(self):
        self.stage('pnpm-lock.yaml', '# packages\n' * 3000)
        self.commit('large lockfile')
        secret = 'AKIA' + 'QWERTYUIOPASDFGH'
        self.stage('credentials.txt', 'aws_access_key_id = ' + secret + '\n')
        self.record()
        (self.repo / 'credentials.txt').write_text('clean working tree\n')
        result = self.run_git('commit', '-sm', 'staged secret', code=1)
        self.assertIn('Staged secret scan failed', result.stderr)
        self.assertIn('credentials.txt', result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)
        self.stage('credentials.txt', 'clean\n')
        self.commit('clean index')

    def test_pre_push_uses_exact_ref_updates_and_committed_receipt(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.root / 'remote.git'
        self.run_git('init', '--bare', '-q', str(remote))
        self.assertIn('committed PASS', self.run_git('push', str(remote), 'HEAD:refs/heads/feat/probe', code=1).stderr)
        self.record('committed')
        self.assertIn('Direct push', self.run_git('push', str(remote), 'HEAD:refs/heads/main', code=1).stderr)
        self.run_git('tag', 'probe')
        self.assertIn('SemVer', self.run_git('push', str(remote), 'refs/tags/probe', code=1).stderr)
        self.run_git('tag', '-a', '-m', 'annotated fixture', 'annotated')
        self.assertIn('Multiple refs', self.run_git('push', '--follow-tags', str(remote), 'HEAD:refs/heads/feat/probe', code=1).stderr)
        self.assertIn('Multiple refs', self.run_git('push', str(remote), 'HEAD:refs/heads/one', 'HEAD:refs/heads/two', code=1).stderr)
        self.run_git('branch', 'other')
        self.assertIn('current branch', self.run_git('push', str(remote), 'other:refs/heads/other', code=1).stderr)
        self.run_git('push', str(remote), 'HEAD:refs/heads/feat/probe')
        self.assertIn('deletion', self.run_git('push', str(remote), ':refs/heads/feat/probe', code=1).stderr)
        self.run_git('config', 'remote.probe.url', str(remote))
        self.run_git('config', 'push.default', 'matching')
        self.run_git('push', 'probe')
        self.stage('file', 'next\n')
        self.commit('next')
        self.assertIn('committed PASS', self.run_git('push', 'probe', 'HEAD:refs/heads/feat/probe', code=1).stderr)
        self.record('committed')
        self.run_git('push', 'probe', 'HEAD:refs/heads/feat/probe')

        self.run_git('checkout', '--detach', '-q')
        self.assertIn('detached HEAD', self.run_git('push', 'probe', 'HEAD:refs/heads/detached', code=1).stderr)

    def test_agent_fast_forward_to_another_remote_branch_needs_no_receipt(self):
        self.stage('file', 'first\n')
        self.commit('first')
        upstream, fork = self.root / 'upstream.git', self.root / 'fork.git'
        for name, remote in [('upstream', upstream), ('origin', fork)]:
            self.run_git('init', '--bare', '-q', str(remote))
            self.run_git('-C', str(remote), 'fetch', '-q', str(self.repo), 'HEAD:refs/heads/main')
            self.run_git('remote', 'add', name, str(remote))
        self.stage('file', 'second\n')
        self.commit('second')
        self.run_git('-C', str(upstream), 'fetch', '-q', str(self.repo), 'HEAD:refs/heads/main')
        self.run_git('fetch', '-q', '--all')
        self.run_git('push', 'origin', 'HEAD:refs/heads/main')
        self.assertEqual(self.run_git('rev-parse', 'HEAD').stdout, self.run_git('-C', str(fork), 'rev-parse', 'main').stdout)
        self.assertIn('Direct push', self.run_git('push', '--force', 'origin', 'HEAD~1:refs/heads/main', code=1).stderr)
        self.stage('file', 'local\n')
        self.commit('local')
        self.assertIn('Direct push', self.run_git('push', 'origin', 'HEAD:refs/heads/main', code=1).stderr)
        self.assertIn('committed PASS', self.run_git('push', 'origin', 'HEAD:refs/heads/feat/probe', code=1).stderr)

    def test_repository_hooks_still_run_and_mutations_invalidate_receipt(self):
        self.stage('file', 'first\n')
        hook = self.repo / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nprintf repository-hook >&2\nexit 1\n')
        hook.chmod(0o755)
        self.record()
        self.assertIn('repository-hook', self.run_git('commit', '-sm', 'repo block', code=1).stderr)
        hook.write_text('#!/bin/sh\nprintf changed > file\ngit add file\n')
        self.assertIn('PASS receipt', self.run_git('commit', '-sm', 'repo mutation', code=1).stderr)
        self.commit('reviewed repository mutation')

    def test_signoff_must_be_a_trailer_and_not_message_body(self):
        self.stage('file', 'first\n')
        self.record()
        self.assertIn('Signed-off-by', self.run_git('commit', '-m', 'Signed-off-by: Fixture <fixture@example.invalid>\n\nbody', code=1).stderr)
        self.run_git('commit', '-qsm', 'signed')

    def test_message_editor_cannot_remove_signoff_after_index_gate(self):
        self.stage('file', 'first\n')
        self.record()
        editor = self.root / 'editor.py'
        editor.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\np = Path(sys.argv[1])\np.write_text("edited\\n\\n" + "\\n".join(line for line in p.read_text().splitlines() if not line.lower().startswith("signed-off-by:")))\n')
        editor.chmod(0o755)
        self.env['GIT_EDITOR'] = str(editor)
        self.assertIn('Signed-off-by', self.run_git('commit', '-s', code=1).stderr)
        self.run_git('commit', '-qsm', 'signed')

    def test_message_editor_can_outlast_receipt_ttl_but_cannot_change_binding(self):
        self.stage('file', 'first\n')
        receipt = self.record()
        editor = self.root / 'editor.py'
        editor.write_text('#!/usr/bin/env python3\nimport json\nfrom pathlib import Path\np = Path(' + repr(str(receipt)) + ')\ndata = json.loads(p.read_text())\ndata["created_at"] -= 301\np.write_text(json.dumps(data))\n')
        editor.chmod(0o755)
        self.env['GIT_EDITOR'] = str(editor)
        self.run_git('commit', '-s', '-m', 'long edit', '--edit', '-q')
        self.record()
        message = self.root / 'message'
        message.write_text('fixture\n\nSigned-off-by: Fixture <fixture@example.invalid>\n')
        self.stage('file', 'changed\n')
        result = subprocess.run([str(SCRIPT), 'commit-msg', str(message)], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertIn('PASS receipt', result.stderr)

    def test_automatic_merge_cherry_pick_and_rebase_stop_for_review_and_resume(self):
        self.stage('base', 'base\n')
        self.commit('base')
        base = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('checkout', '-qb', 'feat/source')
        self.stage('source', 'source\n')
        self.commit('source')
        source = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('checkout', '-q', 'feat/probe')
        self.stage('other', 'other\n')
        self.commit('other')
        self.assertIn('PASS receipt', self.run_git('cherry-pick', source, code=None).stderr)
        self.run_git('rev-parse', '-q', '--verify', 'CHERRY_PICK_HEAD')
        self.record()
        self.run_git('commit', '-s', '--no-edit', '-q')
        self.run_git('reset', '--hard', base)
        self.assertIn('PASS receipt', self.run_git('merge', '--no-ff', source, code=None).stderr)
        self.run_git('rev-parse', '-q', '--verify', 'MERGE_HEAD')
        self.record()
        self.run_git('commit', '-s', '--no-edit', '-q')
        self.run_git('checkout', '-q', 'feat/source')
        self.stage('second', 'second\n')
        self.commit('second')
        self.run_git('checkout', '-q', 'feat/probe')
        self.stage('third', 'third\n')
        self.commit('third')
        self.run_git('checkout', '-q', 'feat/source')
        self.assertIn('PASS receipt', self.run_git('rebase', 'feat/probe', code=None).stderr)
        self.run_git('rev-parse', '-q', '--verify', 'REBASE_HEAD')
        self.record()
        self.run_git('commit', '-s', '--no-edit', '-q')
        self.run_git('rebase', '--continue')
        self.assertEqual('second\n', self.run_git('show', 'HEAD:second').stdout)
        self.assertEqual('third\n', self.run_git('show', 'HEAD:third').stdout)

    def test_whitespace_uses_git_configuration_and_attributes(self):
        self.stage('file', 'trailing \n')
        self.record()
        self.run_git('commit', '-sm', 'whitespace', code=1)
        self.run_git('config', 'core.whitespace', '-trailing-space')
        self.run_git('commit', '-qsm', 'configured whitespace')
        self.run_git('config', 'core.whitespace', 'trailing-space')
        self.stage('.gitattributes', 'file -whitespace\n')
        self.stage('file', 'attribute exception \n')
        self.commit('attribute whitespace')
        self.stage('.gitattributes', '')
        self.stage('file', 'new trailing \n')
        self.record()
        self.run_git('commit', '-sm', 'attribute removed', code=1)

    def test_existing_whitespace_does_not_block_clean_changes_after_policy_change(self):
        self.run_git('config', 'core.whitespace', '-trailing-space')
        self.stage('file', 'historical space \n')
        self.commit('historical whitespace')
        self.run_git('config', 'core.whitespace', 'trailing-space')
        self.stage('file', 'historical space \nclean addition\n')
        self.commit('clean addition')

    def test_missing_optional_secret_scanner_warns_and_allows_commit(self):
        self.stage('file', 'first\n')
        self.record()
        git_only = self.root / 'git-only'
        git_only.mkdir()
        (git_only / 'git').symlink_to(self.git_bin)
        self.env['PATH'] = os.pathsep.join([str(Path(sys.executable).parent), str(git_only), '/usr/bin:/bin'])
        self.git_bin = shutil.which('git', path=self.env['PATH'])
        result = self.run_git('commit', '-qsm', 'missing optional scanner')
        self.assertIn('gitleaks is not installed', result.stderr)

    def test_git_am_requires_review_of_applied_index_then_can_continue(self):
        self.stage('base', 'base\n')
        self.commit('base')
        self.run_git('checkout', '-qb', 'feat/mail')
        self.stage('mail', 'patch\n')
        self.commit('mailed patch')
        patch = self.root / 'mail.patch'
        patch.write_text(self.run_git('format-patch', '--stdout', '-1', 'HEAD').stdout)
        self.run_git('checkout', '-q', 'feat/probe')
        self.stage('target', 'target\n')
        self.commit('target')
        self.assertIn('PASS receipt', self.run_git('am', str(patch), code=1).stderr)
        self.record()
        self.run_git('am', '--continue')
        self.assertEqual('patch\n', self.run_git('show', 'HEAD:mail').stdout)


if __name__ == '__main__':
    unittest.main()
