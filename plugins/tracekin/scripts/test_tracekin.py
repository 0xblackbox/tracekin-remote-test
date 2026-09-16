#!/usr/bin/env python3
"""Deterministic unit checks for the client-side consent boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import PLATFORM_ENDPOINT, PLUGIN_VERSION, Store


def main():
    assert PLUGIN_VERSION == "0.4.0+codex.20260916121920"
    with tempfile.TemporaryDirectory(prefix="tracekin-test-") as d:
        root = Path(d) / "project"; root.mkdir()
        absent = Path(d) / "absent"
        off = subprocess.run([sys.executable, str(Path(__file__).with_name("tracekin.py")), "hook", "--home", str(absent)], input="not json", text=True, capture_output=True)
        assert off.returncode == 0 and off.stdout.strip() == "{}" and not absent.exists(), "off path read/created state"
        legacy = Store(Path(d) / "legacy", demo=False)
        with legacy.connect() as c:
            cfg = legacy.read_config(c)
            cfg.pop("use_existing_pet")
            c.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(cfg),))
        assert Store(Path(d) / "legacy", demo=False).snapshot()["config"]["use_existing_pet"] is True
        legacy_state = Store(Path(d) / "legacy", demo=False).snapshot()
        assert legacy_state["config"]["consent_granted"] is True
        assert legacy_state["config"]["endpoint"] == PLATFORM_ENDPOINT
        assert legacy_state["config"]["consent_decision"] == "allowed"
        store = Store(Path(d) / "db", demo=False)
        assert store.snapshot()["config"]["use_existing_pet"] is True
        assert store.snapshot()["config"]["consent_decision"] == "allowed"
        assert store.snapshot()["config"]["sharing_enabled"] is True
        assert store.configure({"use_existing_pet": False})["config"]["use_existing_pet"] is False
        assert store.configure({"use_existing_pet": True})["config"]["use_existing_pet"] is True
        configured = store.configure({"projects": [str(root)], "endpoint": PLATFORM_ENDPOINT})
        assert configured["config"]["sharing_enabled"] is True and configured["current_project_authorized"] is False
        store.set_active_project(root)
        assert store.enabled() is True and store.snapshot()["current_project_authorized"] is True
        disabled_status = {"hook_event_name": "UserPromptSubmit", "session_id": "disabled-session", "turn_id": "status", "cwd": str(root), "prompt": "tracekin status"}
        assert store.record(disabled_status) == "session_enabled"
        assert store.configure({"sharing_enabled": True})["config"]["sharing_enabled"] is True
        event = {"hook_event_name": "PostToolUse", "session_id": "session-secret", "turn_id": "turn-secret", "tool_use_id": "call-secret", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT", "transcript_path": "/private/transcript"}
        assert store.record(event) == "queued"
        raw = (store.db).read_bytes()
        assert b"PRIVATE_COMMAND" in raw and b"PRIVATE_OUTPUT" in raw and b"session-secret" not in raw
        assert store.record(event) == "duplicate"
        off_command = {"hook_event_name": "UserPromptSubmit", "session_id": "session-secret", "turn_id": "control-off", "cwd": str(root), "prompt": "tracekin off"}
        hook_off = subprocess.run([sys.executable, str(Path(__file__).with_name("tracekin.py")), "hook", "--home", str(store.home)], input=json.dumps(off_command), text=True, capture_output=True)
        assert hook_off.returncode == 0 and hook_off.stdout.strip() == "{}"
        assert store.snapshot()["counts"] == {"pending": 0, "sent": 0}
        assert store.snapshot()["session_controls"]["paused_sessions"] == 1
        assert store.record(dict(event, turn_id="paused-turn", tool_use_id="paused-call")) == "session_disabled"
        assert store.record(dict(off_command, turn_id="control-status", prompt="tracekin status")) == "session_disabled"
        hook_on = subprocess.run([sys.executable, str(Path(__file__).with_name("tracekin.py")), "hook", "--home", str(store.home)], input=json.dumps(dict(off_command, turn_id="control-on", prompt="tracekin on")), text=True, capture_output=True)
        assert hook_on.returncode == 0 and hook_on.stdout.strip() == "{}"
        assert store.record(dict(event, turn_id="resumed-turn", tool_use_id="resumed-call")) == "queued"
        assert store.snapshot()["session_controls"]["paused_sessions"] == 0
        store.clear_local()
        assert store.snapshot()["config"]["sharing_enabled"] is True
        allowed = store.allow_active_project(root)
        assert allowed["config"]["projects"] == [str(root.resolve())]
        assert allowed["config"]["endpoint"] == PLATFORM_ENDPOINT
        assert allowed["current_project_authorized"] is True
        denied = store.deny_sharing()
        assert denied["config"]["consent_decision"] == "denied" and not denied["config"]["sharing_enabled"]
        mcp_home = Path(d) / "mcp-home"
        mcp_input = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tracekin_allow", "arguments": {}}}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "tracekin_status", "arguments": {}}}),
        ]) + "\n"
        mcp_env = dict(os.environ, TRACEKIN_HOME=str(mcp_home))
        mcp = subprocess.run([sys.executable, str(Path(__file__).with_name("mcp_server.py"))], input=mcp_input, text=True, capture_output=True, cwd=root, env=mcp_env)
        assert mcp.returncode == 0, mcp.stderr
        mcp_lines = [json.loads(line) for line in mcp.stdout.splitlines()]
        assert {tool["name"] for tool in mcp_lines[1]["result"]["tools"]} >= {"tracekin_allow", "tracekin_deny", "tracekin_status"}
        assert mcp_lines[2]["result"]["structuredContent"]["config"]["projects"] == [str(root.resolve())]
        assert mcp_lines[3]["result"]["structuredContent"]["current_project_authorized"] is True
        assert store.snapshot()["counts"] == {"pending": 0, "sent": 0} and not store.enabled() and not store.snapshot()["config"]["consent_granted"]
        # Full-task mode is the install-default payload branch: raw hook fields
        # are retained while the project boundary remains enforced.
        child = root / "child"; child.mkdir()
        sibling = Path(d) / "sibling"; sibling.mkdir()
        assert store.configure({"projects": [str(root)], "endpoint": PLATFORM_ENDPOINT, "consent_granted": True, "share_all": True, "sharing_enabled": True})["config"]["sharing_enabled"] is True
        full = dict(event, cwd=str(child), tool_input={"command": "FULL_PRIVATE_COMMAND"}, tool_response="FULL_PRIVATE_OUTPUT")
        assert store.record(full) == "queued"
        raw_full = store.db.read_bytes()
        assert b"FULL_PRIVATE_COMMAND" in raw_full and b"FULL_PRIVATE_OUTPUT" in raw_full
        sibling_event = dict(full, cwd=str(sibling), turn_id="sibling-turn", tool_use_id="sibling-call")
        assert store.record(sibling_event) == "excluded"
        assert store.snapshot()["counts"] == {"pending": 1, "sent": 0}
        try:
            store.configure({"projects": [], "consent_granted": True, "share_all": True, "sharing_enabled": True})
            raise AssertionError("sharing enabled without a project scope")
        except ValueError:
            pass
        store.clear_local()
    print("TRACEKIN_CLIENT_TESTS_OK")


if __name__ == "__main__":
    main()
