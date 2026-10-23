import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from test_pre_ship_gate import SCRIPT, gate
from test_pre_ship_git import GitHookFixture
from pre_ship import events


class EventLogTests(GitHookFixture):
    def test_interruption_and_uncaught_errors_never_log_allow_or_exempt(self):
        def interrupted(cwd, git, check, arguments):
            check(cwd, 'staged')
            raise failure

        for failure in [KeyboardInterrupt(), RuntimeError('private failure details')]:
            for verdict in ['PASS', 'EXEMPTED']:
                with self.subTest(failure=type(failure).__name__, verdict=verdict), patch('sys.argv', [str(SCRIPT), 'prepare-commit-msg', 'message']), patch.object(gate, 'agent_process', return_value=True), patch.object(gate, 'repository_root', return_value=str(self.repo)), patch.object(gate, 'check', return_value={'verdict': verdict}), patch.object(gate, 'prepare_commit', side_effect=interrupted), patch.object(gate, 'log_event') as log:
                    with self.assertRaises(type(failure)):
                        gate.main()
                    log.assert_called_once_with(str(self.repo), 'prepare-commit-msg', 'block', 'interrupted')

    def rows(self):
        path = self.root / '.cache' / 'pre-ship' / 'events.jsonl'
        return [json.loads(row) for row in path.read_text().splitlines()]

    def hook(self, command):
        payload = {'cwd': str(self.repo), 'tool_input': {'command': command}}
        result = subprocess.run([str(SCRIPT), 'hook'], input=json.dumps(payload), env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def exempt(self, mode='staged'):
        result = subprocess.run([str(SCRIPT), 'record', mode, '--verdict', 'EXEMPTED', '--reply', 'EXEMPT', '--reason', 'Native review pending'], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return Path(result.stdout.strip())

    def test_git_and_guard_events_never_copy_commands_findings_or_diffs(self):
        sentinel = 'private-command-value-0123456789'
        self.hook('echo ' + sentinel)
        denied = self.hook('git commit --no-verify -m ' + sentinel)
        self.assertEqual('deny', json.loads(denied.stdout)['hookSpecificOutput']['permissionDecision'])
        secret = 'AKIA' + 'QWERTYUIOPASDFGH'
        self.stage('credential.txt', 'aws_access_key_id = ' + secret + '\n')
        self.record()
        self.run_git('commit', '-sm', sentinel, code=1)
        rows = self.rows()
        self.assertEqual([('allow', 'guard'), ('block', 'guard'), ('block', 'secrets')], [(row['result'], row['reason']) for row in rows])
        self.assertEqual(str(self.repo.resolve()), rows[-1]['root'])
        raw = json.dumps(rows)
        for forbidden in [sentinel, secret, 'credential.txt', 'aws_access_key_id', '--no-verify']:
            self.assertNotIn(forbidden, raw)
        summary = subprocess.run([str(SCRIPT), 'events'], env=self.env, capture_output=True, text=True)
        self.assertEqual(0, summary.returncode, summary.stderr)
        self.assertEqual(3, sum(row['count'] for row in json.loads(summary.stdout)['counts']))

    def test_exempted_commit_and_push_are_distinct_and_mechanical_checks_remain(self):
        self.stage('file', 'first\n')
        self.exempt()
        self.run_git('commit', '-m', 'unsigned', code=1)
        self.run_git('commit', '-qsm', 'first')
        remote = self.root / 'remote.git'
        self.run_git('init', '--bare', '-q', str(remote))
        self.exempt('committed')
        self.run_git('push', str(remote), 'HEAD:refs/heads/main', code=1)
        self.run_git('push', str(remote), 'HEAD:refs/heads/feat/probe')
        rows = self.rows()
        self.assertEqual(['block', 'exempt', 'exempt', 'block', 'exempt'], [row['result'] for row in rows])
        self.assertEqual(['signoff', 'receipt_exempted', 'receipt_exempted', 'push_policy', 'receipt_exempted'], [row['reason'] for row in rows])
        self.assertNotIn('Native review pending', json.dumps(rows))
        for content, category in [('space \n', 'whitespace'), ('x' * 20001, 'size'), ('aws_access_key_id = AKIA' + 'QWERTYUIOPASDFGH\n', 'secrets')]:
            with self.subTest(category=category):
                self.stage('file', content)
                self.exempt()
                self.run_git('commit', '-sm', category, code=1)
                self.assertEqual(('block', category), (self.rows()[-1]['result'], self.rows()[-1]['reason']))

    def test_log_failure_does_not_change_commit_push_or_guard_decisions(self):
        self.stage('file', 'first\n')
        self.record()
        path = self.root / '.cache' / 'pre-ship' / 'events.jsonl'
        path.mkdir()
        self.run_git('commit', '-qsm', 'first')
        remote = self.root / 'remote.git'
        self.run_git('init', '--bare', '-q', str(remote))
        self.record('committed')
        self.run_git('push', str(remote), 'HEAD:refs/heads/feat/probe')
        self.assertEqual('', self.hook('git status').stdout)
        self.assertEqual('deny', json.loads(self.hook('git push --no-verify').stdout)['hookSpecificOutput']['permissionDecision'])

    def test_event_rotation_bounds_retention_and_summary_ignores_partial_rows(self):
        path = self.root / 'events.jsonl'
        with patch.object(events, 'event_path', return_value=path), patch.object(events, 'LIMIT', 240):
            for _ in range(20):
                events.log_event('/repo', 'pre-push', 'exempt', 'receipt_exempted')
            self.assertLessEqual(path.stat().st_size, 240)
            self.assertLessEqual(path.with_suffix('.jsonl.1').stat().st_size, 240)
            with path.open('a') as output:
                output.write('{broken\n')
            summary = events.event_summary()
            self.assertEqual(1, summary['skipped'])
            self.assertTrue(summary['counts'])
            self.assertTrue(all(row['result'] == 'exempt' for row in summary['counts']))


if __name__ == '__main__':
    unittest.main()
