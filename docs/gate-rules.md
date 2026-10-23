# Gate rules

These rules apply only to [identified agent processes](configuration.md#agent-detection).
Human operations with all configured markers empty bypass every pre-ship check.
Lint and tests remain the responsibility of the repository and configured reviewer.

## Commit checks

Agent commits require a current [receipt](reviewer.md#receipt-lifecycle), valid whitespace, acceptable file sizes, a passing enabled secret scan, and sign-off.
`git commit -s` supplies the `Signed-off-by` trailer.
An exemption never disables these mechanical checks.

New or growing staged files above approximately 5,000 tokens (`bytes / 4`) block.
Lockfiles are excluded.
For a local file exception, add `<path> pre-ship-large-file` to the repository's `.git/info/attributes`.
The gate deliberately ignores `.gitattributes` for this exception.
Split code by responsibility and tests by the unit under test.

## Secret scanning

Install [gitleaks](https://github.com/gitleaks/gitleaks) separately if you use secret scanning.
The [`gitleaks.mode`](configuration.md#fields) field accepts three values:

- `optional`: Run gitleaks when installed. If it is missing, continue with an explicit warning. This is the default.
- `required`: Block agent commits when gitleaks is missing or fails.
- `off`: Do not run gitleaks.

An installed gitleaks that reports findings or fails blocks both `optional` and `required` modes.
pre-ship never prints the scanner's raw output or detected secrets.

## Push and tag rules

Agent pushes permit one ref from the current branch at the reviewed HEAD.
Protected branch pushes block unless `push.allow_protected` is true.
`push.protected_branches` contains short branch names and defaults to `main` and `master`.
A fast-forward already contained in another remote's same-named tracking branch can pass without a receipt, including protected branches.

A single new annotated SemVer tag can pass without a receipt.
Its commit must be an ancestor of the live remote main tip, or master when main is absent.
The remote tip must exist locally.
Moving tags, deleting refs, and pushing multiple refs block.
URL rewrites that prevent remote ancestry proof block tag pushes.

## Merge and replay operations

For an agent merge or cherry-pick, apply with `--no-commit`, review the index, then commit.
After a replay stops, review the applied index, obtain a receipt, and commit with sign-off.
For rebase, continue after that commit.
`git am` uses `pre-applypatch` to check its applied index.
A fast-forward creates no commit and runs no commit hook.
