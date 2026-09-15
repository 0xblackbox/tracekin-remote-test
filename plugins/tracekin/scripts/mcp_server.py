#!/usr/bin/env python3
"""Read-only Tracekin MCP surface. Consent and transmission remain in the client UI/hooks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import Store, home_dir


TOOLS = [{
    "name": "tracekin_status",
    "description": "Read the local Tracekin sharing state and redacted delivery counts. Never changes consent.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}, {
    "name": "tracekin_data_contract",
    "description": "Read the current Tracekin event contract and privacy mode without reading task content.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}]


def result(request_id, value):
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method, request_id = req.get("method"), req.get("id")
            if method == "initialize":
                out = result(request_id, {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "tracekin", "version": "0.1.0"}})
            elif method == "tools/list":
                out = result(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                name = req.get("params", {}).get("name")
                if name == "tracekin_status":
                    payload = Store(home_dir(), create=False).snapshot() if (home_dir() / "tracekin.sqlite3").exists() else {"config": {"sharing_enabled": False}, "counts": {"pending": 0, "sent": 0}, "schema": "tracekin.activity.v1"}
                elif name == "tracekin_data_contract":
                    payload = {"schema": "tracekin.activity.v1", "modes": {"off": "no collection", "activity_only": "hashed IDs and coarse activity", "full_task": "prompt, assistant, tool input and tool output fields from hook events"}, "transcript_policy": "never opened implicitly", "consent_control": "client_panel_only"}
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
