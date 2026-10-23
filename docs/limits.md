# Limits

pre-ship is a workflow gate for honest agents.
It is not a security boundary against an agent that chooses to bypass it.
This is a local gate without a server-side fallback.
A process that can edit its configuration or receipt files can bypass it.

## Known bypass paths

These paths bypass some or all checks:

- Remove agent variables with `env -u`, or use SSH without forwarding agent variables.
- Put Git commands inside shell groups, scripts, subshells, `eval`, interactive tools, or unsupported wrappers.
- Rewrite destinations through `url.*` or `remote.*` Git configuration.
- Write remote refs through `gh api` or another remote API.
- Use plumbing such as `commit-tree`, `stash`, or `update-ref`, which does not run commit hooks.
- Push intermediate commits that the HEAD receipt does not cover. The gate does not review the complete push range.
- Disable hooks, change the executable, run `--no-verify` outside the guarded host, or directly call `record` without review.

## PreToolUse coverage

The PreToolUse guard runs on every host call, regardless of [agent markers](configuration.md#agent-detection).
It rejects recognizable `--no-verify` and commit `-n` commands, hook or alias overrides, `send-pack`, and Git configuration or index environment overrides.
It is a limited shell parser. It does not claim coverage of arbitrary shell programs.
