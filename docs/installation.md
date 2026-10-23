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

Use these commands to inspect the installation:

```sh
pre-ship doctor --cwd /path/to/repository
pre-ship events
```

`doctor` returns nonzero for registration drift, unsupported Git, invalid configuration, or missing required gitleaks.
An unconfigured reviewer is informational because external workflows can use [`record`](reviewer.md#external-review-and-record).
`events` counts retained allow, block, and exemption observations by reason.

Diagnostic events exclude commands, diffs, findings, exemption reasons, and commit messages.
New files use mode 0600 and new directories use 0700.
The log rotates at 1 MiB and retains about 2 MiB.
A lock conflict or write failure loses observations without changing the gate result.
The log is diagnostic data, not an audit source of truth.
