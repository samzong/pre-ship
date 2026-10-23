import subprocess
import unittest

from test_pre_ship_git import GitHookFixture


class WhitespaceMergeTests(GitHookFixture):
    def historical_branches(self, count):
        self.stage('base', 'base\n')
        self.commit('base')
        base = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('config', 'core.whitespace', '-trailing-space')
        self.historical_content = '+pretend:42: source \v \n\n'
        branches = []
        for index in range(count):
            branch = f'feat/history-{index}'
            self.run_git('checkout', '-qb', branch, base)
            self.stage(f'+history:{index}', self.historical_content)
            self.commit('historical whitespace')
            branches.append(branch)
        self.run_git('checkout', '-q', 'feat/probe')
        self.run_git('config', 'core.whitespace', 'trailing-space')
        self.stage('target', 'target\n')
        self.commit('target')
        return branches

    def merge_history(self, count):
        branches = self.historical_branches(count)
        self.run_git('merge', '--no-ff', '--no-commit', *branches)
        self.assertNotEqual(0, subprocess.run([self.git_bin, '-C', str(self.repo), 'diff', '--cached', '--check'], env=self.env, capture_output=True).returncode)
        self.record()
        self.run_git('commit', '-qs', '--no-edit')
        self.assertEqual(count + 2, len(self.run_git('rev-list', '--parents', '-n1', 'HEAD').stdout.split()))
        for index in range(count):
            self.assertEqual(self.historical_content, self.run_git('show', f'HEAD:+history:{index}').stdout)

    def test_merge_commits_whitespace_already_in_other_parent(self):
        self.merge_history(1)

    def test_octopus_merge_checks_every_parent(self):
        self.merge_history(2)

    def test_conflict_resolution_new_whitespace_blocks_merge(self):
        self.stage('file', 'base\n')
        self.commit('base')
        self.run_git('checkout', '-qb', 'feat/source')
        self.stage('file', 'source\n')
        self.commit('source')
        self.run_git('checkout', '-q', 'feat/probe')
        self.stage('file', 'target\n')
        self.commit('target')
        self.run_git('merge', '--no-commit', 'feat/source', code=1)
        self.stage('file', 'new resolution \n')
        self.record()
        self.assertIn('Staged whitespace check failed', self.run_git('commit', '-qs', '--no-edit', code=1).stderr)
        self.stage('file', 'clean resolution\n')
        self.commit('resolved')


class WhitespaceReplayTests(GitHookFixture):
    def replay(self, operation):
        self.stage('base', 'base\n')
        self.commit('base')
        self.run_git('checkout', '-qb', 'feat/source')
        self.run_git('config', 'core.whitespace', '-trailing-space')
        self.stage('source', 'replayed space \n')
        self.commit('source whitespace')
        source = self.run_git('rev-parse', 'HEAD').stdout.strip()
        patch = self.root / 'source.patch'
        patch.write_text(self.run_git('format-patch', '--stdout', '-1', 'HEAD').stdout)
        self.run_git('checkout', '-q', 'feat/probe')
        self.run_git('config', 'core.whitespace', 'trailing-space')
        self.stage('target', 'target\n')
        self.commit('target')
        arguments = {'cherry-pick': [source], 'am': [str(patch)], 'rebase': ['feat/probe']}[operation]
        if operation == 'rebase':
            self.run_git('checkout', '-q', 'feat/source')
        self.env['GIT_EDITOR'] = 'true'
        initial = subprocess.run([self.git_bin, '-C', str(self.repo), operation, *arguments], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(0, initial.returncode, initial.stdout + initial.stderr)
        self.assertIn('PASS receipt', initial.stderr)
        self.record()
        resume = ['commit', '-s', '--no-edit'] if operation == 'rebase' else [operation, '--continue']
        resumed = subprocess.run([self.git_bin, '-C', str(self.repo), *resume], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertIn('Staged whitespace check failed', resumed.stderr)
        self.stage('source', 'corrected replay\n')
        self.record()
        if operation == 'rebase':
            self.run_git('commit', '-qs', '--no-edit')
        else:
            self.run_git(operation, '--continue')
        self.assertEqual('corrected replay\n', self.run_git('show', 'HEAD:source').stdout)

    def test_cherry_pick_stops_replayed_whitespace(self):
        self.replay('cherry-pick')

    def test_rebase_stops_replayed_whitespace(self):
        self.replay('rebase')

    def test_am_stops_replayed_whitespace(self):
        self.replay('am')


if __name__ == '__main__':
    unittest.main()
