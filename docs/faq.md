# FAQ

## What is Clew?

Clew is a zero-server, Git-like what-if debugger for Python agent traces. It records local
execution evidence, replays a whole trace or selected suffix through another executor,
branches the result, and structurally diffs the outcomes.

## Does Clew send telemetry?

No. Clew 1.1.5 makes no analytics requests. Provider integrations still make the provider
calls requested by the host application. Adoption is measured outside the runtime through
GitHub, PyPI, and documentation aggregates.

## Is Clew an observability platform?

No. It is a focused package-and-files workflow. Broader products provide dashboards,
evaluation, collaboration, managed storage, or production observability. Relevant current
capabilities include:

- [LangGraph replay and fork](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
  for checkpointed LangGraph state.
- [LangSmith trace comparison](https://docs.langchain.com/langsmith/manage-trace).
- [Arize Phoenix](https://arize.com/docs/phoenix) replay, observability, and self-hosting.
- [Langfuse self-hosting](https://langfuse.com/self-hosting) for a broader LLM engineering
  platform.

Use Clew when local trace files and a failure → replay → branch → diff loop fit the job.

## Is the NDJSON format OpenTelemetry or OTLP?

It is **OTel-shaped JSON**. It borrows familiar field names and preserves `gen_ai.*`
attributes. It is not an OTLP exporter/receiver and has not passed OTLP interoperability
tests.

## Why does every identical call get a different ID?

Because each call is a separate occurrence. Collapsing equal inputs would erase frequency,
ordering, errors, and nondeterministic outputs. The UUID identifies the event; the
`content_hash` protects the exact finalized record.

## Can 1.1.5 open my old `.clew` store?

No. It detects v1 and refuses it without modification. Archive or rename the directory and
initialize v2. See the [compatibility notice](migration.md).

## What happens when replay fails?

The failing occurrence is persisted as `ERROR`, dependent descendants are persisted as
`SKIPPED`, and the complete diagnostic trace remains available. The CLI prints its trace ID
and exits nonzero.

## Are traces encrypted?

No. Stores and bundles contain plaintext payloads. Signed bundles provide integrity and
signer-key authenticity, not confidentiality. Protect the store and encrypt shared bundles
separately.

## Can I use OpenAI, Anthropic, LangChain, MCP, or a TUI?

Yes, through named extras: `openai`, `anthropic`, `langchain`, `mcp`, and `tui`. Provider
wrappers support sync and async clients. The LangChain handler subclasses the real callback
base and honors `parent_run_id`. The MCP server uses stdio and is tested with the public MCP
client.

## What Python versions are supported?

Python 3.11, 3.12, 3.13, and 3.14.
