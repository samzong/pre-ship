import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_pre_ship_gate import SCRIPT, gate, isolated_environment


class AgentBypassTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.cwd = Path(self.scratch.name).resolve()
        self.env = isolated_environment(self.cwd)
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_git_hooks_own_receipts_and_final_index_checks(self):
        for command in ['git commit -sm value', 'git commit -qmnone', 'git add file && git commit -asm value', 'gmc -ay', 'git push --all origin', 'git push origin other', 'git status', 'echo git commit --no-verify', 'git commit -m --no-verify', 'git push -o --no-verify origin HEAD']:
            with self.subTest(command=command):
                gate.guard(command, self.cwd)

    def test_supported_wrappers_block_explicit_bypasses(self):
        wrappers = ['git {args}', '/usr/bin/git {args}', 'rtk git {args}', 'rtk -vv proxy git {args}', 'env TEST=1 command git {args}', "rtk run 'git {args}'", "rtk proxy 'git {args}'", "rtk run -c 'git {args}'", "rtk summary --shell sh 'git {args}'", "sh -c 'git {args}'"]
        bypasses = ['commit --no-verify -m value', 'commit -qns -m value', 'push --no-verify origin HEAD', 'am --no-verify patch', 'am -cn patch', '-c core.hooksPath=/tmp commit -sm value', '-chook.gate.enabled=false commit -sm value', '-c hook.pre-push.enabled=0 push origin HEAD', '--config-env=core.hooksPath=EMPTY commit -sm value']
        for wrapper in wrappers:
            for args in bypasses:
                with self.subTest(wrapper=wrapper, args=args), self.assertRaises(ValueError):
                    gate.guard(wrapper.format(args=args), self.cwd)
        gate.guard('git push -n origin HEAD', self.cwd)
        with self.assertRaises(ValueError):
            gate.guard('gmc --no-verify -y', self.cwd)

    def test_environment_and_persistent_configuration_bypasses_are_blocked(self):
        for command in ['GIT_CONFIG_GLOBAL=/dev/null git commit -s', 'env GIT_CONFIG_COUNT=1 git push', 'env -i GIT_CONFIG_GLOBAL=/dev/null git commit -s', 'export GIT_CONFIG_GLOBAL=/dev/null; git commit -s', 'GIT_INDEX_FILE=/tmp/index git commit -s', 'git config --global hook.gate.enabled false', 'git config --unset core.hooksPath', 'git -c include.path=/tmp/disabled commit -s', 'rtk run -c "GIT_CONFIG_SYSTEM=/dev/null git commit -s"']:
            with self.subTest(command=command), self.assertRaises(ValueError):
                gate.guard(command, self.cwd)
        gate.guard('git config --get hook.gate.enabled', self.cwd)
        gate.guard('git -c core.pager=cat commit -sm value', self.cwd)

    def test_config_reads_use_the_action_and_positional_arguments(self):
        commands = ['git config --global core.hooksPath', 'git config --show-origin hook.gate.command', 'git config --get hook.gate.event list', 'git config --get-regexp hook.gate list', 'git config get --global hook.gate.event', 'git config list --global', 'git config --type path core.hooksPath', 'git config --default list core.hooksPath', 'git config -f /tmp/config core.hooksPath', 'git config --get-color hook.gate.color blue']
        for command in commands:
            with self.subTest(command=command):
                gate.guard(command, self.cwd)

    def test_config_values_cannot_turn_protected_writes_into_reads(self):
        for value in ['get', 'list', '--get', '--get-all', '--get-regexp', '--list', '-l']:
            for prefix in ['git config --global', 'git config set --global']:
                command = prefix + ' hook.gate.event ' + value
                with self.subTest(command=command), self.assertRaises(ValueError):
                    gate.guard(command, self.cwd)
        for command in ['git config --default get hook.gate.event list', 'git config --comment list hook.gate.event value', 'git config unset --value list hook.gate.event', 'git config --unset hook.gate.event list', 'git config --get --unset hook.gate.event', 'git config rename-section hook.gate get', 'git config remove-section hook.gate', 'git config --remove-section core', 'git config rename-section include get', 'git config rename-section other alias', 'git config --unknown hook.gate.event list']:
            with self.subTest(command=command), self.assertRaises(ValueError):
                gate.guard(command, self.cwd)
        gate.guard('git config user.name hook.gate', self.cwd)
        gate.guard('git config set user.name hook.gate', self.cwd)

    def test_config_hook_decisions_match_real_git_arguments(self):
        config = self.cwd / 'config'
        for prefix in [[], ['set']]:
            for value in ['list', '--get', '--list']:
                args = ['git', 'config', *prefix, '--file', str(config), 'hook.gate.event', value]
                subprocess.run(args, check=True, capture_output=True, text=True)
                read = ['git', 'config', '--file', str(config), 'hook.gate.event']
                result = subprocess.run(read, check=True, capture_output=True, text=True)
                self.assertEqual(value, result.stdout.strip())
                for command, denied in [(shlex.join(args), True), (shlex.join(read), False)]:
                    with self.subTest(command=command):
                        payload = {'cwd': str(self.cwd), 'tool_input': {'command': command}}
                        result = subprocess.run([str(SCRIPT), 'hook'], input=json.dumps(payload), capture_output=True, text=True)
                        self.assertEqual(0, result.returncode)
                        if denied:
                            self.assertEqual('deny', json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'])
                        else:
                            self.assertEqual('', result.stdout)

    def test_home_alias_and_send_pack_cannot_skip_git_hooks(self):
        for command in ['HOME=/tmp git commit -s', 'XDG_CONFIG_HOME=/tmp git push', 'env -u HOME git push', 'env --unset=HOME git push', 'env -u XDG_CONFIG_HOME git push', 'HOME=/tmp rtk git push', 'git -c alias.p=push p', 'git -calias.p=push p', 'git send-pack origin HEAD']:
            with self.subTest(command=command), self.assertRaises(ValueError):
                gate.guard(command, self.cwd)
        gate.guard('HOME=/tmp python fixture.py', self.cwd)

    def test_heredoc_body_and_option_values_are_data(self):
        command = "python - <<'PY'\nprint('git commit --no-verify')\nPY\n"
        gate.guard(command, self.cwd)
        with self.assertRaises(ValueError):
            gate.guard(command + 'git commit --no-verify', self.cwd)
        for wrapper in ['rtk run', 'rtk err', 'rtk summary', 'rtk test']:
            with self.subTest(wrapper=wrapper), self.assertRaises(ValueError):
                gate.guard(wrapper + ' ' + shlex.quote('git commit --no-verify'), self.cwd)
        gate.guard('rtk proxy ' + shlex.quote('git status; git commit --no-verify'), self.cwd)

    def test_env_unset_forms_cannot_remove_git_configuration_home(self):
        for variable in ['HOME', 'XDG_CONFIG_HOME']:
            for option in ['-u ' + variable, '-u' + variable, '--unset=' + variable]:
                with self.subTest(option=option), self.assertRaises(ValueError):
                    gate.guard('env ' + option + ' git push', self.cwd)
        gate.guard('env -uUNRELATED git status', self.cwd)
        gate.guard('env --unset=UNRELATED git status', self.cwd)

    def test_hook_entrypoint_denies_without_agent_markers(self):
        os.environ.pop('AI_AGENT')
        payload = {'cwd': str(self.cwd), 'tool_input': {'command': 'git commit --no-verify'}}
        result = subprocess.run([str(SCRIPT), 'hook'], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        self.assertEqual('deny', json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'])
        payload['tool_input']['command'] = "*** Begin Patch\n*** Update File: example\n+Owner's rule\n*** End Patch"
        result = subprocess.run([str(SCRIPT), 'hook'], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual('', result.stdout)
        payload['tool_input']['command'] += '\ngit commit --no-verify'
        result = subprocess.run([str(SCRIPT), 'hook'], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual('deny', json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'])

    def test_env_clearing_and_option_arguments_do_not_hide_git(self):
        for options in ['-i', '-iuHOME', '-', '-C /tmp -uHOME', '--ignore-environment', '-viuXDG_CONFIG_HOME', '-vC/tmp -uHOME', '-P /usr/bin -uHOME', '-P/usr/bin -uHOME', '--chdir /tmp -uHOME', '--chdir=/tmp -uHOME', '-a git -uHOME', '--argv0=git -uHOME', '--block-signal=PIPE -uHOME', '--ignore-signal -uHOME', '--default-signal=INT -uHOME', '--list-signal-handling -uHOME', '-- -']:
            for executable in ['env', '/usr/bin/env', 'genv']:
                with self.subTest(executable=executable, options=options), self.assertRaises(ValueError):
                    gate.guard(executable + ' ' + options + ' git push', self.cwd)
        for command in ['env FOO=1 git status', 'env -u UNRELATED git status', 'env -vuUNRELATED git status', 'env -C /tmp git status', 'env -P /usr/bin git status', 'env --chdir=/tmp git status', 'env -a git python fixture.py', 'env --argv0 git python fixture.py', 'env -u iHOME git status', 'env --block-signal=PIPE git status', 'env --default-signal git status', 'env -i python fixture.py']:
            with self.subTest(command=command):
                gate.guard(command, self.cwd)
        for options in ['-vS', '--split-string', '--unknown', '-C', '--argv0']:
            with self.subTest(options=options), self.assertRaises(ValueError):
                gate.guard('env ' + options, self.cwd)

    def test_env_assignments_with_non_shell_names_do_not_hide_git(self):
        commands = ['env -i 1=1 git push', 'env - 9=9 git push', 'env -u HOME A.B=1 git push', 'env -i "a b=c" git push', 'env -i A=1 -x=1 git push', 'env -i .=x git push', 'env -i A-B=1 git push', 'genv -- - --unset=HOME git push', 'genv -- - --chdir=/tmp git push', 'genv -- - --argv0=x git push', 'genv -- - --uns=HOME git push']
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                gate.guard(command, self.cwd)
        for command in ['env FOO=1 git status', 'env -i "PATH=$PATH" uv run pytest', 'env -C /tmp git status', '1=1 git push', 'A.B=1 git push']:
            with self.subTest(command=command):
                gate.guard(command, self.cwd)


if __name__ == '__main__':
    unittest.main()
