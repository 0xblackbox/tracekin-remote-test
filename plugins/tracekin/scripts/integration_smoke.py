#!/usr/bin/env python3
"""In-process loopback receiver test; disposable demo data only."""
import json
import tempfile
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from serve import App, handler_for
from tracekin import send_https
from http.server import ThreadingHTTPServer

with tempfile.TemporaryDirectory(prefix="tracekin-integration-") as d:
    project = Path(d) / "project"; project.mkdir()
    app = App(Path(d) / "state", demo=True, project=project)
    assert app.snapshot()["config"]["projects"] == [str(project.resolve())]
    off = app.snapshot(); assert off["config"]["sharing_enabled"] and off["current_project_authorized"]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(app))
    server_thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers={"Host": f"127.0.0.1:{server.server_port}", "Origin": base, "X-Tracekin-Token": app.token}), timeout=3) as response:
        api_state = json.loads(response.read())
    assert api_state["current_project_authorized"] is True and api_state["config"]["consent_decision"] == "allowed"
    server.shutdown(); server.server_close()
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
    assert cleared["config"]["sharing_enabled"] and cleared["counts"] == {"pending": 0, "sent": 0}
    app.collector.shutdown()
print("INTEGRATION_DEFAULT True {'pending': 0, 'sent': 0}")
print("INTEGRATION_CLEAR_LOCAL True {'pending': 0, 'sent': 0}")
print("INTEGRATION_SESSION_CONTROL off=disabled paused=blocked on=enabled")
print("INTEGRATION_QUEUED {'pending': 2, 'sent': 0}")
print("INTEGRATION_DELIVERED {'pending': 0, 'sent': 2}")
print("INTEGRATION_CLEARED True {'pending': 0, 'sent': 0}")
