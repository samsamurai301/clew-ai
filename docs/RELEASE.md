# Clew release checklist

Release only from a clean `main` commit after every automated and human gate below passes.
The version in `pyproject.toml`, the `v<version>` tag, the distributions, and the GitHub
release must always agree.

## Containment before release

- Never store a PyPI token in the repository, CI variables, or shell history. Trusted
  publishing is the release path.
- For a compromised or broken release, yank it with a clear migration reason and publish
  a new version. Never replace an uploaded artifact.
- Keep the GitHub default branch, protected-branch rules, and private vulnerability
  reporting enabled.

These are account-level actions and cannot be proven by repository code alone.

## Automated gates

```bash
uv sync --all-extras --group docs
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest --cov=clew --cov-branch --cov-report=term-missing
uv run mkdocs build --strict
uv run pip-audit
uv build
uv run twine check dist/*
```

CI additionally verifies Python 3.11–3.14 on Linux, branch coverage of at least 85%,
CodeQL, secret scanning, wheel installation on macOS and Windows, v2 bundle/NDJSON fuzz
tests, and the 10,000-span performance contract.

Inspect both wheel and sdist. Confirm metadata, the `clew` and `clew-ai` entry points, and
`clew/py.typed`. Install each artifact in a fresh environment and run `clew demo`.

## Human gate

Before public release, at least ten external Python-agent developers must complete the
private demo, at least five must use Clew on their own agent, and no P0/P1 finding may
remain unresolved. Record evidence outside the runtime; 1.1.5 sends no adoption telemetry.

## Publication

1. Update `pyproject.toml`, `CHANGELOG.md`, and relevant docs in a focused PR.
2. Publish the exact candidate commit to TestPyPI through trusted publishing.
3. Install from TestPyPI and repeat the complete user journey.
4. Confirm the version, changelog, compatibility notice, and tag alignment.
5. Push the matching tag (`git tag v<version> && git push origin v<version>`). CI rebuilds
   and verifies the tag, publishes through trusted publishing,
   deploys GitHub Pages, and creates the GitHub release.
6. Verify PyPI installation and every public link from a clean machine.

## One-time PyPI/GitHub connection

On PyPI, open the `clew-ai` project settings and add a trusted publisher:

- Owner: `samsamurai301`
- Repository: `clew-ai`
- Workflow: `.github/workflows/release.yml`
- Environment: `pypi`

Add a second publisher with environment `testpypi` on TestPyPI. In GitHub, create the
`pypi` and `testpypi` environments, require approval for `pypi`, and restrict deployments
to the `main` branch and `v*` tags. No `PYPI_API_TOKEN` secret is needed.

## Future release policy

- Patch: bug fixes, docs, and safe compatibility changes.
- Minor: new public capabilities or integrations.
- Major: intentional breaking API, CLI, or persisted-format changes.
- Every release gets a changelog entry, a migration note when needed, and clean install
  smoke tests for both the wheel and sdist.
