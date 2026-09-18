import json
import subprocess
import sys
from pathlib import Path

import pytest

import hintgap

ROOT = Path(__file__).resolve().parent.parent


def rules(source: str) -> list[str]:
    return [f.rule for f in hintgap.scan_source(source, "t.py")]


def tools(source: str):
    return {f.tool for f in hintgap.scan_source(source, "t.py")}


# --------------------------------------------------------------------------
# True positives
# --------------------------------------------------------------------------


def test_fastmcp_tool_no_hints_flagged():
    src = (
        "@mcp.tool()\n"
        "def get_thing():\n"
        "    'read a thing'\n"
        "    return 1\n"
    )
    assert "HG-NOHINTS" in rules(src)


def test_delete_tool_not_marked_destructive_flagged():
    src = (
        "@mcp.tool()\n"
        "def delete_user(uid):\n"
        "    'remove a user'\n"
        "    return 1\n"
    )
    r = rules(src)
    assert "HG-WRITE-NOHINT" in r
    assert "HG-NOHINTS" in r


def test_write_verb_detected_various():
    for verb in ("create_order", "updateRecord", "sendEmail", "pay_invoice", "purge_logs"):
        src = f"@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\ndef {verb}():\n    'x'\n    return 1\n"
        assert "HG-WRITE-NOHINT" in rules(src), verb


def test_no_description_flagged():
    src = "@mcp.tool()\ndef doer():\n    return 1\n"
    assert "HG-NODESC" in rules(src)


def test_lowlevel_tool_construct_no_hints_flagged():
    src = (
        "types.Tool(name='delete_file', description='del', "
        "inputSchema={'type': 'object'})\n"
    )
    r = rules(src)
    assert "HG-NOHINTS" in r
    assert "HG-WRITE-NOHINT" in r


def test_lowlevel_tool_missing_description():
    src = "types.Tool(name='stat_file', inputSchema={'type': 'object'})\n"
    assert "HG-NODESC" in rules(src)


# --------------------------------------------------------------------------
# True negatives
# --------------------------------------------------------------------------


def test_readonly_annotated_tool_not_flagged():
    src = (
        "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_thing():\n"
        "    'read a thing'\n"
        "    return 1\n"
    )
    assert rules(src) == []


def test_destructive_annotated_write_tool_not_flagged():
    src = (
        "@mcp.tool(description='d', annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))\n"
        "def delete_user(uid):\n"
        "    'remove'\n"
        "    return 1\n"
    )
    assert rules(src) == []


def test_dict_annotations_supported():
    src = (
        "@mcp.tool(annotations={'destructiveHint': True}, description='d')\n"
        "def delete_user(uid):\n"
        "    return 1\n"
    )
    # destructive declared -> no write finding; has description; has hints.
    assert rules(src) == []


def test_read_verb_never_trips_write_rule():
    src = (
        "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_report():\n"
        "    'read'\n"
        "    return 1\n"
    )
    assert "HG-WRITE-NOHINT" not in rules(src)


def test_docstring_counts_as_description():
    src = (
        "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_thing():\n"
        "    'this is a docstring description'\n"
        "    return 1\n"
    )
    assert "HG-NODESC" not in rules(src)


def test_non_mcp_decorator_ignored():
    src = "@app.route('/x')\ndef view():\n    return 1\n"
    assert rules(src) == []


def test_tool_class_without_name_ignored():
    # Not an MCP tool definition (no name=) -> low FP.
    src = "widget = Tool(color='red')\n"
    assert rules(src) == []


def test_write_verb_only_as_substring_not_matched():
    # 'category' contains 'cat' not a write verb; 'settings' contains 'set' as a
    # token only if split — ensure 'preset_get' style does not false-positive on
    # a genuine read tool.
    src = (
        "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_category():\n"
        "    'read'\n"
        "    return 1\n"
    )
    assert "HG-WRITE-NOHINT" not in rules(src)


# --------------------------------------------------------------------------
# Suppression
# --------------------------------------------------------------------------


def test_suppression_on_decorator_line():
    src = (
        "@mcp.tool()  # hintgap: ignore\n"
        "def delete_user(uid):\n"
        "    return 1\n"
    )
    assert rules(src) == []


def test_suppression_on_def_line():
    src = (
        "@mcp.tool()\n"
        "def delete_user(uid):  # hintgap: ignore\n"
        "    return 1\n"
    )
    assert rules(src) == []


def test_suppression_on_construct():
    src = "types.Tool(name='delete_file')  # hintgap: ignore\n"
    assert rules(src) == []


# --------------------------------------------------------------------------
# --select / naming
# --------------------------------------------------------------------------


def test_select_narrows_rules():
    src = "@mcp.tool()\ndef delete_user(uid):\n    return 1\n"
    found = {f.rule for f in hintgap.scan_source(src, "t.py", select={"HG-WRITE-NOHINT"})}
    assert found == {"HG-WRITE-NOHINT"}


def test_name_kwarg_used_for_verb():
    src = "@mcp.tool(name='delete_everything', annotations=ToolAnnotations(readOnlyHint=True))\ndef handler():\n    'x'\n    return 1\n"
    assert "HG-WRITE-NOHINT" in rules(src)


# --------------------------------------------------------------------------
# CLI / integration
# --------------------------------------------------------------------------


def test_examples_server_flags_expected(tmp_path):
    findings = hintgap.scan_path(ROOT / "examples" / "server.py")
    flagged = {f.tool for f in findings}
    assert "send_payment" in flagged
    assert "refresh_cache" in flagged
    assert "get_invoice" not in flagged
    assert "delete_invoice" not in flagged
    assert "ping" not in flagged  # suppressed


def test_cli_exit_codes(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("@mcp.tool()\ndef delete_x():\n    return 1\n")
    assert hintgap.main([str(bad)]) == 1

    good = tmp_path / "good.py"
    good.write_text(
        "@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_x():\n    'r'\n    return 1\n"
    )
    assert hintgap.main([str(good)]) == 0

    assert hintgap.main([str(tmp_path / "nope.py")]) == 2


def test_cli_json_shape(tmp_path, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("@mcp.tool()\ndef delete_x():\n    return 1\n")
    hintgap.main([str(bad), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] >= 1
    assert out["exit_code"] == 1
    assert {"rule", "file", "line", "tool", "message"} <= set(out["findings"][0])


def test_cli_bad_select_returns_2(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("x = 1\n")
    assert hintgap.main([str(f), "--select", "HG-BOGUS"]) == 2


def test_subprocess_smoke():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "hintgap.py"), str(ROOT / "examples" / "server.py")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "HG-" in proc.stdout
