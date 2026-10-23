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

## Configure a reviewer

Create `~/.config/pre-ship/config.toml`, or use the [XDG configuration path](docs/configuration.md#configuration-path):

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

Human commits and pushes pass through unchanged when all configured agent markers are empty.
Existing review workflows can use [`record`](docs/reviewer.md#external-review-and-record) after review passes for the exact target.

## Documentation

- [Configuration](docs/configuration.md): Global policy and agent detection.
- [Reviewer](docs/reviewer.md): Target binding, receipts, external review, and exemptions.
- [Gate rules](docs/gate-rules.md): Commit, push, tag, and replay checks.
- [Installation](docs/installation.md): Host registration, repair, and diagnostics.
- [Limits](docs/limits.md): Threat model and bypass paths.
- [Contributing](CONTRIBUTING.md): Development, test isolation, and CI.

The license is [MIT](LICENSE).
