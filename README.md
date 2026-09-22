# hintgap

> 📖 Read the write-up: [hintgap](https://jaytank.hashnode.dev/hintgap)

**Flag MCP tool definitions that ship without safety annotations — so an agent can reason about a tool's blast radius before it calls it.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

The Model Context Protocol lets a tool declare *behavioural hints* next to its
name and schema — `readOnlyHint`, `destructiveHint`, `idempotentHint` and
`openWorldHint`. They exist for one reason: an autonomous agent (or the client
mediating it) can look at a tool and know, *before invoking it*, whether it is a
safe read or something that mutates, deletes, spends money, or reaches out to the
open internet. A client can auto-approve the reads and pause on the writes.

A tool registered without those hints throws that reasoning surface away. The
agent is left to guess from the name — and `delete_invoice` looks a lot like
`get_invoice` to a next-token predictor under pressure to be helpful. The gap is
invisible in review: the server boots, the tool works, the tests pass. Nothing
ever fails because a hint was missing.

`hintgap` makes it fail. It parses your Python MCP server with the standard
library `ast` module, finds the tool registrations, and reports the ones that
leave the safety surface empty — as an exit code, so it drops into CI.

```text
$ hintgap examples/server.py

HG-NOHINTS  examples/server.py:34  (list_customers)
    tool 'list_customers' declares no safety annotations (readOnlyHint/destructiveHint/idempotentHint/openWorldHint) — the agent cannot reason about its blast radius
HG-NOHINTS  examples/server.py:41  (send_payment)
    tool 'send_payment' declares no safety annotations (readOnlyHint/destructiveHint/idempotentHint/openWorldHint) — the agent cannot reason about its blast radius
HG-WRITE-NOHINT  examples/server.py:41  (send_payment)
    tool 'send_payment' has a mutating verb in its name but is not marked destructiveHint=True (nor readOnlyHint=False) — an agent may treat a write/delete as a safe read
HG-NODESC  examples/server.py:48  (refresh_cache)
    tool 'refresh_cache' has no description (no description= and no docstring) — the agent has nothing to reason about but the name
HG-NOHINTS  examples/server.py:48  (refresh_cache)
    tool 'refresh_cache' declares no safety annotations (readOnlyHint/destructiveHint/idempotentHint/openWorldHint) — the agent cannot reason about its blast radius

5 gaps  ·  exit 1
```

## Install

```bash
pip install hintgap
# or run the single file directly — stdlib only, no dependencies
python hintgap.py path/to/server.py
```

Requires Python 3.8+. No third-party dependencies, no network, no imports of
your code — it only parses the source.

## What it checks

| Rule | Fires when | Why it matters |
| :--- | :--- | :--- |
| **HG-NOHINTS** | a tool is registered with **no** annotation hints at all | the agent can't tell a read from a mutation |
| **HG-WRITE-NOHINT** | a tool whose name implies mutation (`create`/`update`/`delete`/`write`/`send`/`pay`/`remove`/…) is **not** marked `destructiveHint=True` and does **not** set `readOnlyHint=False` | the most dangerous gap — a write the agent may treat as safe |
| **HG-NODESC** | a tool has no description (no `description=` and no docstring) | an undocumented tool is one the model has to guess the purpose of |

Exit codes: `0` clean · `1` gaps found · `2` usage error.

## Registration shapes it understands

**FastMCP decorators** — with or without a call, with or without args:

```python
@mcp.tool()                      # flagged: HG-NOHINTS
def list_customers(): ...

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))   # clean
def get_invoice(id): ...
```

**Low-level SDK construction** — `types.Tool(...)` / `Tool(...)` as returned from
a `list_tools` handler, adjacent to an `@app.call_tool` dispatcher:

```python
types.Tool(
    name="delete_file",          # flagged: HG-NOHINTS + HG-WRITE-NOHINT
    description="Delete a file.",
    inputSchema={...},
)
```

Annotations are read whether they are given as a `ToolAnnotations(...)` call, a
plain `{...}` dict, or as direct keyword arguments.

## Suppressing a line

Reviewed a tool and decided the gap is acceptable? Silence it with a comment on
the registration:

```python
@mcp.tool()  # hintgap: ignore
def ping() -> str:
    return "pong"
```

## Selecting rules

```bash
hintgap server.py --select HG-WRITE-NOHINT       # only the mutation gap
hintgap server.py --json                          # machine-readable output
```

## In CI

```yaml
- name: MCP annotation hygiene
  run: pip install hintgap && hintgap src/
```

## This is opinionated hygiene, not a security scanner

hintgap has a point of view: **every MCP tool should declare its blast radius,
and a mutating tool should say so out loud.** That is a convention, not a
vulnerability — an unannotated tool is not exploitable on its own. hintgap is a
low-false-positive nudge toward a safer default, meant to run green once your
server is annotated and stay green.

It reads only *presence*: whether a hint or description exists, never whether the
values are *correct*. A tool marked `readOnlyHint=True` that secretly writes will
pass — hintgap can't know your implementation, and by design never runs it. The
name-based `HG-WRITE-NOHINT` heuristic keys on a curated set of mutating verbs as
whole tokens, so read tools (`get_*`, `list_*`, `fetch_*`) never trip it.

### The annotation is the permission model

Treat a tool's annotations and description as **security-relevant public schema**,
not documentation. They ship to any client that lists your tools, so they must
never carry secrets, internal hostnames, or auth hints — but they also *define
what the agent believes it is allowed to do*, so review them like an authorization
policy, not a changelog. When a description is missing, the agent guesses the
argument shape and the server often accepts a wider call than a human would have.
And when the agent picks the wrong tool, log the **decision context, not the
payload**: the tool name chosen, which annotations were present or absent at call
time, and the *shape* of the arguments (keys and types, not values). That tells
you whether the miss was a missing hint, an ambiguous description, or a genuine
model error — which is exactly the signal you feed back into tightening the schema.

### Not the same as mcpwary / toolstrict / toolscribe

Three neighbours look at MCP tools and each checks a *different* thing:

- **mcpwary** audits a tool-manifest JSON for *security risk* — shell execution,
  unscoped file paths, SSRF, prompt-injection in descriptions. It reasons about
  what a tool's *schema and description* expose. hintgap reasons about whether the
  *annotation hints* exist at all, from source.
- **toolstrict** lints a tool's *input schema* for strictness (`required`,
  `additionalProperties`, types). That's about argument validity, not blast radius.
- **toolscribe** *generates* tool descriptions. hintgap only reports that one is
  missing; it never writes content.

hintgap occupies the remaining gap: the four MCP *safety annotations*, checked
statically from the server source.

## License

MIT © Jay Tank
