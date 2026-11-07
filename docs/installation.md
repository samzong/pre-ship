# Installation and diagnostics

Install once for all repositories with the [README commands](../README.md#install).
Installation adds four global Git hooks and PreToolUse entries for Claude Code and Codex.
It preserves unrelated Git and host configuration.
It leaves `core.hooksPath` available for pre-commit and other tools.
See the [versioned Git hook documentation](https://git-scm.com/docs/git-hook/2.54.0.html) for the configuration format.

## Helper path

`install` requires an executable named `pre-ship` outside the uv and pipx run caches.
Do not run installation through `uvx`, `pipx run`, or `python -m pre_ship`.
Use `uv tool install` first. Rejected helpers leave all configuration unchanged.

Installation registers the absolute invocation path without resolving symlinks.
Keep that path available after upgrades.
Symlinks and their targets identify the same helper for doctor, uninstall, and conflict checks.
Reinstallation updates owned Git, Claude Code, and Codex entries to the current invocation path.
Installation does not change shared hook enable flags or take over an existing gate helper.

## Host registration

Codex reads `~/.codex/hooks.json`, or `$CODEX_HOME/hooks.json` when set.
In Codex, open `/hooks` and trust the new command.
Claude Code reads `~/.claude/settings.json`.
Restart an active host if it does not reload its hook configuration.
`doctor` inspects registration and Git's native hook list. It does not prove host execution or Codex trust.

## Install and uninstall output

Use `--json` to get machine-readable output. Run these commands to install or remove registrations:

```sh
pre-ship install
pre-ship install --json
pre-ship uninstall
pre-ship uninstall --json
```

See the [command output contract](../README.md#output-contract) for JSON fields and stdout, stderr, and exit code behavior.

## Removal and repair

To remove only this installation's registrations, run:

```sh
pre-ship uninstall
```

Uninstall preserves unrelated entries, symbolic links, and file modes.
It removes containers that become empty, including empty regular configuration files.
Installation is idempotent.
If a write fails after an earlier write succeeds, registration can be partial.
Repair the reported file, rerun `install`, and run `doctor`.

## Diagnostics

Run `doctor` to inspect registration, Git support, configuration, and the secret scanner:

```sh
pre-ship doctor --cwd /path/to/repository
```

The default output lists each check and groups repair steps for failed checks below the results.
The command returns a nonzero exit code when registration, Git support, configuration, or required gitleaks fails.
Disabled Git or host hooks appear as failures.
For an empty `review.command`, see [external review and record](reviewer.md#external-review-and-record).

Run `doctor --json` for a machine-readable report.

Run `events` to view counts for retained allow, block, and exemption observations by reason:

```sh
pre-ship events
pre-ship events --json
```

See the [command output contract](../README.md#output-contract) for JSON fields, stdout, stderr, and exit code behavior.

Diagnostic events exclude commands, diffs, findings, exemption reasons, and commit messages.
New files use mode 0600 and new directories use 0700.
The log rotates at 1 MiB and retains about 2 MiB.
A lock conflict or write failure loses observations without changing the gate result.
The log is diagnostic data, not an audit source of truth.

`doctor` can print limited status color in a terminal. It does not use color when `NO_COLOR` is set.

## Test fixture commits

A test fixture repository holds disposable test data. Global Git hooks also run in these repositories when tests inherit the developer's Git configuration.
In an agent environment, an ordinary fixture `git commit` then requires a receipt for that fixture repository.
pre-ship does not skip repositories based on a temporary path, a missing remote, or missing receipt history.
An agent can also create a delivery repository with those properties.

Use isolated Git configuration when the test needs ordinary `git commit` behavior.
Use Git plumbing, the low-level commands that create Git objects, when the test only needs commit history.
If the test covers pre-ship or another hook, install that hook explicitly in the private test environment and test its result.
Keep normal review and hooks active for the contributor's repository. Do not create fixture receipts that claim an independent review occurred.

### Isolate the test environment

Set the environment on Git subprocesses inside the test harness. Do not change the developer's HOME or global configuration.
A private HOME alone does not exclude system configuration or inherited `GIT_CONFIG*` overrides.
The example clears inherited Git environment variables, uses an empty private global file, and disables system configuration.
It retains agent markers and gives each subprocess an explicit environment and working directory.
See Git's [configuration environment variables](https://git-scm.com/docs/git-config/2.54.0#ENVIRONMENT).

This Python example creates an ordinary fixture commit and removes the private environment afterward:

```python
import os
from pathlib import Path
import subprocess
import tempfile

with tempfile.TemporaryDirectory(prefix="pre-ship-fixture-") as directory:
    root = Path(directory)
    home = root / "home"
    home.mkdir()
    configuration = home / "gitconfig"
    configuration.touch()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               GIT_CONFIG_GLOBAL=str(configuration), GIT_CONFIG_NOSYSTEM="1")
    repo = root / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env,
                              check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "fixture")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "file.txt").write_text("fixture\n")
    git("add", "file.txt")
    git("commit", "-q", "-m", "Fixture")
    assert git("show", "HEAD:file.txt") == "fixture"
```

Keep these overrides inside the test harness. The PreToolUse guard rejects direct agent commands that override `GIT_CONFIG*`.
Run the test harness through the repository's normal test command.
Use a new disposable repository. Existing local configuration, template hooks, and explicitly installed hooks still apply.
If tests start pre-ship or an agent host, isolate their configuration and cache paths too. See [Contributing](../CONTRIBUTING.md).

### Create history with Git plumbing

Use this recipe only inside a disposable fixture repository when commit hooks are outside the test's scope.
The repository must have a staged index and a local fixture identity, as in the Python example.
The following shell example assumes the current directory is that repository. It can retain the inherited global pre-ship hooks and agent markers.

```sh
tree=$(git write-tree)
commit=$(git commit-tree "$tree" -m "Fixture")
git update-ref HEAD "$commit" ""
parent=$(git rev-parse HEAD)
tree=$(git write-tree)
commit=$(git commit-tree "$tree" -p "$parent" -m "Next fixture")
git update-ref HEAD "$commit" "$parent"
```

[`commit-tree`](https://git-scm.com/docs/git-commit-tree) creates a commit object without running commit hooks. `update-ref` attaches it to the current branch.
The first update requires an unborn HEAD. The second supplies the expected parent to avoid overwriting a changed HEAD.
The second commit reuses the index. To model changed content, edit and stage files before its `write-tree` command.
`update-ref` can run reference-transaction hooks. This recipe does not isolate other Git configuration or test ordinary `git commit` behavior.
Do not use either recipe to publish changes or bypass review of the contributor's repository.
