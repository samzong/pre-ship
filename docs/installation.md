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
