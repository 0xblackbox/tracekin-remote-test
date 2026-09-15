#!/usr/bin/env python3
"""In-process loopback receiver test; disposable demo data only."""
import tempfile
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).parent))
from serve import App
from tracekin import send_https

with tempfile.TemporaryDirectory(prefix="tracekin-integration-") as d:
    project = Path(d) / "project"; project.mkdir()
    app = App(Path(d) / "state", demo=True, project=project)
    app.store.configure({"projects": [], "share_all": True})
    off = app.snapshot(); assert not off["config"]["sharing_enabled"]
    app.store.configure({"consent_granted": True, "sharing_enabled": True})
    app.sample()
    queued = app.snapshot()["counts"]
    assert queued == {"pending": 2, "sent": 0}
    control = {"hook_event_name": "UserPromptSubmit", "session_id": "live-session", "turn_id": "off", "cwd": str(project), "prompt": "tracekin off"}
    assert app.store.record(control) == "session_disabled"
    assert app.store.record(dict(control, turn_id="paused", prompt="ordinary private prompt")) == "session_disabled"
    assert app.store.record(dict(control, turn_id="on", prompt="tracekin on")) == "session_enabled"
    assert app.store.deliver_one(send_https) == "sent"
    assert app.store.deliver_one(send_https) == "sent"
    delivered = app.snapshot()
    assert delivered["counts"] == {"pending": 0, "sent": 2} and delivered["demo_received"] == 2
    cleared = app.store.clear_local()
    assert not cleared["config"]["sharing_enabled"] and cleared["counts"] == {"pending": 0, "sent": 0}
    app.collector.shutdown()
print("INTEGRATION_OFF False {'pending': 0, 'sent': 0}")
print("INTEGRATION_ON True")
print("INTEGRATION_SESSION_CONTROL off=disabled paused=blocked on=enabled")
print("INTEGRATION_QUEUED {'pending': 2, 'sent': 0}")
print("INTEGRATION_DELIVERED {'pending': 0, 'sent': 2}")
print("INTEGRATION_CLEARED False {'pending': 0, 'sent': 0}")
