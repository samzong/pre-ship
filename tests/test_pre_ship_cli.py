import json
import shlex
import subprocess
import sys

from test_pre_ship_gate import SCRIPT
from test_pre_ship_git import GitHookFixture


class CLIContractTests(GitHookFixture):
    def command(self, *args, payload=None, code=0):
        result = subprocess.run([str(SCRIPT), *args], cwd=self.repo, env=self.env, input=payload, capture_output=True, text=True)
        self.assertEqual(code, result.returncode, result.stderr)
        return result

    def test_subcommand_usage_names_only_the_invoked_program_and_command(self):
        self.env['NO_COLOR'] = '1'
        for action in ('review', 'record', 'doctor', 'events', 'install', 'uninstall'):
            with self.subTest(action=action):
                usage = self.command(action, '--help').stdout.split('\n\n', 1)[0]
                self.assertRegex(usage, r'^usage: pre-ship ' + action + r'(?:\s|$)')
                self.assertNotIn('<command>', usage)
                self.assertNotIn('--version', usage)

    def test_bare_invocation_shows_help_without_reading_stdin_and_registered_hosts_use_hook(self):
        process = subprocess.Popen([str(SCRIPT)], cwd=self.repo, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(0, process.wait(timeout=3))
        finally:
            if process.poll() is None:
                process.kill()
            output, error = process.communicate()
        self.assertEqual(self.command('--help').stdout, output)
        self.assertEqual('', error)
        self.command('install')
        payload = json.dumps({'cwd': str(self.repo), 'tool_input': {'command': 'git commit --no-verify'}})
        for relative in ('.claude/settings.json', '.codex/hooks.json'):
            data = json.loads((self.root / relative).read_text())
            commands = [shlex.split(hook['command']) for entry in data['hooks']['PreToolUse'] for hook in entry['hooks']]
            self.assertEqual([[str(SCRIPT), 'hook']], commands)
            result = subprocess.run(commands[0], cwd=self.repo, env=self.env, input=payload, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('deny', json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'])

    def test_review_and_record_json_preserve_receipt_and_mode_binding(self):
        self.stage('file', 'content\n')
        self.commit()
        configuration = self.root / '.config/pre-ship/config.toml'
        configuration.parent.mkdir(parents=True)
        configuration.write_text('[review]\ncommand = ' + json.dumps([sys.executable, '-c', 'import json, sys; target = json.load(sys.stdin); assert target["mode"] in {"staged", "committed"}']) + '\n')
        for action in ('review', 'record'):
            for mode in ('staged', 'committed'):
                results = []
                for selection in ([mode], ['--mode', mode]):
                    value = json.loads(self.command(action, *selection, '--json').stdout)
                    self.assertEqual({'root', 'head', 'tree', 'mode', 'fingerprint', 'version', 'verdict', 'created_at'}, set(value))
                    self.assertEqual((2, 'PASS', mode, str(self.repo)), (value['version'], value['verdict'], value['mode'], value['root']))
                    receipt = next((self.root / '.cache/pre-ship').glob('????????????????.json'))
                    self.assertEqual(value, json.loads(receipt.read_text()))
                    self.assertEqual(64, len(value['fingerprint']))
                    self.assertIsInstance(value.pop('created_at'), (int, float))
                    results.append(value)
                self.assertEqual(*results)
        marker = 'Pre-Ship: UNVERIFIED — EXEMPTED'
        options = ['--verdict', 'EXEMPTED', '--reply', 'EXEMPT', '--reason', 'Missing native proof']
        result = self.command('record', '--json', *options)
        exempted = json.loads(result.stdout)
        self.assertEqual(('EXEMPTED', 'EXEMPT', 'Missing native proof'), (exempted['verdict'], exempted['reply'], exempted['reason']))
        self.assertIn(marker, result.stderr)
        result = self.command('record', *options)
        self.assertEqual(1, (result.stdout + result.stderr).count(marker))

    def test_diagnostics_json_preserve_summary_and_registration_fields(self):
        value = json.loads(self.command('doctor', '--json', code=1).stdout)
        self.assertEqual({'configuration', 'checks', 'scope'}, set(value))
        self.assertTrue(all({'component', 'status'} <= row.keys() for row in value['checks']))
        self.command('hook', payload=json.dumps({'cwd': str(self.repo), 'tool_input': {'command': 'git status'}}))
        value = json.loads(self.command('events', '--json').stdout)
        self.assertEqual({'counts', 'skipped'}, set(value))
        self.assertEqual([{'result': 'allow', 'reason': 'guard', 'count': 1}], value['counts'])
        self.assertEqual(0, value['skipped'])
        installed = json.loads(self.command('install', '--json').stdout)
        self.assertEqual({'configuration', 'checks', 'scope'}, set(installed))
        self.assertEqual(installed, json.loads(self.command('doctor', '--json').stdout))
        removed = json.loads(self.command('uninstall', '--json').stdout)
        self.assertEqual({'action', 'scope'}, set(removed))
        self.assertEqual('uninstall', removed['action'])

    def test_hook_entrypoints_keep_legacy_arguments_and_host_protocol(self):
        self.stage('file', 'content\n')
        self.record()
        for prefix in (['hook'], ['--cwd', str(self.repo), 'hook'], ['hook', '--cwd', str(self.repo)]):
            result = self.command(*prefix, payload=json.dumps({'cwd': str(self.repo), 'tool_input': {'command': 'git commit --no-verify'}}))
            self.assertEqual('deny', json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'])
        message = self.repo / '.git/COMMIT_EDITMSG'
        message.write_text('subject\n\nSigned-off-by: Fixture <fixture@example.invalid>\n')
        self.command('prepare-commit-msg', str(message), 'message', 'HEAD')
        self.command('commit-msg', str(message))
        applied = self.repo / '.git/rebase-apply/final-commit'
        applied.parent.mkdir()
        applied.write_text(message.read_text())
        self.command('pre-applypatch')
        self.run_git('commit', '-qsm', 'fixture')
        self.record('committed')
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.command('pre-push', 'origin', 'fixture://remote', payload=f'refs/heads/feat/probe {head} refs/heads/feat/probe {"0" * len(head)}\n')
