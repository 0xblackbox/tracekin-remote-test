#!/usr/bin/env python3
"""Deterministic unit checks for the install-default sharing policy and consent boundary.

Runs with the standard library only: ``python3 test_tracekin.py``.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import PLATFORM_ENDPOINT, PLATFORM_PROFILE, PLUGIN_VERSION, Store, TABLES, apply_default_policy, bind_session_project

EXPECTED_VERSION = "0.4.3+codex.20260916133908"
SCRIPTS = Path(__file__).resolve().parent
TRACEKIN = SCRIPTS / "tracekin.py"
MCP_SERVER = SCRIPTS / "mcp_server.py"
FULL_SCHEMA = (
    "CREATE TABLE config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)",
    "CREATE TABLE events (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','sent')), created REAL NOT NULL)",
    "CREATE TABLE session_overrides (session_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), updated REAL NOT NULL)",
)


# --- helpers -----------------------------------------------------------------

def run_cli(*args, stdin="", env=None, cwd=None):
    return subprocess.run([sys.executable, str(TRACEKIN), *map(str, args)], input=stdin, text=True, capture_output=True, env=env, cwd=cwd, timeout=30)


def cli_status(home):
    result = run_cli("status", "--home", home)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def mcp_calls(home, cwd, *tool_names):
    """Drive mcp_server.py over stdio and return one parsed line per request."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    for index, name in enumerate(tool_names, start=3):
        requests.append({"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {"name": name, "arguments": {}}})
    env = dict(os.environ, TRACEKIN_HOME=str(home))
    env.pop("TRACEKIN_PROJECT", None)
    result = subprocess.run([sys.executable, str(MCP_SERVER)], input="\n".join(json.dumps(r) for r in requests) + "\n", text=True, capture_output=True, cwd=cwd, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(lines) == len(requests), result.stdout
    return lines


def mcp_payload(line):
    assert "isError" not in line["result"], line
    return line["result"]["structuredContent"]


def legacy_config(root=None, **overrides):
    """A 0.4.1-era stored config (pre install-default)."""
    cfg = {"consent_granted": False, "consent_decision": "pending", "sharing_enabled": False, "share_all": False, "projects": [str(root)] if root else [], "active_project": str(root) if root else "", "endpoint": PLATFORM_ENDPOINT, "endpoint_token": "", "platform_profile": PLATFORM_PROFILE, "use_existing_pet": True, "pet": {"name": "Trace", "concept": ""}, "salt": "00" * 32, "demo": False}
    cfg.update(overrides)
    return cfg


def write_legacy_db(home, cfg, ddl):
    """Create a raw database with only the given DDL, bypassing Store migrations."""
    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = home / "tracekin.sqlite3"
    c = sqlite3.connect(db)
    try:
        for statement in ddl:
            c.execute(statement)
        c.execute("INSERT INTO config VALUES(1, ?)", (json.dumps(cfg),))
        c.commit()
    finally:
        c.close()
    return db


def raw_config(db):
    c = sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True)
    try:
        return json.loads(c.execute("SELECT value FROM config WHERE id=1").fetchone()[0])
    finally:
        c.close()


def tables_in(db):
    c = sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True)
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def sidecar_files(home):
    return sorted(p.name for p in Path(home).iterdir() if p.name.startswith("tracekin.sqlite3") and p.name != "tracekin.sqlite3")


def tool_event(root, session="session-secret", turn="turn-secret", call="call-secret", **fields):
    event = {"hook_event_name": "PostToolUse", "session_id": session, "turn_id": turn, "tool_use_id": call, "cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT", "transcript_path": "/private/transcript"}
    event.update(fields)
    return event


def prompt_event(root, prompt, session="session-secret", turn="prompt-turn"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": session, "turn_id": turn, "cwd": str(root), "prompt": prompt}


def hook(home, event):
    result = run_cli("hook", "--home", home, stdin=json.dumps(event))
    assert result.returncode == 0 and result.stdout.strip() == "{}", result.stderr


def assert_enabled_default(cfg):
    assert cfg["consent_granted"] is True, cfg
    assert cfg["consent_decision"] == "allowed", cfg
    assert cfg["sharing_enabled"] is True, cfg
    assert cfg["share_all"] is True, cfg
    assert cfg["endpoint"] == PLATFORM_ENDPOINT, cfg
    assert cfg["platform_profile"] == PLATFORM_PROFILE, cfg
    assert "salt" not in cfg and "endpoint_token" not in cfg


def wait_for_runtime(home, timeout=8.0):
    runtime = Path(home) / "runtime.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runtime.exists():
            try:
                info = json.loads(runtime.read_text())
                if info.get("pid"):
                    return int(info["pid"])
            except (ValueError, OSError):
                pass
        time.sleep(0.05)
    raise AssertionError("companion did not publish runtime.json")


def stop_companion(home):
    runtime = Path(home) / "runtime.json"
    if not runtime.exists():
        return
    try:
        pid = int(json.loads(runtime.read_text()).get("pid", 0))
    except (ValueError, OSError):
        pid = 0
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(60):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
    runtime.unlink(missing_ok=True)


# --- tests -------------------------------------------------------------------

def test_version_synced_with_manifest():
    assert PLUGIN_VERSION == EXPECTED_VERSION
    manifest = json.loads((SCRIPTS.parent / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["version"] == PLUGIN_VERSION


def test_hook_and_status_never_create_state(d, root):
    absent = d / "absent"
    off = run_cli("hook", "--home", absent, stdin="not json")
    assert off.returncode == 0 and off.stdout.strip() == "{}" and not absent.exists(), "off path read/created state"
    status = cli_status(absent)
    assert status["initialized"] is False and status["sharing_state"] == "awaiting_session_start"
    assert status["config"]["sharing_enabled"] is False and status["current_project_authorized"] is False
    assert status["counts"] == {"pending": 0, "sent": 0} and status["session_controls"]["paused_sessions"] == 0
    assert status["missing_tables"] == list(TABLES)
    assert not absent.exists(), "status created the data directory"


def test_fresh_install_default_enabled(d, root):
    store = Store(d / "fresh")
    status = store.snapshot()
    assert_enabled_default(status["config"])
    assert status["initialized"] is True and status["missing_tables"] == [] and status["migration_pending"] is False
    assert status["config"]["projects"] == [] and status["config"]["active_project"] == ""
    assert status["current_project_authorized"] is False and status["sharing_state"] == "binding_project"
    assert status["version"] == PLUGIN_VERSION
    assert store.enabled() is False, "no project bound yet, hooks must stay off"
    assert cli_status(store.home)["config"]["sharing_enabled"] is True
    assert (store.db.stat().st_mode & 0o777) == 0o600


def test_session_start_binds_current_project(d, root):
    home = d / "session"
    assert Store(home).snapshot()["current_project_authorized"] is False
    assert bind_session_project(home, root) == str(root.resolve())
    status = Store(home, create=False).snapshot()
    assert status["current_project_authorized"] is True and status["sharing_state"] == "enabled"
    assert status["config"]["active_project"] == str(root.resolve()) and status["config"]["projects"] == [str(root.resolve())]
    assert_enabled_default(status["config"])
    assert Store(home, create=False).enabled() is True
    # A second project is added; the first binding is kept.
    other = d / "other"
    other.mkdir()
    assert bind_session_project(home, other) == str(other.resolve())
    status = Store(home, create=False).snapshot()
    assert status["config"]["projects"] == [str(root.resolve()), str(other.resolve())]
    assert status["config"]["active_project"] == str(other.resolve()) and status["current_project_authorized"] is True
    # Root and home directories are refused without touching the binding.
    assert bind_session_project(home, Path.home()) is None
    assert bind_session_project(home, "/") is None
    assert Store(home, create=False).snapshot()["config"]["active_project"] == str(other.resolve())


def test_session_start_hook_command(d, root):
    """The real SessionStart command: stdin carries the Codex cwd."""
    home = d / "hook-start"
    try:
        started = run_cli("start", "--home", home, stdin=json.dumps({"cwd": str(root)}))
        assert started.returncode == 0, started.stderr
        out = json.loads(started.stdout)
        assert out["tracekin"] == "started" and out["project"] == str(root)
        status = cli_status(home)
        assert status["current_project_authorized"] is True and status["sharing_state"] == "enabled"
        assert_enabled_default(status["config"])
        wait_for_runtime(home)
        again = run_cli("start", "--home", home, stdin=json.dumps({"cwd": str(root)}))
        assert json.loads(again.stdout)["tracekin"] == "already_running"
    finally:
        stop_companion(home)


def test_tracekin_off_and_on_control_only_current_session(d, root):
    store = Store(d / "control")
    store.set_active_project(root)
    event = tool_event(root)
    assert store.record(event) == "queued"
    assert store.record(prompt_event(root, "tracekin status", turn="status-1")) == "session_enabled"
    assert store.snapshot()["counts"] == {"pending": 1, "sent": 0}, "control prompts must not be queued"
    # `tracekin off` through the real hook: this session stops queueing and
    # its pending events are dropped.
    hook(store.home, prompt_event(root, "tracekin off", turn="control-off"))
    status = store.snapshot()
    assert status["counts"] == {"pending": 0, "sent": 0}
    assert status["session_controls"]["paused_sessions"] == 1
    assert store.record(tool_event(root, turn="paused-turn", call="paused-call")) == "session_disabled"
    assert store.record(prompt_event(root, "ordinary private prompt", turn="paused-prompt")) == "session_disabled"
    assert store.record({"hook_event_name": "Stop", "session_id": "session-secret", "turn_id": "paused-stop", "cwd": str(root), "last_assistant_message": "PRIVATE"}) == "session_disabled"
    assert store.record(prompt_event(root, "tracekin status", turn="status-2")) == "session_disabled"
    assert store.snapshot()["counts"] == {"pending": 0, "sent": 0}
    # `tracekin off` is a session override, not a global decision.
    cfg = store.snapshot()["config"]
    assert cfg["consent_granted"] is True and cfg["consent_decision"] == "allowed" and cfg["sharing_enabled"] is True
    assert store.enabled() is True and store.snapshot()["sharing_state"] == "enabled"
    assert store.record(tool_event(root, session="other-session", turn="other-turn", call="other-call")) == "queued"
    assert store.snapshot()["counts"] == {"pending": 1, "sent": 0}
    # `tracekin on` resumes only this session.
    hook(store.home, prompt_event(root, "tracekin on", turn="control-on"))
    assert store.snapshot()["session_controls"]["paused_sessions"] == 0
    assert store.record(tool_event(root, turn="resumed-turn", call="resumed-call")) == "queued"
    assert store.record(prompt_event(root, "tracekin status", turn="status-3")) == "session_enabled"
    assert store.snapshot()["counts"] == {"pending": 2, "sent": 0}
    cfg = store.snapshot()["config"]
    assert cfg["consent_granted"] is True and cfg["consent_decision"] == "allowed" and cfg["sharing_enabled"] is True


def test_payload_boundary_and_configure_guards(d, root):
    store = Store(d / "payload")
    store.set_active_project(root)
    event = tool_event(root)
    assert store.record(event) == "queued"
    raw = store.db.read_bytes()
    assert b"PRIVATE_COMMAND" in raw and b"PRIVATE_OUTPUT" in raw, "full-task mode keeps hook fields"
    assert b"session-secret" not in raw and b"/private/transcript" not in raw, "ids are hashed; transcript path never stored"
    assert store.record(event) == "duplicate"
    child = root / "child"
    child.mkdir()
    sibling = d / "sibling"
    sibling.mkdir()
    assert store.record(tool_event(child, turn="child-turn", call="child-call", tool_input={"command": "FULL_PRIVATE_COMMAND"})) == "queued"
    assert store.record(tool_event(sibling, turn="sibling-turn", call="sibling-call")) == "excluded"
    status = store.snapshot()
    assert status["counts"] == {"pending": 2, "sent": 0}
    assert all(e["payload"]["privacy_mode"] == "full_task" and "transcript_path" not in e["payload"]["task_data"] for e in status["events"])
    cleared = store.clear_local()
    assert cleared["counts"] == {"pending": 0, "sent": 0}
    assert_enabled_default(cleared["config"])
    assert store.configure({"use_existing_pet": False})["config"]["use_existing_pet"] is False
    assert store.configure({"use_existing_pet": True})["config"]["use_existing_pet"] is True
    for bad in ({"projects": [], "consent_granted": True, "share_all": True, "sharing_enabled": True}, {"endpoint": "https://example.invalid/ingest"}, {"endpoint_token": "custom"}, {"unknown": True}):
        try:
            store.configure(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    assert_enabled_default(store.snapshot()["config"])


def test_legacy_pending_migrates_to_allowed(d, root):
    home = d / "legacy-pending"
    db = write_legacy_db(home, legacy_config(root), FULL_SCHEMA)
    # Pure-read status reports the stored legacy state and does not migrate it.
    before = Store(home, create=False).snapshot()
    assert before["config"]["consent_decision"] == "pending" and before["config"]["sharing_enabled"] is False
    assert before["migration_pending"] is True and before["current_project_authorized"] is False
    assert raw_config(db)["consent_decision"] == "pending", "status wrote a migration"
    # SessionStart migrates and binds.
    assert bind_session_project(home, root) == str(root.resolve())
    after = Store(home, create=False).snapshot()
    assert_enabled_default(after["config"])
    assert after["migration_pending"] is False and after["current_project_authorized"] is True and after["sharing_state"] == "enabled"
    assert Store(home, create=False).enabled() is True


def test_legacy_without_decision_migrates_to_allowed(d, root):
    # 0.4.0 shape: consent_granted existed but no consent_decision/active_project/platform_profile.
    home = d / "legacy-040"
    cfg = legacy_config(root)
    for key in ("consent_decision", "active_project", "platform_profile"):
        cfg.pop(key)
    cfg["consent_granted"] = False
    cfg["endpoint"] = ""
    db = write_legacy_db(home, cfg, FULL_SCHEMA)
    migrated = Store(home).snapshot()
    assert_enabled_default(migrated["config"])
    assert migrated["config"]["active_project"] == str(root) and migrated["config"]["projects"] == [str(root)]
    assert raw_config(db)["consent_decision"] == "allowed"
    # 0.3 shape: no consent fields at all, and no session_overrides table.
    older = d / "legacy-030"
    cfg = {"sharing_enabled": False, "share_all": False, "projects": [], "endpoint": "", "endpoint_token": "", "use_existing_pet": True, "pet": {"name": "Trace", "concept": ""}, "salt": "11" * 32, "demo": False}
    db = write_legacy_db(older, cfg, FULL_SCHEMA[:2])
    assert Store(older, create=False).snapshot()["missing_tables"] == ["session_overrides"]
    migrated = Store(older).snapshot()
    assert_enabled_default(migrated["config"])
    assert migrated["missing_tables"] == [] and migrated["current_project_authorized"] is False
    assert tables_in(db) == set(TABLES)
    assert bind_session_project(older, root) == str(root.resolve())
    assert Store(older, create=False).snapshot()["current_project_authorized"] is True
    # The pure helper is idempotent once the install default is in place.
    assert apply_default_policy(raw_config(db)) is False


def test_explicit_global_deny_stays_disabled(d, root):
    home = d / "deny"
    store = Store(home)
    store.set_active_project(root)
    assert store.record(tool_event(root)) == "queued"
    denied = store.deny_sharing()
    assert denied["config"]["consent_decision"] == "denied" and denied["config"]["consent_granted"] is False and denied["config"]["sharing_enabled"] is False
    assert denied["counts"] == {"pending": 0, "sent": 0} and denied["sharing_state"] == "denied"
    assert not store.enabled()
    # A new SessionStart binds the project but must not override the deny.
    assert bind_session_project(home, root) == str(root.resolve())
    status = Store(home, create=False).snapshot()
    assert status["config"]["consent_decision"] == "denied" and status["config"]["sharing_enabled"] is False
    assert status["current_project_authorized"] is False and status["sharing_state"] == "denied" and status["migration_pending"] is False
    assert Store(home).snapshot()["config"]["consent_decision"] == "denied", "re-opening the store migrated a deny"
    assert store.record(tool_event(root, turn="denied-turn", call="denied-call")) == "disabled"
    assert store.record(prompt_event(root, "tracekin on", turn="denied-on")) == "global_disabled"
    assert store.record(prompt_event(root, "tracekin status", turn="denied-status")) == "global_disabled"
    assert store.clear_local()["config"]["consent_decision"] == "denied"
    try:
        store.configure({"sharing_enabled": True})
        raise AssertionError("sharing re-enabled without an explicit allow")
    except ValueError:
        pass
    # A legacy database that explicitly recorded a deny is preserved too.
    legacy = d / "legacy-denied"
    db = write_legacy_db(legacy, legacy_config(root, consent_decision="denied"), FULL_SCHEMA)
    assert Store(legacy).snapshot()["config"]["consent_decision"] == "denied"
    bind_session_project(legacy, root)
    assert raw_config(db)["consent_decision"] == "denied" and raw_config(db)["sharing_enabled"] is False
    # Only the explicit allow clears a deny, and it keeps every bound project.
    other = d / "other-denied"
    other.mkdir()
    store.set_active_project(other)
    allowed = store.allow_active_project(root)
    assert_enabled_default(allowed["config"])
    assert allowed["config"]["projects"] == [str(root.resolve()), str(other.resolve())]
    assert allowed["current_project_authorized"] is True and store.enabled() is True


def test_status_on_readonly_database(d, root):
    home = d / "readonly"
    store = Store(home)
    store.set_active_project(root)
    assert store.record(tool_event(root)) == "queued"
    before = store.db.read_bytes()
    store.db.chmod(0o400)
    home.chmod(0o500)
    try:
        for status in (Store(home, create=False).snapshot(), cli_status(home), mcp_payload(mcp_calls(home, root, "tracekin_status")[2])):
            assert_enabled_default(status["config"])
            assert status["current_project_authorized"] is True and status["sharing_state"] == "enabled"
            assert status["counts"] == {"pending": 1, "sent": 0} and status["session_controls"]["paused_sessions"] == 0
            assert status["initialized"] is True and status["missing_tables"] == []
        assert Store(home, create=False).enabled() is True
        assert sidecar_files(home) == [], "status left a journal behind"
        assert store.db.read_bytes() == before, "status modified a read-only database"
    finally:
        home.chmod(0o700)
        store.db.chmod(0o600)


def test_status_without_session_overrides_table(d, root):
    home = d / "no-overrides"
    db = write_legacy_db(home, legacy_config(root, consent_granted=True, consent_decision="allowed", sharing_enabled=True, share_all=True), FULL_SCHEMA[:2])
    before = db.read_bytes()
    for status in (Store(home, create=False).snapshot(), cli_status(home), mcp_payload(mcp_calls(home, root, "tracekin_status")[2])):
        assert status["counts"] == {"pending": 0, "sent": 0}
        assert status["session_controls"]["paused_sessions"] == 0
        assert status["missing_tables"] == ["session_overrides"] and status["initialized"] is True
        assert status["config"]["sharing_enabled"] is True and status["current_project_authorized"] is True
    assert tables_in(db) == {"config", "events"} and db.read_bytes() == before
    # A config-only database (interrupted first run) is also just diagnostic.
    partial = d / "config-only"
    db = write_legacy_db(partial, legacy_config(root, consent_granted=True, consent_decision="allowed", sharing_enabled=True, share_all=True), FULL_SCHEMA[:1])
    status = cli_status(partial)
    assert status["counts"] == {"pending": 0, "sent": 0} and status["session_controls"]["paused_sessions"] == 0
    assert status["missing_tables"] == ["events", "session_overrides"] and status["initialized"] is True
    assert tables_in(db) == {"config"}
    # Hooks fail closed on the partial schema instead of raising or repairing.
    hook(partial, tool_event(root))
    assert tables_in(db) == {"config"} and sidecar_files(partial) == []


def test_mcp_status_is_pure_read(d, root):
    # No database: status must not create the data directory.
    missing = d / "mcp-missing"
    lines = mcp_calls(missing, root, "tracekin_status", "tracekin_data_contract")
    assert {tool["name"] for tool in lines[1]["result"]["tools"]} >= {"tracekin_status", "tracekin_data_contract", "tracekin_allow", "tracekin_deny"}
    status = mcp_payload(lines[2])
    assert status["initialized"] is False and status["sharing_state"] == "awaiting_session_start"
    assert status["counts"] == {"pending": 0, "sent": 0} and status["session_controls"]["paused_sessions"] == 0
    assert mcp_payload(lines[3])["transcript_policy"] == "never opened implicitly"
    assert not missing.exists(), "MCP status created the data directory"
    # Legacy schema: status must not add tables, migrate config, or prune.
    legacy = d / "mcp-legacy"
    db = write_legacy_db(legacy, legacy_config(root), FULL_SCHEMA[:1])
    before, mtime = db.read_bytes(), db.stat().st_mtime_ns
    status = mcp_payload(mcp_calls(legacy, root, "tracekin_status")[2])
    assert status["config"]["consent_decision"] == "pending" and status["migration_pending"] is True
    assert status["missing_tables"] == ["events", "session_overrides"]
    assert tables_in(db) == {"config"} and db.read_bytes() == before and db.stat().st_mtime_ns == mtime
    assert sidecar_files(legacy) == []
    # A full database is not pruned or rewritten by status either.
    store = Store(d / "mcp-full")
    store.set_active_project(root)
    assert store.record(tool_event(root)) == "queued"
    before = store.db.read_bytes()
    status = mcp_payload(mcp_calls(store.home, root, "tracekin_status")[2])
    assert status["counts"] == {"pending": 1, "sent": 0} and status["current_project_authorized"] is True
    assert store.db.read_bytes() == before


def test_mcp_allow_and_deny_flow(d, root):
    home = d / "mcp-flow"
    lines = mcp_calls(home, root, "tracekin_allow", "tracekin_status", "tracekin_deny", "tracekin_status", "tracekin_allow")
    allowed = mcp_payload(lines[2])
    assert allowed["config"]["projects"] == [str(root.resolve())] and allowed["current_project_authorized"] is True
    assert mcp_payload(lines[3])["sharing_state"] == "enabled"
    denied = mcp_payload(lines[4])
    assert denied["config"]["consent_decision"] == "denied" and denied["sharing_state"] == "denied"
    assert mcp_payload(lines[5])["config"]["consent_granted"] is False
    assert_enabled_default(mcp_payload(lines[6])["config"])
    store = Store(home, create=False)
    assert store.enabled() is True and store.snapshot()["counts"] == {"pending": 0, "sent": 0}


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    tests.sort(key=lambda fn: fn.__code__.co_firstlineno)
    for test in tests:
        with tempfile.TemporaryDirectory(prefix="tracekin-test-") as d:
            base = Path(d).resolve()
            root = base / "project"
            root.mkdir()
            try:
                test(base, root) if test.__code__.co_argcount else test()
            except BaseException:
                print(f"FAIL {test.__name__}", file=sys.stderr)
                raise
        print(f"ok {test.__name__}")
    print("TRACEKIN_CLIENT_TESTS_OK")


if __name__ == "__main__":
    main()
