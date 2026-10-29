from test_pre_ship_git import GitHookFixture


class AgentCoauthorTests(GitHookFixture):
    def message(self, message, code=0):
        self.stage('file', 'clean\n')
        self.record()
        return self.run_git('commit', '--allow-empty', '-qsm', message, code=code)

    def test_codex_trailer_is_rejected(self):
        result = self.message('fixture\n\nCo-authored-by: Codex <noreply@openai.com>', code=1)
        self.assertIn('line 3', result.stderr)
        self.assertIn('git commit -s', result.stderr)
        self.assertIn('git commit --amend -s', result.stderr)

    def test_claude_trailer_is_rejected_case_insensitively(self):
        self.env.pop('AI_AGENT')
        result = self.message('fixture\n\nCO-AUTHORED-BY: Claude <NOREPLY@ANTHROPIC.COM>', code=1)
        self.assertIn('Agent co-author', result.stderr)

    def test_human_coauthors_are_preserved(self):
        for name in ['Claude', 'Devin', 'Jules']:
            with self.subTest(name=name):
                trailer = f'Co-authored-by: {name} <human@example.com>'
                self.message('fixture\n\n' + trailer)
                self.assertIn(trailer, self.run_git('log', '-1', '--format=%B').stdout)

    def test_message_without_coauthor_passes(self):
        self.message('fixture')

    def test_indented_coauthor_examples_pass(self):
        self.env.pop('AI_AGENT')
        for indent in [' ', '    ']:
            with self.subTest(indent=len(indent)):
                self.message('fixture\n\n' + indent + 'Co-authored-by: Codex <noreply@openai.com>')
