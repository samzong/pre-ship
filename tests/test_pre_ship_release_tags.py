import subprocess
import unittest

import test_pre_ship_git as fixtures


class ReleaseTagTests(fixtures.GitHookFixture):
    def git_at(self, path, *args, code=0):
        result = subprocess.run([self.git_bin, '-C', str(path), *args], env=dict(self.env, GIT_CONFIG_GLOBAL='/dev/null'), capture_output=True, text=True)
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)
        return result

    def bare_remote(self, name='origin', seed_ref='refs/heads/main', seed_repo=None):
        remote = self.root / f'{name}.git'
        self.git_at(self.root, 'init', '--bare', '-q', str(remote))
        if seed_repo is not None:
            self.git_at(remote, 'fetch', '-q', str(seed_repo), f'HEAD:{seed_ref}')
        self.git_at(remote, 'symbolic-ref', 'HEAD', seed_ref)
        self.run_git('remote', 'add', name, str(remote))
        return remote

    def remote_ref(self, remote, ref):
        result = self.git_at(remote, 'for-each-ref', '--format=%(objectname)', ref)
        return result.stdout.strip() or None

    def assert_rejected_ref(self, remote, ref, spec, keyword, before=None):
        previous = self.remote_ref(remote, ref) if before is None else before
        result = self.run_git('push', 'origin', spec, code=1)
        self.assertEqual(previous, self.remote_ref(remote, ref))
        self.assertIn(keyword.lower(), result.stderr.lower())
        return result.stderr

    def test_new_annotated_release_tags_without_receipts_allow_tip_and_ancestor(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_repo=self.repo)
        first = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v1.0.0', first)
        self.run_git('push', 'origin', 'refs/tags/v1.0.0')
        self.assertEqual(self.run_git('rev-parse', 'v1.0.0').stdout.strip(), self.remote_ref(remote, 'refs/tags/v1.0.0'))

        self.stage('file', 'second\n')
        self.commit('second')
        self.git_at(remote, 'fetch', '-q', str(self.repo), 'HEAD:refs/heads/main')
        self.run_git('tag', '-a', '-m', 'release', 'v1.0.1-beta.13', first)
        self.run_git('checkout', '--detach', '-q')
        self.run_git('push', str(remote), 'refs/tags/v1.0.1-beta.13')
        self.assertEqual(self.run_git('rev-parse', 'v1.0.1-beta.13').stdout.strip(), self.remote_ref(remote, 'refs/tags/v1.0.1-beta.13'))

    def test_pre_push_rejects_non_release_tag_updates_without_remote_changes(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_repo=self.repo)
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()

        self.run_git('tag', 'v1.1.0')
        with self.subTest(tag='lightweight'):
            self.assert_rejected_ref(remote, 'refs/tags/v1.1.0', 'refs/tags/v1.1.0', 'annotated')
        self.run_git('tag', '-d', 'v1.1.0')

        for tag in ['v01.2.3', 'v1.2.3-', 'v1.2.3-01', 'v1.2.3+build.1', 'release-1.2.3']:
            with self.subTest(tag=tag):
                self.run_git('tag', '-a', '-m', 'release', tag, head)
                self.assert_rejected_ref(remote, f'refs/tags/{tag}', f'refs/tags/{tag}', 'SemVer')
                self.run_git('tag', '-d', tag)

    def test_pre_push_rejects_moving_existing_release_tag(self):
        self.stage('file', 'first\n')
        self.commit('first')
        first = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v2.0.0', first)
        remote = self.bare_remote(seed_repo=self.repo)
        self.git_at(remote, 'fetch', '-q', str(self.repo), 'refs/tags/v2.0.0:refs/tags/v2.0.0')
        remote_tag = self.remote_ref(remote, 'refs/tags/v2.0.0')
        self.assertIsNotNone(remote_tag)
        self.stage('file', 'second\n')
        self.commit('second')
        second = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.git_at(remote, 'fetch', '-q', str(self.repo), 'HEAD:refs/heads/main')
        self.run_git('tag', '-fa', '-m', 'moved release', 'v2.0.0', second)
        self.assert_rejected_ref(remote, 'refs/tags/v2.0.0', '+refs/tags/v2.0.0:refs/tags/v2.0.0', 'existing', before=remote_tag)

    def test_pre_push_rejects_unmerged_release_tag_and_fake_tracking_tip(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_repo=self.repo)
        self.stage('file', 'unmerged\n')
        self.commit('unmerged')
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v3.0.0', head)
        with self.subTest(tracking_ref='actual remote tip'):
            self.assert_rejected_ref(remote, 'refs/tags/v3.0.0', 'refs/tags/v3.0.0', 'ancestor')

        self.run_git('update-ref', 'refs/remotes/origin/main', head)
        self.run_git('tag', '-a', '-m', 'release', 'v3.0.1', head)
        with self.subTest(tracking_ref='forged local origin/main'):
            self.assert_rejected_ref(remote, 'refs/tags/v3.0.1', 'refs/tags/v3.0.1', 'ancestor')

        self.git_at(remote, 'fetch', '-q', str(self.repo), 'HEAD:refs/heads/master')
        self.run_git('tag', '-a', '-m', 'release', 'v3.0.2', head)
        with self.subTest(tracking_ref='master contains tag but main does not'):
            self.assert_rejected_ref(remote, 'refs/tags/v3.0.2', 'refs/tags/v3.0.2', 'ancestor')

    def test_pre_push_queries_actual_push_url_instead_of_fetch_url(self):
        self.stage('file', 'first\n')
        self.commit('first')
        destination = self.bare_remote(seed_repo=self.repo)
        self.stage('file', 'unmerged at destination\n')
        self.commit('unmerged at destination')
        fetch_remote = self.bare_remote(name='fetch', seed_repo=self.repo)
        self.run_git('config', 'remote.origin.url', str(fetch_remote))
        self.run_git('config', 'remote.origin.pushurl', str(destination))
        self.run_git('tag', '-a', '-m', 'release', 'v3.1.0')
        self.assert_rejected_ref(destination, 'refs/tags/v3.1.0', 'refs/tags/v3.1.0', 'ancestor')
        self.assertIsNone(self.remote_ref(fetch_remote, 'refs/tags/v3.1.0'))

    def test_pre_push_rejects_replaced_local_ancestry(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_repo=self.repo)
        tip = self.run_git('rev-parse', 'HEAD').stdout.strip()
        tree = self.run_git('rev-parse', 'HEAD^{tree}').stdout.strip()
        unmerged = self.run_git('commit-tree', tree, '-m', 'unmerged orphan').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v3.1.1', unmerged)
        self.run_git('replace', '--graft', tip, unmerged)
        self.assert_rejected_ref(remote, 'refs/tags/v3.1.1', 'refs/tags/v3.1.1', 'ancestor')

    def test_pre_push_rejects_url_rewriting_after_push_instead_of(self):
        self.stage('file', 'first\n')
        self.commit('first')
        destination = self.bare_remote(seed_repo=self.repo)
        self.stage('file', 'unmerged at destination\n')
        self.commit('unmerged at destination')
        fake = self.bare_remote(name='fake', seed_repo=self.repo)
        self.run_git('tag', '-a', '-m', 'release', 'v1.5.0')
        overrides = ['-c', 'remote.p2.url=fixture-target', '-c', f'url.{destination}.pushInsteadOf=fixture-target', '-c', f'url.{fake}.insteadOf={destination}']
        resolved = self.run_git(*overrides, 'ls-remote', '--get-url', '--', str(destination)).stdout.removesuffix('\n')
        self.assertEqual(str(fake), resolved)
        result = subprocess.run([self.git_bin, '-C', str(self.repo), *overrides, 'push', 'p2', 'refs/tags/v1.5.0'], env=self.env, capture_output=True, text=True)
        self.assertIsNone(self.remote_ref(destination, 'refs/tags/v1.5.0'), result.stdout + result.stderr)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn('URL rewriting is active for the push target', result.stderr)
        self.assertIn('ancestry cannot be proven', result.stderr)

    def test_pre_push_selects_exact_default_ref_among_tail_matches(self):
        self.stage('file', 'first\n')
        self.commit('first')
        for index, branch in enumerate(['main', 'master']):
            with self.subTest(branch=branch):
                remote = self.bare_remote(name=branch, seed_ref=f'refs/heads/{branch}', seed_repo=self.repo)
                for suffix in ['main', 'master']:
                    self.git_at(remote, 'fetch', '-q', str(self.repo), f'HEAD:refs/heads/x/refs/heads/{suffix}')
                tag = f'v1.6.{index}'
                self.run_git('tag', '-a', '-m', 'release', tag)
                self.run_git('push', branch, f'refs/tags/{tag}')
                self.assertEqual(self.run_git('rev-parse', tag).stdout.strip(), self.remote_ref(remote, f'refs/tags/{tag}'))

    def test_pre_push_rejects_non_commit_tag_and_missing_default_branch(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote()
        self.run_git('tag', '-a', '-m', 'release', 'v3.2.0', 'HEAD^{tree}')
        self.assert_rejected_ref(remote, 'refs/tags/v3.2.0', 'refs/tags/v3.2.0', 'commit')
        self.run_git('tag', '-a', '-m', 'release', 'v3.2.1')
        self.assert_rejected_ref(remote, 'refs/tags/v3.2.1', 'refs/tags/v3.2.1', 'main or master')

    def test_pre_push_uses_master_fallback_when_main_is_absent(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_ref='refs/heads/master', seed_repo=self.repo)
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v4.0.0', head)
        self.run_git('push', 'origin', 'refs/tags/v4.0.0')
        self.assertEqual(self.run_git('rev-parse', 'v4.0.0').stdout.strip(), self.remote_ref(remote, 'refs/tags/v4.0.0'))

    def test_pre_push_rejects_missing_local_remote_tip_and_prompts_fetch(self):
        seed = self.root / 'seed'
        seed.mkdir()
        self.git_at(seed, 'init', '-q', '-b', 'main')
        self.git_at(seed, 'config', 'user.name', 'Fixture')
        self.git_at(seed, 'config', 'user.email', 'fixture@example.invalid')
        (seed / 'file').write_text('remote only\n')
        self.git_at(seed, 'add', 'file')
        self.git_at(seed, 'commit', '-q', '-m', 'remote only')
        remote = self.bare_remote(seed_repo=seed)
        self.stage('local', 'local\n')
        self.commit('local')
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v5.0.0', head)
        self.assert_rejected_ref(remote, 'refs/tags/v5.0.0', 'refs/tags/v5.0.0', 'fetch')

    def test_pre_push_rejects_multiple_and_deleted_tag_refs(self):
        self.stage('file', 'first\n')
        self.commit('first')
        remote = self.bare_remote(seed_repo=self.repo)
        head = self.run_git('rev-parse', 'HEAD').stdout.strip()
        self.run_git('tag', '-a', '-m', 'release', 'v6.0.0', head)
        self.git_at(remote, 'fetch', '-q', str(self.repo), 'refs/tags/v6.0.0:refs/tags/v6.0.0')
        before = self.remote_ref(remote, 'refs/tags/v6.0.0')
        self.run_git('tag', '-a', '-m', 'release', 'v6.0.1', head)
        result = self.run_git('push', 'origin', 'refs/tags/v6.0.1', 'HEAD:refs/heads/feature', code=1)
        self.assertEqual(before, self.remote_ref(remote, 'refs/tags/v6.0.0'))
        self.assertIsNone(self.remote_ref(remote, 'refs/tags/v6.0.1'))
        self.assertIsNone(self.remote_ref(remote, 'refs/heads/feature'))
        self.assertIn('multiple refs', result.stderr.lower())
        self.assert_rejected_ref(remote, 'refs/tags/v6.0.0', ':refs/tags/v6.0.0', 'deletion', before=None)


if __name__ == '__main__':
    unittest.main()
