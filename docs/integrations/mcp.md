# Model Context Protocol (MCP) server

clew ships an MCP server. Once connected, any MCP-compatible
client (Claude Desktop, Cursor, Cline, the MCP Inspector) can
browse, search, branch, replay, and diff your agent traces
*from the conversation*.

## What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io)
is a standard for connecting LLM clients to local tools. The
host (Claude, Cursor, …) speaks JSON-RPC over stdio to a server
process; the server exposes tools, resources, and prompts.

`clew mcp` runs as a server process. It opens your local
`.clew/` store and makes it available to whichever LLM you're
talking to.

## Install

```bash
# uv
uv add 'clew[mcp]'

# pip
pip install 'clew[mcp]'
```

## Connect from Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "clew": {
      "command": "clew",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Desktop. You'll see a 🔌 icon for clew in the
toolbar; click it to see the available tools.

## Connect from Cursor

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "clew": {
      "command": "clew",
      "args": ["mcp"]
    }
  }
}
```

## Available tools

The server exposes 12 tools, covering everything the CLI does:

| Tool | What it does |
|---|---|
| `list_traces` | Enumerate every trace in the store |
| `get_trace` | Fetch a trace by id (full span tree) |
| `get_span` | Fetch a single span by id |
| `search` | Find spans by name, type, status, metadata |
| `list_branches` | Show every branch and its head span |
| `show_branch` | Show the span tree rooted at a branch |
| `diff_traces` | Compute a structural diff between two traces |
| `create_branch` | Create a new branch at a given span |
| `checkout` | Switch the current branch |
| `replay` | Replay a trace, returning the new trace id |
| `doctor` | Run clew doctor and return the report |
| `query` | Run clew query with a filter spec |

Plus two resources:

- `store://info` — summary of the store (head, branches, count)
- `trace://<id>` — a single trace as a JSON document

## Example conversation

> **You:** My agent ran a search and got a bad result. Find the
> trace and tell me what happened.
>
> **Claude (with clew MCP):**
> `[calls list_traces, then get_trace on the most recent]`
> I found a 4-span trace from 2 minutes ago. The agent's
> `search-tool` span errored with `"tool failed: timeout"`. The
> plan and answer spans ran fine, but the answer reflects the
> timeout. Let me show you the input/output...
> `[calls get_span on each]`
> ...here's the diff between what was attempted and what would
> have worked. Want me to create a branch at the `search-tool`
> span so you can re-run with a different search backend?

That's the killer use case: instead of asking you to read the
trace yourself, Claude reads it and proposes an action.

## Privacy and security

The MCP server runs locally and has the same filesystem
permissions as the `clew` binary. It does not phone home, does
not log to a central server, and does not retain any data
beyond what's already in your store.

The stdio transport is the most secure option: the only thing
that crosses a process boundary is JSON-RPC messages between
the host and the server.

## See also

- [OpenTelemetry integration](otel.md) — the OTel bridge works
  with MCP-exposed traces
- [Tracing reference](../reference/protocol.md) — full schema
