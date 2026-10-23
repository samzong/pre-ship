# Configuration

pre-ship uses one global policy for all repositories.
There is no repository override, so a repository cannot silently weaken that policy.
See the [reviewer contract](reviewer.md) before you configure a review command.

## Configuration path

Create `~/.config/pre-ship/config.toml`.
If `XDG_CONFIG_HOME` is an absolute path, use `$XDG_CONFIG_HOME/pre-ship/config.toml` instead.
Empty or relative XDG paths use the default directory.

## Fields

All fields are optional. A missing file uses the defaults below.
The review command has no default executable.

| Field | Default | Meaning |
| --- | --- | --- |
| `agent_markers` | `["CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID"]` | Environment variable names that identify an agent process. An empty list treats every Git operation as human. |
| `exemption_reply` | `"EXEMPT"` | Literal owner reply required to record an exemption. It must contain 1 to 100 characters, including a non-space character. |
| `review.command` | `[]` | Command arguments, executed without a shell from the repository root. Each argument must be a nonempty string. |
| `gitleaks.mode` | `"optional"` | `optional` runs an installed scanner and warns when absent. `required` blocks when absent or failed. `off` skips scanning. |
| `push.protected_branches` | `["main", "master"]` | Protected branches as short names, without `refs/`. |
| `push.allow_protected` | `false` | Allows agent pushes to protected branches when true. Other push checks still apply. |

Keep credentials out of the command arguments. Use the reviewer's existing credential mechanism.
A missing command blocks `review`. Git operations that require a receipt remain blocked without one.
An external workflow can supply a receipt through [`record`](reviewer.md#external-review-and-record).

## Agent detection

An agent is any process with a nonempty variable from `agent_markers`.
Configure this list to match the hosts that you use.
An empty list treats every Git operation as human.

When all configured markers are empty, the Git operation counts as human.
Human commits skip the receipt but still run the whitespace, size, secret scanning, and sign-off checks.
Human pushes skip push policy.
The PreToolUse guard ignores these markers and runs on every call, because only agent hosts invoke it.
Codex, for example, starts hook processes without its session markers.
The guard stays active until `uninstall`.

## Invalid configuration

Unknown keys, invalid tables, invalid field values, and unreadable TOML cause configuration errors.
If configuration cannot be read or validated, commits and processes with a default agent marker block with an actionable error.
Other pushes pass through so a broken configuration does not block human pushes.

Custom markers require valid configuration.
Repair the global TOML file before you rely on custom markers.
Use [`doctor`](installation.md#diagnostics) to inspect configuration and registration.
