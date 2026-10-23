# Reviewer contract

The reviewer owns review quality, independent reviewer selection, tests, and verdict interpretation.
pre-ship owns command execution and target binding.
It does not interpret model prose or convert findings into a verdict.

## Command input and result

Configure [`review.command`](configuration.md#fields) as an argument array.
pre-ship executes it without a shell from the repository root.
The command must exit zero only when its review passes. A command that always exits zero defeats this workflow.

The reviewer receives one JSON object on stdin:

```json
{"root":"/path/to/repository","head":"<commit SHA or UNBORN>","tree":"<tree SHA>","mode":"staged","fingerprint":"<SHA-256 of the binary diff>"}
```

| JSON field | Environment variable | Meaning |
| --- | --- | --- |
| `root` | `PRE_SHIP_ROOT` | Canonical worktree root. |
| `head` | `PRE_SHIP_HEAD` | HEAD commit SHA, or `UNBORN` before the first commit. |
| `tree` | `PRE_SHIP_TREE` | Tree SHA for the review target. |
| `mode` | `PRE_SHIP_MODE` | `staged` or `committed`. |
| `fingerprint` | `PRE_SHIP_FINGERPRINT` | SHA-256 of the binary diff. |

An adapter can pass these fields to an existing review service.
For `staged`, inspect `git diff --cached --binary`, not unstaged files.
For `committed`, inspect the last commit at the supplied HEAD, including its root diff if it has no parent.

pre-ship discards the command's stdout and stderr to avoid leaking review content.
For diagnostics, run the reviewer directly or configure it to write a private report.
Keep credentials out of the argument array.

## CLI output

`review` runs the configured reviewer and records a receipt for the exact target.
It defaults to staged. Use `review staged` or `review --mode staged` for the staged target.
Use `review committed` or `review --mode committed` for the last commit.

```sh
pre-ship review staged
pre-ship review --mode committed --json
```

`record` records a receipt after an external review passes for the exact target.
It uses the same target selection as `review` and defaults to staged.
Use `--json` with `review` or `record` to print a receipt object instead of human-readable output.

```sh
pre-ship record staged --json
```

The JSON receipt contains `version`, `root`, `head`, `tree`, `mode`, `fingerprint`, `verdict`, and `created_at`.
`version` is `2`. An `EXEMPTED` receipt also contains `reply` and `reason`.
The `root`, `head`, `tree`, `mode`, and `fingerprint` fields identify the reviewed target.

See the [command output contract](../README.md#output-contract) for stdout, stderr, and exit code behavior.

## Receipt lifecycle

A receipt is a local record bound to a reviewed Git target.
Before review, pre-ship removes the previous receipt for this repository.
After a zero exit, it compares the target again.
It writes a receipt only if HEAD, tree, mode, root, and fingerprint still match.
A failed command or changed target must leave no receipt from that attempt.

SIGTERM and SIGHUP remove the receipt and stop the direct reviewer process, unless the process inherited an ignored signal.
Ignored signals stay ignored in pre-ship and its reviewer, including SIGHUP under `nohup`.
SIGKILL cannot run cleanup.
Unstaged edits do not change a staged target.
The reviewer must explicitly account for runtime inputs outside that target.

Commit entry and push require a receipt no more than five minutes old.
`commit-msg` checks the binding again after message editing without the age limit.
A committed receipt covers the last commit at HEAD, not all commits in a push.

## External review and record

If an existing workflow produces the review verdict, keep that workflow as the owner of the verdict.
After it passes for the exact staged target, record that target:

```sh
pre-ship record staged --cwd /path/to/repository
git -C /path/to/repository commit -s -m "Describe the change"
```

Before push, review the committed target and run `pre-ship record committed`.
`record` does not run a review or tests. It is an attestation for an external workflow.
Never call it after a failed review or for content that changed after review.
`record` defaults to staged. `--mode staged` and `--mode committed` also work.

## Exemptions

If evidence remains unavailable, report that gap to the owner first.
Only after the owner gives the configured literal reply, record an exemption for that exact target:

```sh
pre-ship record staged --verdict EXEMPTED --reply EXEMPT --reason "Describe the missing evidence without secrets"
```

[`exemption_reply`](configuration.md#fields) changes the required literal reply. The default is `EXEMPT`.
The helper cannot authenticate a conversation. The calling workflow must obtain the owner's reply.
An exemption remains `Pre-Ship: UNVERIFIED — EXEMPTED`, never PASS.
It cannot override a failed mechanical check or authorize a different target.

## Receipt storage

Receipts live in `~/.cache/pre-ship`.
They bind the canonical worktree root, HEAD, tree, binary diff fingerprint, mode, verdict, and creation time.
Exemptions also store the literal reply and a missing-evidence summary of at most 2,000 characters.
Keep secrets out of that summary.
