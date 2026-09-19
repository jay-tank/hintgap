"""Low-level MCP SDK style: tools built as `types.Tool(...)` and returned from a
`list_tools` handler, dispatched by an `@app.call_tool` handler.

    $ hintgap examples/lowlevel.py
"""

import mcp.types as types
from mcp.server import Server

app = Server("files")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # GOOD: annotated read tool.
        types.Tool(
            name="read_file",
            description="Read a file's contents.",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        # BAD: HG-WRITE-NOHINT + HG-NOHINTS — deletes, no annotations.
        types.Tool(
            name="delete_file",
            description="Delete a file at the given path.",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
        # BAD: HG-NOHINTS + HG-NODESC — no description, no annotations.
        types.Tool(
            name="stat_file",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    return [types.TextContent(type="text", text="ok")]
