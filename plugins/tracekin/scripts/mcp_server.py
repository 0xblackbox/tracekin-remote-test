#!/usr/bin/env python3
"""Read-only Tracekin MCP surface. Consent stays in the panel; session commands run in hooks."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import Store, home_dir


TOOLS = [{
    "name": "tracekin_status",
    "description": "Read the local Tracekin sharing state, paused-session count, and delivery counts. Never changes consent.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}, {
    "name": "tracekin_data_contract",
    "description": "Read the current Tracekin event contract and privacy mode without reading task content.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}]


def empty_status():
    return {
        "config": {"consent_granted": False, "sharing_enabled": False, "share_all": False, "projects": [], "endpoint": "", "use_existing_pet": True, "pet": {"name": "Trace", "concept": ""}, "demo": False},
        "counts": {"pending": 0, "sent": 0},
        "events": [],
        "session_controls": {"paused_sessions": 0, "commands": ["tracekin off", "tracekin on", "tracekin status"]},
        "schema": "tracekin.activity.v1",
        "native_pet": "configured_in_codex",
        "proof_status": "activity_only_not_training_proof",
        "initialized": False,
    }


def result(request_id, value):
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method, request_id = req.get("method"), req.get("id")
            if method == "initialize":
                out = result(request_id, {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "tracekin", "version": "0.2.2"}})
            elif method == "tools/list":
                out = result(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                name = req.get("params", {}).get("name")
                if name == "tracekin_status":
                    if (home_dir() / "tracekin.sqlite3").exists():
                        try:
                            payload = Store(home_dir(), create=False).snapshot()
                        except (OSError, TypeError, ValueError, sqlite3.Error):
                            payload = empty_status()
                    else:
                        payload = empty_status()
                elif name == "tracekin_data_contract":
                    payload = {"schema": "tracekin.activity.v1", "modes": {"off": "no collection", "activity_only": "hashed IDs and coarse activity", "full_task": "prompt, assistant, tool input and tool output fields from hook events"}, "scope_policy": "configured_projects_only", "transcript_policy": "never opened implicitly", "consent_control": "one_time_client_panel_opt_in", "session_controls": ["tracekin off", "tracekin on", "tracekin status"], "control_prompts_uploaded": False}
                else:
                    out = result(request_id, {"isError": True, "content": [{"type": "text", "text": "unknown tool"}]})
                    print(json.dumps(out, ensure_ascii=False), flush=True); continue
                out = result(request_id, {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload})
            elif method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            else:
                out = result(request_id, {"error": "method not supported"})
            print(json.dumps(out, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, OSError):
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "invalid request"}}), flush=True)


if __name__ == "__main__":
    main()
