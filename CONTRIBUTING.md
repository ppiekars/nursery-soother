# Contributing

Nursery Soother is at the foundation stage. Changes should preserve the safety
boundary described in [docs/architecture.md](docs/architecture.md) and should
not silently introduce behavior that controls nursery devices.

## Development setup

The test environment tracks the current Home Assistant Python runtime. Install
[uv](https://docs.astral.sh/uv/), then run:

```bash
uv sync
uv run pre-commit install
```

The install command enables both the commit-time hooks and the test suite on
`pre-push`.

## Checks

Run these before opening a pull request:

```bash
uv run pre-commit run --all-files
uv run pytest
```

Commit-time hooks check file hygiene, JSON/YAML/TOML syntax, Ruff formatting and
linting, and `uv.lock`. The full test suite runs automatically before a push. To
exercise that hook explicitly, run:

```bash
uv run pre-commit run --hook-stage pre-push --all-files
```

Validate Home Assistant metadata with the same Hassfest image used by CI:

```bash
mkdir -p /tmp/hassfest-empty-venv
docker run --rm \
  -v "$(pwd):/github/workspace" \
  -v /tmp/hassfest-empty-venv:/github/workspace/.venv \
  ghcr.io/home-assistant/hassfest
```

The second mount prevents Hassfest from discovering Home Assistant's bundled
integrations inside the local virtual environment.

CI also runs the official HACS repository validator. Its temporary ignores are
documented in [docs/releasing.md](docs/releasing.md).

## Pull requests

- Keep config-entry data compatible or add a migration.
- Put connection-critical selections in config-entry data and mutable policy
  settings in config-entry options.
- Register listeners during config-entry setup and attach cleanup callbacks to
  the entry lifecycle.
- Add tests for every config-flow path and state transition.
- Add user-facing strings, written out in full, to `translations/en.json`.
  Custom integrations do not use Home Assistant Core's `strings.json` build
  pipeline or translation-key placeholders.
- Do not log notification payloads, camera URLs, snapshot paths, or other
  household-sensitive values.
