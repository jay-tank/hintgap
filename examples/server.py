"""A small FastMCP-style server that mixes well-annotated and gap-ridden tools.

Run hintgap over this file to see each rule fire:

    $ hintgap examples/server.py

Only the deliberately-unsafe registrations are flagged; the annotated ones and
the '# hintgap: ignore' line are left alone.
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("billing")


# GOOD: a read tool that says so — no findings.
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def get_invoice(invoice_id: str) -> str:
    """Fetch a single invoice by id."""
    return f"invoice {invoice_id}"


# GOOD: a mutating tool that declares its blast radius — no findings.
@mcp.tool(
    description="Permanently delete an invoice.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
def delete_invoice(invoice_id: str) -> str:
    return f"deleted {invoice_id}"


# BAD: HG-NOHINTS (no annotations) — and it also has a docstring, so no HG-NODESC.
@mcp.tool()
def list_customers() -> list[str]:
    """List every customer."""
    return []


# BAD: HG-WRITE-NOHINT + HG-NOHINTS — a payment tool with no hints at all.
@mcp.tool()
def send_payment(customer_id: str, amount: int) -> str:
    """Charge a customer and send the payment."""
    return "ok"


# BAD: HG-NODESC + HG-NOHINTS — no docstring, no description, no annotations.
@mcp.tool()
def refresh_cache():
    return True


# Suppressed on purpose: reviewed and accepted as low-risk internal helper.
@mcp.tool()  # hintgap: ignore
def ping() -> str:
    return "pong"
