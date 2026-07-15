# 1.1.5 release checklist

Clew 1.1.5 is a breaking corrective Beta release. Release only from a clean `main` commit
after every automated and human gate below passes.

## Containment before release

- Revoke the PyPI token that appeared in the working directory and conversation history.
- Confirm `token.txt` is absent from the repository and local workspace.
- Yank PyPI 1.1.4 with the reason: **trace identity collisions and incorrect project
  links; use 1.1.5 or later**.
- Leave the existing `v1.1.4` tag unchanged. Record that it does not reproduce the manually
  uploaded artifact.
- Make `main` the GitHub default branch and enable private vulnerability reporting.

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
uv run twine check dist/clew_ai-1.1.5*
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

1. Publish the exact candidate commit to TestPyPI through trusted publishing.
2. Install from TestPyPI and repeat the complete user journey.
3. Confirm version 1.1.5, changelog, compatibility notice, and tag alignment.
4. Push the tag. CI rebuilds and verifies the tag, publishes through trusted publishing,
   deploys GitHub Pages, and creates the GitHub release.
5. Verify PyPI installation and every public link from a clean machine.

Never replace an uploaded artifact. If a critical regression is found, yank 1.1.5 and
publish 1.1.6.
