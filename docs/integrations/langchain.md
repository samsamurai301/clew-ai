# LangChain integration

clew ships a [LangChain](https://python.langchain.com) callback
handler that turns every chain / tool / LLM call into a clew
span. The handler is drop-in: pass it to any LangChain `invoke`
or `__call__` and the trace appears in your local store.

## Install

```bash
uv add 'clew[langchain]'  # or: uv add clew langchain
```

(The `langchain` extra pulls in `langchain-core` for the type
hints. The handler itself is pure Python.)

## Quick example

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from clew.integrations.langchain import ClewCallbackHandler
from clew.sdk import Tracer, SpanType

t = Tracer()
cb = ClewCallbackHandler(tracer=t)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("human", "{question}"),
])
model = ChatOpenAI(model="gpt-4o")
chain = prompt | model | StrOutputParser()

@t.agent
def ask(q: str) -> str:
    return chain.invoke({"question": q}, config={"callbacks": [cb]})

ask("what is clew?")
```

After the run, `clew show <trace_id>` shows:

```
ask (agent)
└─ RunnableSequence (chain)
   ├─ ChatPromptTemplate (template)
   ├─ ChatOpenAI (llm, type=LLM)
   └─ StrOutputParser (parser)
```

Each LangChain runnable becomes a `SpanType.OBSERVATION`; LLM
runs become `SpanType.LLM` automatically (the handler
inspects the runnable class).

## What gets captured

For each LangChain event, the handler writes a span with:

| Field | Source |
|---|---|
| `span.name` | the runnable's name (e.g. `ChatPromptTemplate`, `ChatOpenAI`) |
| `span.type` | `LLM` for chat models, `TOOL` for tool runs, `OBSERVATION` for everything else |
| `span.input` | the runnable's input (serialized) |
| `span.output` | the runnable's output (serialized) |
| `span.attributes` | the runnable's metadata (model name, temperature, etc.) |
| `span.status` | `OK` on success, `ERROR` on exception |

Tool calls become `SpanType.TOOL` with the tool name in
`attributes["tool_name"]`.

## Sharing across chains

A single `ClewCallbackHandler` can be passed to multiple
chains in one agent:

```python
@t.agent
def big_agent(question: str) -> str:
    a = chain1.invoke(..., config={"callbacks": [cb]})
    b = chain2.invoke(a, config={"callbacks": [cb]})
    return chain3.invoke(b, config={"callbacks": [cb]})
```

The handler maintains its own context (which runnable is
currently active) so the spans form a single tree.

## See also

- [OpenTelemetry integration](otel.md) — bridges for OpenAI
  and Anthropic directly
- [SDK reference](../reference/sdk.md)
