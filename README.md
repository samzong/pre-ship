# pre-ship

pre-commit checks code. pre-ship checks that an AI coding agent's change was reviewed before it ships.

Install it once for all repositories. pre-ship runs only when an agent environment variable is nonempty. Human commits and pushes pass through unchanged. It uses named hooks in Git configuration, so it leaves `core.hooksPath` available for pre-commit and other tools.

pre-ship is a workflow gate for honest agents. It is not a security boundary against an agent that chooses to bypass it.

## Install

You need Python 3.11 or later and Git 2.54.0 or later on macOS or Linux. Git introduced configuration-based hooks in [2.54.0](https://github.com/git/git/blob/v2.54.0/Documentation/RelNotes/2.54.0.adoc). See the [versioned hook documentation](https://git-scm.com/docs/git-hook/2.54.0.html) for the configuration format.

Install with [uv](https://docs.astral.sh/uv/guides/tools/):

```sh
uv tool install git+https://github.com/samzong/pre-ship
pre-ship --version
pre-ship install
pre-ship doctor --cwd /path/to/repository
```

This repository is private. Git must already have access to it. The package has no runtime dependencies outside the Python standard library. The isolated build uses `uv_build`.

`install` requires an executable named `pre-ship` outside the uv and pipx run caches. Do not run installation through `uvx`, `pipx run`, or `python -m pre_ship`. Use `uv tool install` first. Rejected helpers leave all configuration unchanged.

Installation registers the absolute invocation path without resolving symlinks. Keep that path available after upgrades. Symlinks and their targets identify the same helper for doctor, uninstall, and conflict checks. Reinstallation updates owned Git, Claude Code, and Codex entries to the current invocation path.

`install` adds four global Git hooks and PreToolUse entries for Claude Code and Codex. It preserves unrelated Git and host configuration. It does not change shared hook enable flags or take over an existing gate helper.

If another helper is registered, run that helper's `uninstall` command first. For example:

```sh
/absolute/path/to/old/pre-ship-gate uninstall
pre-ship install
pre-ship doctor --cwd /path/to/repository
```

Use the exact helper path in the conflict message or the value from `git config --global --get hook.preship-prepare.command`. The same procedure applies to an older `~/.agents/bin/pre-ship` installation. Do not remove an old checkout while its hooks remain registered.

Codex reads `~/.codex/hooks.json`, or `$CODEX_HOME/hooks.json` when set. In Codex, open `/hooks` and trust the new command. Claude Code reads `~/.claude/settings.json`. Restart an active host if it does not reload its hook configuration. `doctor` inspects registration and Git's native hook list. It does not prove host execution or Codex trust.

To remove only this installation's registrations:

```sh
pre-ship uninstall
```

Installation is idempotent. If a write fails after an earlier write succeeds, registration can be partial. Repair the reported file, rerun `install`, and run `doctor`. Uninstall preserves unrelated entries, symbolic links, and file modes. It removes containers that become empty, including empty regular configuration files.

## Configure a reviewer

Create `~/.config/pre-ship/config.toml`. If `XDG_CONFIG_HOME` is an absolute path, use `$XDG_CONFIG_HOME/pre-ship/config.toml` instead. Empty or relative XDG paths use the default directory. There is no repository override. One global policy avoids a repository silently weakening the gate.

```toml
agent_markers = ["CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "LODY_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"]
exemption_reply = "EXEMPT"

[review]
command = ["/absolute/path/to/your-review-command"]

[gitleaks]
mode = "optional"

[push]
protected_branches = ["main", "master"]
allow_protected = false
```

Replace the reviewer path with an executable from your review workflow. The command must exit zero only when its review passes. pre-ship does not interpret prose from a model or convert findings into a verdict. A command that always exits zero defeats this workflow.

The command is an argument array, executed without a shell from the repository root. No command is configured by default. A missing command blocks `review` and leaves Git operations without a review receipt blocked. If configuration cannot be read or validated, default agent markers block operations with an actionable error. Other processes pass through so a broken configuration does not block human work. Custom markers require valid configuration. Keep credentials out of the argument array and use the reviewer's existing credential mechanism.

The reviewer receives one JSON object on stdin:

```json
{"root":"/path/to/repository","head":"<commit SHA or UNBORN>","tree":"<tree SHA>","mode":"staged","fingerprint":"<SHA-256 of the binary diff>"}
```

The same fields are available as `PRE_SHIP_ROOT`, `PRE_SHIP_HEAD`, `PRE_SHIP_TREE`, `PRE_SHIP_MODE`, and `PRE_SHIP_FINGERPRINT`. An adapter can read these fields and pass the exact target to an existing review service. For `staged`, inspect `git diff --cached --binary`, not unstaged files. For `committed`, inspect the last commit at the supplied HEAD, including its root diff if it has no parent.

The reviewer owns review quality, independent reviewer selection, tests, and verdict interpretation. pre-ship owns command execution and target binding. It discards the command's stdout and stderr to avoid leaking review content. For diagnostics, run the reviewer directly or configure it to write a private report.

Before review, pre-ship removes the previous receipt for this repository. After a zero exit, it compares the target again and writes a receipt only if HEAD, tree, mode, root, and fingerprint still match. A failed command or changed target must leave no receipt from that attempt.

SIGTERM and SIGHUP also remove the receipt and stop the direct reviewer process, unless the process inherited an ignored signal. Ignored signals stay ignored in pre-ship and its reviewer, including SIGHUP under `nohup`. SIGKILL cannot run cleanup. Unstaged edits do not change a staged target. The reviewer must explicitly account for any runtime inputs outside that target.

## Use the gate

Review the final index before an agent commits:

```sh
git add path/to/changed-file
pre-ship review staged
git commit -s -m "Describe the change"
pre-ship review committed
git push -u origin HEAD
```

A receipt is a local record bound to a reviewed Git target. Commit entry and push require a receipt no more than five minutes old. `commit-msg` checks the binding again after message editing without the age limit. A committed receipt covers the last commit at HEAD, not all commits in a push.

An agent is any process with a nonempty variable from `agent_markers`. Human operations with all configured markers empty bypass every pre-ship check, including sign-off, size, secret scanning, and push policy. The host guard also acts only in an identified agent process. Configure the list to match the hosts that you use. An empty list disables agent enforcement.

Agent commits run the receipt, whitespace, size, optional secret scan, and Signed-off-by checks. `git commit -s` supplies the sign-off. An exemption never disables these mechanical checks. Lint and tests remain the responsibility of the repository and configured reviewer.

New or growing staged files above approximately 5,000 tokens (`bytes / 4`) block. Lockfiles are excluded. For a local file exception, add `<path> pre-ship-large-file` to the repository's `.git/info/attributes`. The gate deliberately ignores `.gitattributes` for this exception. Split code by responsibility and tests by the unit under test.

`gitleaks.mode` accepts three values:

- `optional`: Run gitleaks when installed. If it is missing, continue with an explicit warning. This is the default.
- `required`: Block agent commits when gitleaks is missing or fails.
- `off`: Do not run gitleaks.

An installed gitleaks that reports findings or fails blocks both `optional` and `required` modes. pre-ship never prints the scanner's raw output or detected secrets. Install [gitleaks](https://github.com/gitleaks/gitleaks) separately if you use secret scanning.

Agent pushes permit one ref from the current branch at the reviewed HEAD. Protected branch pushes block unless `push.allow_protected` is true. `push.protected_branches` contains short branch names and defaults to `main` and `master`. A fast-forward already contained in another remote's same-named tracking branch can pass without a receipt, including protected branches.

A single new annotated SemVer tag can pass without a receipt if its commit is an ancestor of the live remote main tip, or master when main is absent. The remote tip must exist locally. Moving tags, deleting refs, and pushing multiple refs block. URL rewrites that prevent remote ancestry proof block tag pushes.

For an agent merge or cherry-pick, apply with `--no-commit`, review the index, then commit. After a replay stops, review the applied index, obtain a receipt, and commit with sign-off. For rebase, continue after that commit. `git am` uses `pre-applypatch` to check its applied index. A fast-forward creates no commit and runs no commit hook.

## Connect an external review workflow

If your existing workflow already produces a review verdict, keep it as the owner of that verdict. After it passes for the exact staged target, record that target:

```sh
pre-ship record staged --cwd /path/to/repository
git -C /path/to/repository commit -s -m "Describe the change"
```

Before push, review the committed target and run `pre-ship record committed`. `record` does not run a review or tests. It is an attestation for an external workflow. Never call it after a failed review or for content that changed after review. `record` defaults to staged, and `--mode staged` or `--mode committed` also works.

If evidence remains unavailable, report that gap to the owner first. Only after the owner gives the configured literal reply, record an exemption for that exact target:

```sh
pre-ship record staged --verdict EXEMPTED --reply EXEMPT --reason "Describe the missing evidence without secrets"
```

`exemption_reply` changes the required literal reply. The default is `EXEMPT`. The helper cannot authenticate a conversation. The calling workflow must obtain the owner's reply. An exemption remains `Pre-Ship: UNVERIFIED — EXEMPTED`, never PASS. It cannot override a failed mechanical check or authorize a different target.

Receipts live in `~/.cache/pre-ship`. They bind the canonical worktree root, HEAD, tree, binary diff fingerprint, mode, verdict, and creation time. Exemptions also store the literal reply and a missing-evidence summary of at most 2,000 characters. Keep secrets out of that summary.

## Limits and diagnostics

This is a local gate without a server-side fallback. A process that can edit its configuration or receipt files can bypass it. These known paths also bypass some or all checks:

- Remove agent variables with `env -u`, or use SSH without forwarding agent variables.
- Put Git commands inside shell groups, scripts, subshells, `eval`, interactive tools, or unsupported wrappers.
- Rewrite destinations through `url.*` or `remote.*` Git configuration.
- Write remote refs through `gh api` or another remote API.
- Use plumbing such as `commit-tree`, `stash`, or `update-ref`, which does not run commit hooks.
- Push intermediate commits that the HEAD receipt does not cover. The gate does not review the complete push range.
- Disable hooks, change the executable, run `--no-verify` outside the guarded host, or directly call `record` without review.

The PreToolUse guard rejects recognizable `--no-verify` and commit `-n` commands, hook or alias overrides, `send-pack`, and Git configuration or index environment overrides. It is a limited shell parser. It does not claim coverage of arbitrary shell programs.

Use these commands to inspect the installation:

```sh
pre-ship doctor --cwd /path/to/repository
pre-ship events
```

`doctor` returns nonzero for registration drift, unsupported Git, invalid configuration, or missing required gitleaks. An unconfigured reviewer is informational because external workflows can use `record`. `events` counts retained allow, block, and exemption observations by reason.

Diagnostic events exclude commands, diffs, findings, exemption reasons, and commit messages. New files use mode 0600 and new directories use 0700. The log rotates at 1 MiB and retains about 2 MiB. A lock conflict or write failure loses observations without changing the gate result. The log is diagnostic data, not an audit source of truth.

## Develop

Use the package's editable environment and isolated test fixtures:

```sh
uv run python -m unittest discover -s tests -v
uv build
```

`bin/pre-ship` is the source-checkout entry point used by tests. Distribution installs the `pre-ship` console script. Tests isolate HOME, host paths, and Git configuration. CI runs Python 3.11 and 3.14 on macOS and Linux with Git 2.54 or later.

The code originated from the stdlib gate in `samzong/.agents` at `a804b4a`. pre-ship v0.1 replaces private skill assumptions with a configurable reviewer contract. It retains `record` for external workflows. The license is MIT.
