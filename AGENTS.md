# Maintenance contract

Support Python 3.11 or later on macOS and Linux. Use `uv` for the package environment and build.

Run `uv run python -m unittest discover -s tests -v` and `uv build` before a PR. Integration tests must use a private HOME, host paths, and Git configuration. Never run install or uninstall against a contributor's real configuration during tests.

Preserve the reviewer contract and target binding described in docs/reviewer.md. Human commits without configured agent markers must run the content checks without a receipt, and human pushes must pass through unchanged. Keep hook names under `hook.preship-*` and leave `core.hooksPath` available to other tools.

Keep source files free of code comments and docstrings. Preserve executable shebangs and license text. Do not print commands, credentials, diffs, or raw scanner output in diagnostics.
