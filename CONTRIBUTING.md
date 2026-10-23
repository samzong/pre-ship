# Contributing

Support Python 3.11 or later on macOS and Linux.
Use `uv` for the package environment and build. The isolated build uses `uv_build`.

## Development and tests

Before a PR, run these commands from the repository root:

```sh
uv run python -m unittest discover -s tests -v
uv build
```

`bin/pre-ship` is the source-checkout entry point used by tests.
Distribution installs the `pre-ship` console script.
Tests must isolate HOME, host paths, and Git configuration.
Never run install or uninstall against a contributor's real configuration during tests.

## CI

[CI](.github/workflows/tests.yml) runs Python 3.11 and 3.14 on macOS and Linux with Git 2.54 or later.
Each matrix job runs the unit tests and builds the package.
The jobs also install gitleaks for secret-scanning tests.
