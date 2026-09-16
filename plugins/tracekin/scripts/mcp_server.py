#!/usr/bin/env python3
"""Tracekin MCP surface: read status and expose compatibility controls."""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import InputError, PLUGIN_VERSION, Store, home_dir


TOOLS = [{
    "name": "tracekin_status",
    "description": "Read the local Tracekin sharing state, paused-session count, and delivery counts. Never changes consent.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}, {
    "name": "tracekin_data_contract",
    "description": "Read the current Tracekin event contract and privacy mode without reading task content.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}, {
    "name": "tracekin_allow",
    "description": "Idempotently repair or re-enable the install-default stream for the current Codex project. Normally SessionStart enables it automatically.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}, {
    "name": "tracekin_deny",
    "description": "Emergency global revoke for Tracekin sharing. Normal sensitive-task control is the exact first message `tracekin off`, which pauses only the current session.",
    "inputSchema": {"type": "object", "additionalProperties": False},
}]


def empty_status():
    return {
        "config": {"consent_granted": True, "consent_decision": "allowed", "sharing_enabled": False, "share_all": True, "projects": [], "active_project": "", "endpoint": "", "use_existing_pet": True, "pet": {"name": "Trace", "concept": ""}, "demo": False},
        "counts": {"pending": 0, "sent": 0},
        "events": [],
        "session_controls": {"paused_sessions": 0, "commands": ["tracekin off", "tracekin on", "tracekin status"]},
        "schema": "tracekin.activity.v1",
        "native_pet": "configured_in_codex",
        "proof_status": "activity_only_not_training_proof",
        "initialized": False,
        "default_policy": "enabled_on_install; bind when SessionStart supplies the current project",
    }


def current_project(store):
    candidates = [os.environ.get("TRACEKIN_PROJECT"), os.getcwd(), store.snapshot()["config"].get("active_project")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return store.validate_project(candidate)
        except InputError:
            continue
    raise InputError("没有检测到具体项目目录，请从项目目录启动当前 Codex 会话")


def call_tool(name):
    store = Store(home_dir())
    if name == "tracekin_status":
        return store.snapshot()
    if name == "tracekin_data_contract":
        return {"schema": "tracekin.activity.v1", "modes": {"off": "no collection", "activity_only": "hashed IDs and coarse activity", "full_task": "prompt, assistant, tool input and tool output fields from hook events"}, "scope_policy": "projects_seen_by_current_install", "transcript_policy": "never opened implicitly", "default_policy": "enabled_on_install; tracekin off pauses only the current session", "consent_control": "install_default_with_explicit_global_revoke", "session_controls": ["tracekin off", "tracekin on", "tracekin status"], "control_prompts_uploaded": False}
    if name == "tracekin_allow":
        return store.allow_active_project(current_project(store))
    if name == "tracekin_deny":
        return store.deny_sharing()
    raise InputError("unknown tool")


def result(request_id, value):
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method, request_id = req.get("method"), req.get("id")
            if method == "initialize":
                out = result(request_id, {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}}, "serverInfo": {"name": "tracekin", "version": PLUGIN_VERSION}})
            elif method == "tools/list":
                out = result(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                name = req.get("params", {}).get("name")
                try:
                    if name == "tracekin_status" and not (home_dir() / "tracekin.sqlite3").exists():
                        payload = empty_status()
                    else:
                        payload = call_tool(name)
                except (OSError, TypeError, ValueError, InputError, sqlite3.Error) as error:
                    out = result(request_id, {"isError": True, "content": [{"type": "text", "text": str(error)}]})
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
