# Contributing to clew

Thanks for your interest in making `clew` better! This document
covers the day-to-day workflow.

## TL;DR

```bash
git clone https://github.com/samsamurai301/clew-ai
cd clew-ai
uv sync --group dev
uv run pytest          # run the test suite
uv run ruff check .    # lint
uv run mypy --strict src/  # types
```

## Project layout

```
src/clew/
├── core/          Storage, branch, replay, diff, bundle, format, health, query, runner
├── sdk/           Tracer (decorators + context manager) + OTel bridge
├── ui/            rich renderers + textual TUI
└── cli.py         typer CLI
tests/             pytest test suite, one file per source module
docs/              hand-written documentation
examples/          runnable demos
```

The boundary is intentional: `core/` is the engine (no I/O that
isn't file I/O), `sdk/` is the user-facing Python API, `ui/` is
rendering, `cli.py` is the CLI. New features should slot into one
of these layers — don't add new top-level packages without a
discussion in an issue first.

## Style

- **Python 3.11+** features are fair game (PEP 604, StrEnum, etc.).
- We use **Pydantic v2** for persisted domain models and frozen dataclasses for small
  in-memory result/context values.
- We use **uv** for dependency management; don't commit `poetry.lock`
  or `requirements.txt`.
- We use **Typer** and **Rich** for the CLI. Textual is an optional TUI extra.
- All public functions and methods get a docstring in the same
  style as the rest of the codebase (imperative mood, mention
  side effects, end with a period).
- We use **mypy --strict** on `src/`. Per-file overrides are
  acceptable for genuinely untyped third-party surfaces (e.g. TUI
  widgets) but please add a comment explaining why.
- We use **ruff** for formatting and lint. Run `ruff check --fix`
  before committing.

## Adding a CLI command

1. Define the function with a `typer.Typer.command(...)` decorator
   in `src/clew/cli.py`.
2. Add the command to the appropriate section comment (e.g.
   `# --- share / verify / import ---`).
3. Add a test in `tests/test_cli.py` using `typer.testing.CliRunner`.
4. Update `docs/user-guide/cli.md` with the new command.

## Adding a CLI sub-feature

If you're adding a new store-level operation (like `clew doctor`):

1. Add the implementation in `src/clew/core/<module>.py`.
2. Add tests in `tests/test_<module>.py`.
3. Add the CLI command in `src/clew/cli.py`.
4. Add an entry to the `clew --help` summary if it's a user-facing
   command.

## Tests

- Unit tests live in `tests/`, one file per source module.
- The CLI tests use `typer.testing.CliRunner`. We *do not* mock
  the store; tests run against a real `.clew` directory in a tmp
  path.
- Preserve the repository gate of at least 85% branch coverage; add focused coverage for
  every behavior you introduce.
- If you change a public API, update `docs/user-guide/tracer.md` or
  `docs/user-guide/cli.md` in the same commit.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(sdk): add @t.stream decorator for streaming responses
fix(cli): handle empty argv in `clew trace`
docs: update README with v1.0.0 migration notes
```

A `feat:` commit will trigger a minor version bump. A `fix:` or
`docs:` commit will trigger a patch version bump. A `BREAKING
CHANGE:` footer (or a `!` after the type) triggers a major bump.

## Release process

Maintainers run releases through the clean-commit, TestPyPI, artifact-verification, tag,
trusted-publishing, Pages, and public-link checklist in [`docs/RELEASE.md`](docs/RELEASE.md).

You do not need to do any of this for a PR — the maintainer will
handle it.

## Code of conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/).
Be kind. Assume good faith. Focus on the code, not the person.

## Security

See [SECURITY.md](SECURITY.md) for our security policy, supported
versions, and how to report a vulnerability. **Please do not open
a public GitHub issue for security reports.**
