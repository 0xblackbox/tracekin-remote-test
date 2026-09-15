#!/usr/bin/env python3
"""Deterministic unit checks for the client-side consent boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import PLUGIN_VERSION, Store


def main():
    assert PLUGIN_VERSION == "0.2.0+codex.20260916015659"
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
        assert Store(Path(d) / "legacy", demo=False).snapshot()["config"]["consent_granted"] is False
        store = Store(Path(d) / "db", demo=False)
        assert store.snapshot()["config"]["use_existing_pet"] is True
        assert store.configure({"use_existing_pet": False})["config"]["use_existing_pet"] is False
        assert store.configure({"use_existing_pet": True})["config"]["use_existing_pet"] is True
        store.configure({"projects": [str(root)], "endpoint": "https://collector.invalid/ingest"})
        assert store.enabled() is False
        disabled_status = {"hook_event_name": "UserPromptSubmit", "session_id": "disabled-session", "turn_id": "status", "cwd": str(root), "prompt": "tracekin status"}
        assert store.record(disabled_status) == "global_disabled"
        try:
            store.configure({"sharing_enabled": True})
            raise AssertionError("sharing enabled without consent")
        except ValueError:
            pass
        assert store.configure({"consent_granted": True, "sharing_enabled": True})["config"]["sharing_enabled"] is True
        event = {"hook_event_name": "PostToolUse", "session_id": "session-secret", "turn_id": "turn-secret", "tool_use_id": "call-secret", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT", "transcript_path": "/private/transcript"}
        assert store.record(event) == "queued"
        raw = (store.db).read_bytes()
        assert b"PRIVATE_COMMAND" not in raw and b"PRIVATE_OUTPUT" not in raw and b"session-secret" not in raw
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
        assert store.snapshot()["counts"] == {"pending": 0, "sent": 0} and not store.enabled() and not store.snapshot()["config"]["consent_granted"]
        # Full-task mode is a deliberate second consent branch: raw hook fields are retained,
        # including a synthetic sentinel, and it is global rather than project-scoped.
        assert store.configure({"consent_granted": True, "share_all": True, "sharing_enabled": True})["config"]["sharing_enabled"] is True
        full = dict(event, cwd=str(Path(d) / "outside"), tool_input={"command": "FULL_PRIVATE_COMMAND"}, tool_response="FULL_PRIVATE_OUTPUT")
        assert store.record(full) == "queued"
        raw_full = store.db.read_bytes()
        assert b"FULL_PRIVATE_COMMAND" in raw_full and b"FULL_PRIVATE_OUTPUT" in raw_full
        store.clear_local()
    print("TRACEKIN_CLIENT_TESTS_OK")


if __name__ == "__main__":
    main()
