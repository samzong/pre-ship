import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pre_ship import cli as gate

SCRIPT = Path(os.environ.get('PRE_SHIP_TEST_CLI', Path(__file__).resolve().parents[1] / 'bin' / 'pre-ship'))

def isolated_environment(root):
    markers = {'CLAUDECODE', 'AI_AGENT', 'CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID'}
    env = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'PIPX_')) and key not in markers}
    env.update(HOME=str(root), CODEX_HOME=str(root / '.codex'), XDG_CONFIG_HOME=str(root / '.config'), XDG_CACHE_HOME=str(root / '.cache'), XDG_DATA_HOME=str(root / '.local/share'), XDG_STATE_HOME=str(root / '.local/state'), GIT_CONFIG_GLOBAL=str(root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1', AI_AGENT='fixture')
    return env


class PreShipGateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo = self.root / 'repo with spaces'
        self.repo.mkdir()
        self.env = isolated_environment(self.root)
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.receipts = patch.object(gate, 'receipt_path', lambda root: self.root / (gate.hashlib.sha256(root.encode()).hexdigest() + '.json'))
        self.receipts.start()
        self.addCleanup(self.receipts.stop)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        (self.repo / 'file').write_text('base\n')
        self.git('add', 'file')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env, check=True, capture_output=True, text=True).stdout

    def test_initial_staged_and_root_committed_reviews_track_content(self):
        unborn = gate.target(self.repo, 'staged')
        self.assertEqual('UNBORN', unborn['head'])
        gate.record(self.repo, 'staged')
        gate.check(self.repo, 'staged')
        self.git('commit', '-qsm', 'base')
        committed = gate.target(self.repo, 'committed')
        self.assertEqual(unborn['fingerprint'], committed['fingerprint'])
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'committed')
        gate.record(self.repo, 'committed')
        gate.check(self.repo, 'committed')
        self.git('commit', '--amend', '--no-edit', '-q', '--date=2000-01-01T00:00:00Z')
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'committed')

    def test_staged_receipt_survives_unstaged_edits_but_not_index_changes(self):
        path = gate.record(self.repo, 'staged')
        (self.repo / 'file').write_text('unstaged\n')
        gate.check(self.repo, 'staged')
        self.git('add', 'file')
        path.touch()
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'staged')
        path.write_text('')
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'staged')

    def test_receipt_checks_all_authorization_fields_and_age(self):
        changes = [{'version': 1}, {'created_at': gate.time.time() - gate.AGE - 1}, {'created_at': gate.time.time() + 60}, {'head': '0' * 40}, {'tree': '0' * 40}, {'fingerprint': '0' * 64}, {'root': '/another'}, {'verdict': 'BLOCK'}, {'mode': 'committed'}]
        for verdict in ['PASS', 'EXEMPTED']:
            options = {'verdict': verdict, **({'reply': 'EXEMPT', 'reason': 'Native review pending'} if verdict == 'EXEMPTED' else {})}
            for change in changes + ([{'reply': 'yes'}, {'reason': ''}] if verdict == 'EXEMPTED' else []):
                with self.subTest(verdict=verdict, change=change):
                    path = gate.record(self.repo, 'staged', **options)
                    data = json.loads(path.read_text())
                    data.update(change)
                    path.write_text(json.dumps(data))
                    path.touch()
                    with self.assertRaises(ValueError):
                        gate.check(self.repo, 'staged')

    def test_receipts_do_not_cross_repositories_but_accept_child_directory(self):
        gate.record(self.repo, 'staged')
        child = self.repo / 'child'
        child.mkdir()
        gate.check(child, 'staged')
        other = self.root / 'other'
        other.mkdir()
        subprocess.run(['git', '-C', str(other), 'init', '-q'], check=True)
        with self.assertRaises(ValueError):
            gate.check(other, 'staged')

    def test_cli_creates_receipts_only_when_recording(self):
        home = self.root / 'home'
        home.mkdir()
        env = isolated_environment(home)
        result = subprocess.run([str(SCRIPT), 'prepare-commit-msg', '.git/COMMIT_EDITMSG'], cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual([], list((home / '.cache' / 'pre-ship').glob('????????????????.json')))
        result = subprocess.run([str(SCRIPT), 'record'], cwd=self.repo, env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(Path(result.stdout.strip()).is_file())
        result = subprocess.run([str(SCRIPT), 'record'], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)

    def test_exemption_requires_literal_reply_and_reason_and_preserves_binding(self):
        for reply, reason in [('yes', 'Native review pending'), ('EXEMPT', ''), ('EXEMPT', '  ')]:
            with self.subTest(reply=reply, reason=reason), self.assertRaises(ValueError):
                gate.record(self.repo, 'staged', verdict='EXEMPTED', reply=reply, reason=reason)
        path = gate.record(self.repo, 'staged', verdict='EXEMPTED', reply='EXEMPT', reason='Native review pending')
        data = json.loads(path.read_text())
        self.assertEqual('EXEMPTED', data['verdict'])
        self.assertEqual('Native review pending', data['reason'])
        self.assertEqual('EXEMPTED', gate.check(self.repo, 'staged')['verdict'])
        data['created_at'] -= gate.AGE + 1
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'staged')
        gate.record(self.repo, 'staged', verdict='EXEMPTED', reply='EXEMPT', reason='Native review pending')
        (self.repo / 'file').write_text('changed\n')
        self.git('add', 'file')
        with self.assertRaises(ValueError):
            gate.check(self.repo, 'staged')

    def test_pre_push_rejects_malformed_or_duplicate_default_ref_rows(self):
        self.git('commit', '-qsm', 'base')
        self.git('tag', '-a', '-m', 'release', 'v1.0.0')
        tag = self.git('rev-parse', 'v1.0.0').strip()
        tip = self.git('rev-parse', 'HEAD').strip()
        url = 'fixture://remote'
        data = f'refs/tags/v1.0.0 {tag} refs/tags/v1.0.0 {"0" * len(tag)}\n'
        outputs = [b'malformed\n', f'{tip}\trefs/heads/main\n{tip}\trefs/heads/main\n'.encode(), f'{tip}\trefs/heads/main\ninvalid\trefs/heads/x/refs/heads/main\n'.encode()]
        for advertised in outputs:
            with self.subTest(advertised=advertised):
                def git(cwd, *args):
                    if args[:2] == ('ls-remote', '--get-url'):
                        return f'{url}\n'.encode()
                    if args[0] == 'ls-remote':
                        return advertised
                    return gate.git(cwd, *args)

                with self.assertRaisesRegex(ValueError, 'invalid tip|duplicate tips'):
                    gate.push_refs(self.repo, git, gate.check, data, ['origin', url])


if __name__ == '__main__':
    unittest.main()
