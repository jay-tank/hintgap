# hintgap — usage

## Synopsis

```
hintgap [PATH] [--select RULES] [--json] [--version]
```

- `PATH` — a `.py` file or a directory (recursed). Default: `.`
- `--select` — comma-separated subset of rules to enable. Default: all.
- `--json` — machine-readable output.
- `--version` — print version and exit.

Exit codes: `0` clean · `1` gaps found · `2` usage error (missing path, unknown
rule).

## Rules

### HG-NOHINTS
A tool registered with no annotation hints at all. Add a `ToolAnnotations(...)`
(or dict) declaring the relevant hints:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_invoice(invoice_id: str) -> str: ...
```

### HG-WRITE-NOHINT
A tool whose name contains a mutating verb (`create`, `update`, `delete`,
`write`, `send`, `pay`, `remove`, `set`, `modify`, `insert`, `drop`, `charge`,
`refund`, `transfer`, `publish`, `deploy`, and more) that does not declare its
mutating nature. Fix it by marking it honestly:

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def delete_invoice(invoice_id: str) -> str: ...
```

`destructiveHint=True` **or** `readOnlyHint=False` clears the rule — either is an
explicit statement that the tool is not a safe read.

### HG-NODESC
A tool with no description. For a FastMCP decorator, a function docstring counts;
otherwise pass `description=`. For low-level `Tool(...)`, pass a non-empty
`description=`.

## Recognised registrations

- FastMCP: `@mcp.tool`, `@mcp.tool()`, `@server.tool(...)`, `@app.tool(...)` —
  any decorator whose attribute is `.tool`.
- Low-level SDK: `Tool(...)` / `types.Tool(...)` calls that carry a `name=`
  argument (the `name=` guard keeps false positives off unrelated `Tool` classes).

Annotations are read from a `ToolAnnotations(...)` call, a `{...}` dict, or direct
keyword arguments.

## Suppression

Put `# hintgap: ignore` anywhere on the registration's lines (the decorator line,
the `def` line, or the `Tool(` call). Every rule for that tool is skipped.

## Design notes / limits

- **Presence, not correctness.** hintgap checks that a hint or description
  *exists*, never that its value is truthful. A mislabelled tool passes.
- **Static only.** It parses source with `ast`; it never imports or runs your
  code, and makes no network calls.
- **Heuristic naming.** `HG-WRITE-NOHINT` keys on verb *tokens* (snake_case and
  camelCase are split), so `get_category` is not mistaken for a write.
- **Python only** in this version. Tools defined in TypeScript/other SDKs are out
  of scope.
