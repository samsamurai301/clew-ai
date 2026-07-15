# Installation

## Requirements

- **Python 3.11 through 3.14.** clew uses modern type syntax
  (`str | None`, `StrEnum`, structural pattern matching) and
  Pydantic v2, all of which require 3.11+.
- **Linux, macOS, or Windows.** clew is pure Python with
  C-accelerated deps (cryptography, sqlite3).
- **No system-level dependencies.** No Docker, no Postgres, no
  Redis. Your store is a directory on your filesystem.

## Install

=== "uv"

    ```bash
    uv add clew-ai
    ```

=== "pip"

    ```bash
    pip install clew-ai
    ```

=== "poetry"

    ```bash
    poetry add clew-ai
    ```

## Optional extras

```bash
# MCP server (Claude Desktop, Cursor, Cline)
uv add 'clew-ai[mcp]'

# Textual TUI, LangChain, or provider clients
uv add 'clew-ai[tui]'
uv add 'clew-ai[langchain]'
uv add 'clew-ai[openai]'
uv add 'clew-ai[anthropic]'

# All optional integrations
uv add 'clew-ai[all]'
```

## Verify

```bash
clew version
# clew 1.1.5
```

If you see the version, you're good. The first time you run
`clew init` in a project, it creates a `.clew/` directory
alongside your code.

## Next

- [Quickstart →](quickstart.md) — five-minute walkthrough
- [Tutorial →](tutorial.md) — build a real agent with clew
