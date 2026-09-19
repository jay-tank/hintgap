# hintgap examples

Two small MCP servers that exercise every rule. Run hintgap over each and compare
against the annotated notes in the source.

## `server.py` — FastMCP decorators

```bash
$ hintgap examples/server.py
```

- `get_invoice` — `readOnlyHint=True` → **clean**
- `delete_invoice` — `destructiveHint=True` + description → **clean**
- `list_customers` — no annotations → **HG-NOHINTS**
- `send_payment` — no annotations, mutating name → **HG-NOHINTS + HG-WRITE-NOHINT**
- `refresh_cache` — no annotations, no docstring → **HG-NOHINTS + HG-NODESC**
- `ping` — `# hintgap: ignore` → **suppressed**

## `lowlevel.py` — low-level `types.Tool(...)`

```bash
$ hintgap examples/lowlevel.py
```

- `read_file` — `readOnlyHint=True` → **clean**
- `delete_file` — no annotations, mutating name → **HG-NOHINTS + HG-WRITE-NOHINT**
- `stat_file` — no annotations, no description → **HG-NOHINTS + HG-NODESC**

Both files return exit code `1`. Add the missing annotations and they go green.
