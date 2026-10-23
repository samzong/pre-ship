# pre-ship

pre-ship checks that an AI coding agent's change was reviewed before it ships.

It is a workflow gate for honest agents, not a security boundary. See the [limits](docs/limits.md).

## Requirements

Use Python 3.11 or later and Git 2.54.0 or later on macOS or Linux.
Git introduced configuration-based hooks in [2.54.0](https://github.com/git/git/blob/v2.54.0/Documentation/RelNotes/2.54.0.adoc).
pre-ship has no runtime dependencies outside the Python standard library.

## Install

Install once for all repositories with [uv](https://docs.astral.sh/uv/guides/tools/):

```sh
uv tool install git+https://github.com/samzong/pre-ship
pre-ship --version
pre-ship install
pre-ship doctor --cwd /path/to/repository
```

Installation preserves unrelated configuration and leaves `core.hooksPath` available for other tools.
See [installation](docs/installation.md) for host setup, Codex trust, repairs, and removal.

## Configure a command-based reviewer

If you want pre-ship to run a reviewer, create `~/.config/pre-ship/config.toml` or use the [XDG configuration path](docs/configuration.md#configuration-path):

```toml
[review]
command = ["/absolute/path/to/your-review-command"]
```

Replace the path with your reviewer executable. It must exit zero only when its review passes.
All other fields have defaults. See the [configuration reference](docs/configuration.md) and [reviewer contract](docs/reviewer.md).

## Daily use

Review the final index before an agent commits, then review the last commit before push:

```sh
git add path/to/changed-file
pre-ship review staged
git commit -s -m "Describe the change"
pre-ship review committed
git push -u origin HEAD
```

When all configured agent markers are empty, commits run the same content checks without a receipt, and pushes pass through unchanged.
Existing review workflows can use [`record`](docs/reviewer.md#external-review-and-record) after review passes for the exact target.

## Commands

The user commands are `review`, `record`, `doctor`, `events`, `install`, and `uninstall`.
Run `pre-ship` or `pre-ship --help`, or add `--help` after a command to see its options.
The installed Git and host hooks use hidden commands. Existing hook registrations can keep calling them.

Use `review` to run the configured reviewer and save a receipt for the staged or committed target:

```sh
pre-ship review staged
pre-ship review --mode committed --json
```

Use `record` when another workflow already reviewed the exact target:

```sh
pre-ship record staged --json
```

`review` and `record` default to staged. `review staged` and `review --mode staged` select the same target.
All six commands print human-readable output by default. Each command accepts `--json`.

```sh
pre-ship doctor --cwd /path/to/repository
pre-ship doctor --json --cwd /path/to/repository
pre-ship events --json
pre-ship install --json
pre-ship uninstall
pre-ship uninstall --json
```

### Output contract

On success, `--json` writes only JSON to stdout. Errors use text on stderr and keep the command's exit code.
`review --json` and `record --json` print the receipt object.
`doctor --json` keeps its existing `configuration`, `checks`, and `scope` fields.
The report goes to stdout when a check fails, and the command keeps its failure exit code.
The `review` check's `status` is `configured` when `review.command` is nonempty and `not_configured` when it is empty.
Human output shows `[ok] review: configured` for a configured command and `[info] review: not configured` when it is empty.
The review row never appears under `Next steps`.
`events --json` keeps its existing `counts` and `skipped` fields.
`install --json` prints the same report as `doctor --json`.
If that report shows a doctor failure, `install` still prints the report to stdout and keeps its failure exit code.
Human `install` output reuses the `doctor` report and adds no reviewer configuration step.
`uninstall --json` prints an object with `action` and `scope` fields.
Without `--json`, `uninstall` prints: “Only registrations for this exact helper were removed. Other configuration was preserved.”

For an `EXEMPTED` receipt, human output shows the status once on stdout.
With `--json`, the receipt stays on stdout and the exemption warning goes to stderr.

Python 3.14 adds color to argparse help output. Python 3.11 through 3.13 show plain help.
The standard library controls color with `NO_COLOR` and `FORCE_COLOR`.

## Documentation

- [Configuration](docs/configuration.md): Global policy and agent detection.
- [Reviewer](docs/reviewer.md): Target binding, receipts, external review, and exemptions.
- [Gate rules](docs/gate-rules.md): Commit, push, tag, and replay checks.
- [Installation](docs/installation.md): Host registration, repair, and diagnostics.
- [Limits](docs/limits.md): Threat model and bypass paths.
- [Contributing](CONTRIBUTING.md): Development, test isolation, and CI.

The license is [MIT](LICENSE).
