#!/usr/bin/env python3
"""hintgap — flag MCP tool definitions that ship without safety annotations.

The Model Context Protocol lets a tool declare *behavioural hints* alongside its
name and schema — ``readOnlyHint``, ``destructiveHint``, ``idempotentHint`` and
``openWorldHint``. Those hints are how a client or an autonomous agent reasons
about a tool's blast radius *before* it calls it: is this a safe read, or does it
mutate, delete, spend money, or reach out to the open world? A tool registered
without them leaves the agent blind — it cannot tell ``get_invoice`` apart from
``delete_invoice`` except by guessing at the name.

``hintgap`` parses Python source with the stdlib ``ast`` module, finds MCP tool
registrations, and reports the ones that leave that reasoning surface empty:

  * **HG-NOHINTS**     — a tool registered with no annotation hints at all.
  * **HG-WRITE-NOHINT**— a tool whose name implies mutation (create / update /
    delete / write / send / pay / ...) that is not marked ``destructiveHint``
    and does not explicitly set ``readOnlyHint=False``. The most dangerous gap:
    a mutating tool the agent may treat as safe.
  * **HG-NODESC**      — a tool with no description (no ``description=`` and, for
    a decorated function, no docstring). An undocumented tool is a tool the
    model has to guess the purpose of.

Recognised registration shapes:

  * FastMCP decorators — ``@mcp.tool()`` / ``@server.tool()`` / ``@app.tool()``
    (with or without a call, with or without arguments).
  * Low-level SDK construction — ``types.Tool(...)`` / ``Tool(...)`` (as returned
    from a ``list_tools`` handler, adjacent to an ``@app.call_tool`` dispatcher).

hintgap is deliberately opinionated MCP hygiene, and deliberately low-FP: it only
looks at real tool registrations, reads only annotation/description presence
(never their prose), makes no network calls, and never imports or runs your code.
Silence a line you have judged fine with a ``# hintgap: ignore`` comment.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Signals / vocabulary
# ---------------------------------------------------------------------------

# The four MCP tool annotation hints.
HINT_NAMES = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Attribute name that marks a FastMCP tool decorator: `<something>.tool`.
_TOOL_DECORATOR_ATTR = "tool"

# Verbs (as whole tokens) that imply a mutation / side effect. Read verbs
# (get/list/read/fetch/search/...) are intentionally absent: a name that only
# matches read verbs never trips HG-WRITE-NOHINT.
_WRITE_VERBS = frozenset(
    {
        "create",
        "update",
        "delete",
        "write",
        "send",
        "pay",
        "remove",
        "set",
        "modify",
        "insert",
        "drop",
        "put",
        "post",
        "patch",
        "transfer",
        "charge",
        "refund",
        "execute",
        "run",
        "add",
        "edit",
        "save",
        "purge",
        "revoke",
        "publish",
        "deploy",
        "cancel",
        "terminate",
        "kill",
        "reset",
        "move",
        "rename",
        "upload",
        "install",
        "uninstall",
        "grant",
        "issue",
        "submit",
        "approve",
        "reject",
    }
)

_IGNORE_RE = re.compile(r"#\s*hintgap:\s*ignore\b")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".tox",
    ".eggs",
}

_ALL_RULES = ("HG-NOHINTS", "HG-WRITE-NOHINT", "HG-NODESC")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    rule: str
    file: str
    line: int
    tool: str
    message: str


@dataclass
class ToolReg:
    """A discovered MCP tool registration."""

    name: str
    file: str
    line: int  # line to report / anchor suppression
    span: range  # all source lines the registration spans
    kind: str  # "decorator" or "construct"
    hints: dict  # hint name -> value (True/False/None-if-unknown)
    has_description: bool
    write_verb: bool
    reg_hints_present: bool  # any hint key was present at all


# ---------------------------------------------------------------------------
# ast helpers
# ---------------------------------------------------------------------------


def _const_bool(node: ast.AST):
    """Return True/False for a boolean constant node, else None (unknown)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _collect_hints(keywords: list[ast.keyword]) -> dict:
    """Extract annotation hints from a call's keyword arguments.

    Handles hints given directly (``readOnlyHint=True``) and, more commonly, via
    an ``annotations=`` argument that is either a ``ToolAnnotations(...)`` call or
    a plain ``{...}`` dict literal.
    """
    hints: dict = {}

    def take(name: str, value_node: ast.AST) -> None:
        if name in HINT_NAMES:
            hints[name] = _const_bool(value_node)

    for kw in keywords:
        if kw.arg is None:  # **kwargs — cannot inspect
            continue
        if kw.arg in HINT_NAMES:
            take(kw.arg, kw.value)
        elif kw.arg == "annotations":
            val = kw.value
            if isinstance(val, ast.Call):
                for inner in val.keywords:
                    if inner.arg:
                        take(inner.arg, inner.value)
            elif isinstance(val, ast.Dict):
                for k, v in zip(val.keys, val.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        take(k.value, v)
    return hints


def _has_description_kw(keywords: list[ast.keyword]) -> bool:
    for kw in keywords:
        if kw.arg == "description":
            val = kw.value
            # description="" or description=None does not count as documented.
            if isinstance(val, ast.Constant):
                return bool(val.value)
            return True  # a non-constant expression — assume it is real
    return False


def _name_kw(keywords: list[ast.keyword]):
    for kw in keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _tokenize(name: str) -> list[str]:
    parts: list[str] = []
    for chunk in re.split(r"[_\-\s./]+", name):
        if not chunk:
            continue
        parts.extend(p.lower() for p in _CAMEL_RE.split(chunk) if p)
    return parts


def _implies_write(name: str) -> bool:
    return any(tok in _WRITE_VERBS for tok in _tokenize(name))


def _span(node: ast.AST) -> range:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    return range(start, end + 1)


def _is_tool_decorator(dec: ast.AST) -> bool:
    """True if a decorator node is a FastMCP `<obj>.tool` / `<obj>.tool(...)`."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(target, ast.Attribute) and target.attr == _TOOL_DECORATOR_ATTR


def _is_tool_construct(node: ast.Call) -> bool:
    """True if a call constructs an MCP `Tool(...)` / `types.Tool(...)`."""
    func = node.func
    if isinstance(func, ast.Name):
        base = func.id
    elif isinstance(func, ast.Attribute):
        base = func.attr
    else:
        return False
    if base != "Tool":
        return False
    # Low-FP guard: a real tool definition carries a name= argument.
    return _name_kw(node.keywords) is not None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class _Collector(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.regs: list[ToolReg] = []

    def _visit_func(self, node) -> None:
        for dec in node.decorator_list:
            if not _is_tool_decorator(dec):
                continue
            keywords = dec.keywords if isinstance(dec, ast.Call) else []
            hints = _collect_hints(keywords)
            declared_name = _name_kw(keywords) or node.name
            has_desc = _has_description_kw(keywords) or ast.get_docstring(node) is not None
            self.regs.append(
                ToolReg(
                    name=declared_name,
                    file=self.filename,
                    line=getattr(dec, "lineno", node.lineno),
                    span=range(getattr(dec, "lineno", node.lineno), (node.end_lineno or node.lineno) + 1),
                    kind="decorator",
                    hints=hints,
                    has_description=has_desc,
                    write_verb=_implies_write(declared_name),
                    reg_hints_present=bool(hints),
                )
            )
            break  # one tool per function

    def visit_FunctionDef(self, node):  # noqa: N802
        self._visit_func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._visit_func(node)
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: N802
        if _is_tool_construct(node):
            hints = _collect_hints(node.keywords)
            declared_name = _name_kw(node.keywords) or "<tool>"
            self.regs.append(
                ToolReg(
                    name=declared_name,
                    file=self.filename,
                    line=node.lineno,
                    span=_span(node),
                    kind="construct",
                    hints=hints,
                    has_description=_has_description_kw(node.keywords),
                    write_verb=_implies_write(declared_name),
                    reg_hints_present=bool(hints),
                )
            )
        self.generic_visit(node)


def _ignore_lines(source: str) -> set[int]:
    return {i for i, line in enumerate(source.splitlines(), start=1) if _IGNORE_RE.search(line)}


def _destructive_declared(hints: dict) -> bool:
    """A tool declares its mutating nature if destructiveHint is True or it
    explicitly says it is not read-only (readOnlyHint=False)."""
    if hints.get("destructiveHint") is True:
        return True
    if hints.get("readOnlyHint") is False:
        return True
    return False


def _evaluate(reg: ToolReg, select: set[str]) -> list[Finding]:
    out: list[Finding] = []

    if "HG-NODESC" in select and not reg.has_description:
        out.append(
            Finding(
                rule="HG-NODESC",
                file=reg.file,
                line=reg.line,
                tool=reg.name,
                message=(
                    f"tool '{reg.name}' has no description (no description= and no docstring) — "
                    "the agent has nothing to reason about but the name"
                ),
            )
        )

    if "HG-NOHINTS" in select and not reg.reg_hints_present:
        out.append(
            Finding(
                rule="HG-NOHINTS",
                file=reg.file,
                line=reg.line,
                tool=reg.name,
                message=(
                    f"tool '{reg.name}' declares no safety annotations "
                    "(readOnlyHint/destructiveHint/idempotentHint/openWorldHint) — "
                    "the agent cannot reason about its blast radius"
                ),
            )
        )

    if (
        "HG-WRITE-NOHINT" in select
        and reg.write_verb
        and not _destructive_declared(reg.hints)
    ):
        out.append(
            Finding(
                rule="HG-WRITE-NOHINT",
                file=reg.file,
                line=reg.line,
                tool=reg.name,
                message=(
                    f"tool '{reg.name}' has a mutating verb in its name but is not marked "
                    "destructiveHint=True (nor readOnlyHint=False) — an agent may treat a "
                    "write/delete as a safe read"
                ),
            )
        )

    return out


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def scan_source(source: str, filename: str = "<string>", select: set[str] | None = None) -> list[Finding]:
    select = select or set(_ALL_RULES)
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    collector = _Collector(filename)
    collector.visit(tree)

    ignore = _ignore_lines(source)
    findings: list[Finding] = []
    for reg in collector.regs:
        if any(ln in ignore for ln in reg.span):
            continue
        findings.extend(_evaluate(reg, select))
    return findings


def _iter_py_files(root: Path, exclude_dirs: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part in exclude_dirs for part in rel_parts):
            continue
        yield path


def scan_path(target: Path, select: set[str] | None = None, exclude_dirs: set[str] | None = None) -> list[Finding]:
    exclude_dirs = exclude_dirs or set(_DEFAULT_EXCLUDE_DIRS)
    findings: list[Finding] = []
    if target.is_dir():
        files = _iter_py_files(target, exclude_dirs)
    else:
        files = [target]
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_source(source, str(path), select))
    return findings


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_text(findings: list[Finding]) -> str:
    if not findings:
        return "hintgap: no MCP tool annotation gaps found — clean  ·  exit 0"
    lines: list[str] = []
    for f in findings:
        lines.append(f"{f.rule}  {f.file}:{f.line}  ({f.tool})")
        lines.append(f"    {f.message}")
    n = len(findings)
    lines.append("")
    lines.append(f"{n} gap{'s' if n != 1 else ''}  ·  exit 1")
    return "\n".join(lines)


def render_json(findings: list[Finding]) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "rule": f.rule,
                    "file": f.file,
                    "line": f.line,
                    "tool": f.tool,
                    "message": f.message,
                }
                for f in findings
            ],
            "count": len(findings),
            "exit_code": 1 if findings else 0,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hintgap",
        description=(
            "Flag MCP tool definitions that omit safety annotations "
            "(readOnlyHint/destructiveHint/idempotentHint/openWorldHint), so an "
            "agent can reason about a tool's blast radius before calling it."
        ),
        epilog="Silence a line with a '# hintgap: ignore' comment.",
    )
    p.add_argument("path", nargs="?", default=".", help="file or directory to scan (default: .)")
    p.add_argument(
        "--select",
        default=None,
        help="comma-separated rules to enable (default: all): " + ", ".join(_ALL_RULES),
    )
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    p.add_argument("--version", action="version", version=f"hintgap {__version__}")
    return p


def _parse_select(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    chosen = {r.strip() for r in raw.split(",") if r.strip()}
    unknown = chosen - set(_ALL_RULES)
    if unknown:
        raise SystemExit(
            f"hintgap: error: unknown rule(s): {', '.join(sorted(unknown))}. "
            f"valid: {', '.join(_ALL_RULES)}"
        )
    return chosen


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        print(f"hintgap: error: {target} does not exist", file=sys.stderr)
        return 2

    try:
        select = _parse_select(args.select)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    findings = scan_path(target, select)

    if args.json:
        print(render_json(findings))
    else:
        print(render_text(findings))

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
