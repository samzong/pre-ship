import json
import subprocess
import time

from test_pre_ship_gate import SCRIPT
from test_pre_ship_git import GitHookFixture


class BranchDeletionTests(GitHookFixture):
    def setUp(self):
        super().setUp()
        self.stage('file', 'first\n')
        self.commit('first')
        self.remote = self.root / 'remote.git'
        self.run_git('init', '--bare', '-q', str(self.remote))
        self.run_git('remote', 'add', 'origin', str(self.remote))
        self.record('committed')
        self.run_git('push', 'origin', 'HEAD:refs/heads/old')
        self.oid = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.ref = 'refs/heads/old'

    def receipt(self, ref=None, oid=None, remote='origin', code=0):
        result = subprocess.run([str(SCRIPT), 'record-deletion', '--remote', remote, '--ref', ref or self.ref,
            '--expected-oid', oid or self.oid, '--json'], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def delete(self, remote='origin', ref=None, code=0):
        return self.run_git('push', f'--force-with-lease={ref or self.ref}:{self.oid}', remote, ':' + (ref or self.ref), code=code)

    def test_reviewed_deletion_uses_real_hook_and_preserves_other_refs(self):
        self.record('committed')
        self.run_git('push', 'origin', 'HEAD:refs/heads/keep')
        self.assertIn('deletion PASS', self.delete(code=1).stderr)
        self.receipt()
        self.delete()
        refs = self.run_git('ls-remote', 'origin').stdout
        self.assertNotIn(self.ref, refs)
        self.assertIn('refs/heads/keep', refs)
        self.assertEqual(self.oid, self.run_git('rev-parse', 'HEAD').stdout.strip())

    def test_receipt_binds_remote_ref_tip_root_and_freshness(self):
        value = json.loads(self.receipt().stdout)
        path = self.root / '.cache/pre-ship'
        receipt = next(path.glob('*-deletion.json'))
        for key, changed in [('remote', '0' * 64), ('ref', 'refs/heads/other'), ('oid', '1' * 40),
                ('root', str(self.root)), ('created_at', time.time() - 301), ('created_at', time.time() + 60), ('version', 0), ('verdict', 'EXEMPTED')]:
            with self.subTest(key=key, changed=changed):
                receipt.write_text(json.dumps(dict(value, **{key: changed})))
                self.assertIn('deletion PASS', self.delete(code=1).stderr)
                self.assertIn(self.ref, self.run_git('ls-remote', 'origin', self.ref).stdout)
        receipt.write_text('{')
        self.delete(code=1)
        self.receipt()
        self.stage('file', 'second\n')
        self.commit('second')
        self.record('committed')
        self.run_git('push', 'origin', 'HEAD:' + self.ref)
        self.assertIn('stale info', self.delete(code=1).stderr)
        self.receipt(code=1)
        self.assertNotEqual(self.oid, self.run_git('ls-remote', 'origin', self.ref).stdout.split()[0])

    def test_protected_default_tag_and_multiple_deletions_stay_blocked(self):
        config = self.root / '.config/pre-ship/config.toml'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('[push]\nallow_protected = true\nprotected_branches = ["main", "master", "keep"]\n')
        self.record('committed')
        for ref in ['main', 'keep']:
            self.run_git('push', 'origin', 'HEAD:refs/heads/' + ref)
            self.receipt(ref='refs/heads/' + ref, code=1)
        self.run_git('tag', '-a', '-m', 'release', 'v1.0.0')
        self.run_git('push', 'origin', 'refs/tags/v1.0.0')
        tag_ref = 'refs/tags/v1.0.0'
        tag_oid = self.run_git('rev-parse', tag_ref).stdout.strip()
        self.assertIn('non-branch', self.receipt(ref=tag_ref, oid=tag_oid, code=1).stderr)
        value = json.loads(self.receipt().stdout)
        receipt = next((self.root / '.cache/pre-ship').glob('*-deletion.json'))
        receipt.write_text(json.dumps(dict(value, ref=tag_ref, oid=tag_oid)))
        self.assertIn('non-branch', self.run_git('push', 'origin', ':' + tag_ref, code=1).stderr)
        self.receipt()
        self.run_git('--git-dir=' + str(self.remote), 'symbolic-ref', 'HEAD', self.ref)
        self.assertIn('default branch', self.delete(code=1).stderr)
        self.receipt(code=1)
        self.run_git('--git-dir=' + str(self.remote), 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.receipt()
        self.assertIn('Multiple refs', self.run_git('push', 'origin', ':' + self.ref, ':refs/heads/keep', code=1).stderr)
        self.assertIn('refs/tags/v1.0.0', self.run_git('ls-remote', 'origin').stdout)
        self.assertIn(self.ref, self.run_git('ls-remote', 'origin').stdout)

    def test_remote_aliases_rewrites_and_multiple_push_urls(self):
        other = self.root / 'other.git'
        self.run_git('init', '--bare', '-q', str(other))
        self.run_git('remote', 'add', 'other', str(other))
        self.record('committed')
        self.run_git('push', 'other', 'HEAD:' + self.ref)
        self.receipt()
        self.assertIn('deletion PASS', self.delete(remote='other', code=1).stderr)
        self.run_git('remote', 'set-url', '--add', '--push', 'origin', str(self.remote))
        self.run_git('remote', 'set-url', '--add', '--push', 'origin', str(other))
        self.receipt(code=1)
        self.run_git('config', 'url.' + str(other) + '.insteadOf', str(self.remote))
        self.run_git('push', str(self.remote), ':' + self.ref, code=1)
        self.assertIn(self.ref, self.run_git('ls-remote', 'other', self.ref).stdout)
