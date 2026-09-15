#!/usr/bin/env python3
"""Deterministic unit checks for the client-side consent boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import Store


def main():
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
        store = Store(Path(d) / "db", demo=False)
        assert store.snapshot()["config"]["use_existing_pet"] is True
        assert store.configure({"use_existing_pet": False})["config"]["use_existing_pet"] is False
        assert store.configure({"use_existing_pet": True})["config"]["use_existing_pet"] is True
        store.configure({"projects": [str(root)], "endpoint": "https://collector.invalid/ingest"})
        assert store.enabled() is False
        assert store.configure({"sharing_enabled": True})["config"]["sharing_enabled"] is True
        event = {"hook_event_name": "PostToolUse", "session_id": "session-secret", "turn_id": "turn-secret", "tool_use_id": "call-secret", "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT", "transcript_path": "/private/transcript"}
        assert store.record(event) == "queued"
        raw = (store.db).read_bytes()
        assert b"PRIVATE_COMMAND" not in raw and b"PRIVATE_OUTPUT" not in raw and b"session-secret" not in raw
        assert store.record(event) == "duplicate"
        store.clear_local()
        assert store.snapshot()["counts"] == {"pending": 0, "sent": 0} and not store.enabled()
        # Full-task mode is a deliberate second consent branch: raw hook fields are retained,
        # including a synthetic sentinel, and it is global rather than project-scoped.
        assert store.configure({"share_all": True, "sharing_enabled": True})["config"]["sharing_enabled"] is True
        full = dict(event, cwd=str(Path(d) / "outside"), tool_input={"command": "FULL_PRIVATE_COMMAND"}, tool_response="FULL_PRIVATE_OUTPUT")
        assert store.record(full) == "queued"
        raw_full = store.db.read_bytes()
        assert b"FULL_PRIVATE_COMMAND" in raw_full and b"FULL_PRIVATE_OUTPUT" in raw_full
        store.clear_local()
    print("TRACEKIN_CLIENT_TESTS_OK")


if __name__ == "__main__":
    main()
