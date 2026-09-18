#!/usr/bin/env python3
"""Deterministic unit checks for the install-default sharing policy and consent boundary.

Runs with the standard library only: ``python3 test_tracekin.py``.
"""
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error

sys.path.insert(0, str(Path(__file__).parent))
from tracekin import PLATFORM_ENDPOINT, PLATFORM_PROFILE, PLUGIN_VERSION, Store, TABLES, apply_default_policy, bind_session_project, detect_harness, home_dir, normalize_hook_event, session_command, tool_category

EXPECTED_VERSION = "0.8.2+build.20260918053206"
SCRIPTS = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPTS.parent
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


def mcp_calls(home, cwd, *tool_names, env=None):
    """Drive mcp_server.py over stdio and return one parsed line per request."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    for index, name in enumerate(tool_names, start=3):
        requests.append({"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {"name": name, "arguments": {}}})
    env = dict(env) if env is not None else dict(os.environ, TRACEKIN_HOME=str(home))
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


def wait_for_runtime(home, timeout=20.0):
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
    log = Path(home) / "companion.log"
    detail = log.read_text(errors="replace")[-2000:] if log.exists() else "(no companion.log)"
    raise AssertionError("companion did not publish runtime.json\n--- companion.log ---\n" + detail)


def settle_companion(home):
    """Wait for the companion a SessionStart just spawned, then stop it so the
    rest of a test never races a delivery attempt against the real receiver."""
    wait_for_runtime(home)
    stop_companion(home)


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

def test_version_synced_with_manifests():
    assert PLUGIN_VERSION == EXPECTED_VERSION
    codex = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text())
    claude = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((PLUGIN_DIR.parent.parent / ".claude-plugin" / "marketplace.json").read_text())
    assert codex["version"] == claude["version"] == PLUGIN_VERSION
    assert [p["version"] for p in marketplace["plugins"] if p["name"] == "tracekin"] == [PLUGIN_VERSION]
    # Both harnesses share hooks.json; each gets an MCP config in its own variable dialect.
    hooks = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text())
    commands = [h["command"] for group in hooks["hooks"].values() for entry in group for h in entry["hooks"]]
    assert commands and all("${CLAUDE_PLUGIN_ROOT}" in c for c in commands)
    assert "${PLUGIN_ROOT}" in json.loads((PLUGIN_DIR / codex["mcpServers"]).read_text())["mcpServers"]["tracekin"]["args"][0]
    assert "${CLAUDE_PLUGIN_ROOT}" in json.loads((PLUGIN_DIR / claude["mcpServers"]).read_text())["mcpServers"]["tracekin"]["args"][0]
    # Cursor plugin manifest: Cursor event names, plugin-relative wrappers, ${CURSOR_PLUGIN_ROOT} for MCP.
    cursor = json.loads((PLUGIN_DIR / ".cursor-plugin" / "plugin.json").read_text())
    assert cursor["version"] == PLUGIN_VERSION
    cursor_hooks = json.loads((PLUGIN_DIR / cursor["hooks"]).read_text())
    assert cursor_hooks["version"] == 1 and set(cursor_hooks["hooks"]) == {"sessionStart", "beforeSubmitPrompt", "postToolUse", "stop"}
    for event, entries in cursor_hooks["hooks"].items():
        for entry in entries:
            wrapper = PLUGIN_DIR / entry["command"]
            assert wrapper.is_file() and os.access(wrapper, os.X_OK), entry
            assert wrapper.name == ("start.sh" if event == "sessionStart" else "hook.sh")
    assert "${CURSOR_PLUGIN_ROOT}" in json.loads((PLUGIN_DIR / cursor["mcpServers"]).read_text())["mcpServers"]["tracekin"]["args"][0]
    # Gemini CLI extension: manifest at the repo root, hooks/hooks.json with ${extensionPath} wrappers, ms timeouts.
    repo = PLUGIN_DIR.parent.parent
    gemini = json.loads((repo / "gemini-extension.json").read_text())
    assert gemini["name"] == "tracekin" and gemini["version"] == PLUGIN_VERSION
    assert "${extensionPath}" in gemini["mcpServers"]["tracekin"]["args"][0] and gemini["mcpServers"]["tracekin"]["args"][0].endswith("mcp_server.py")
    gemini_hooks = json.loads((repo / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(gemini_hooks) == {"SessionStart", "BeforeAgent", "AfterTool", "AfterAgent"}
    for event, groups in gemini_hooks.items():
        for hook_entry in groups[0]["hooks"]:
            assert hook_entry["command"].startswith("${extensionPath}/plugins/tracekin/gemini/") and hook_entry["timeout"] >= 1000, hook_entry
            wrapper = repo / hook_entry["command"].replace("${extensionPath}/", "")
            assert wrapper.is_file() and os.access(wrapper, os.X_OK), wrapper
            assert wrapper.name == ("start.sh" if event == "SessionStart" else "hook.sh")


def test_cursor_plugin_wrappers_run_from_any_directory(d, root):
    """The .cursor-plugin hooks are plugin-relative wrappers; they must locate the
    real script from their own path, whatever cwd Cursor gives them."""
    home = d / "cursor-plugin"
    elsewhere = d / "elsewhere"
    elsewhere.mkdir()
    env = dict(base_env(), TRACEKIN_HOME=str(home), CURSOR_PROJECT_DIR=str(root))
    try:
        started = subprocess.run([str(PLUGIN_DIR / "cursor" / "start.sh")], input=json.dumps(cursor_event(root, "sessionStart")), text=True, capture_output=True, env=env, cwd=elsewhere, timeout=30)
        assert started.returncode == 0 and json.loads(started.stdout) == {}, started.stderr
        settle_companion(home)
        assert Store(home, create=False).snapshot()["current_project_authorized"] is True
        hooked = subprocess.run([str(PLUGIN_DIR / "cursor" / "hook.sh")], input=json.dumps(cursor_event(root, "beforeSubmitPrompt")), text=True, capture_output=True, env=env, cwd=elsewhere, timeout=30)
        assert hooked.returncode == 0 and json.loads(hooked.stdout) == {"continue": True}, hooked.stderr
        assert Store(home, create=False).snapshot()["counts"] == {"pending": 1, "sent": 0}
    finally:
        stop_companion(home)


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


ENV_KEYS = ("TRACEKIN_HOME", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "CODEX_HOME", "TRACEKIN_PROJECT", "HOME")


def base_env():
    return {k: v for k, v in os.environ.items() if k not in ENV_KEYS}


class temp_env:
    """Temporarily replace selected process environment variables."""

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in self.values.items():
            os.environ[key] = value

    def __exit__(self, *_):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_hooks_and_mcp_share_one_data_dir(d, root):
    """Harnesses inject PLUGIN_DATA / CLAUDE_PLUGIN_DATA into hook commands only,
    never into the .mcp.json server. Every surface must resolve ~/.tracekin."""
    fake_home = d / "home"
    fake_home.mkdir()
    plugin_data = d / "plugins" / "data" / "tracekin-tracekin-remote-test"
    hook_env = dict(base_env(), HOME=str(fake_home), CODEX_HOME=str(d / "codex-home"), PLUGIN_DATA=str(plugin_data), CLAUDE_PLUGIN_DATA=str(plugin_data))
    mcp_env = dict(base_env(), HOME=str(fake_home))
    expected = str((fake_home / ".tracekin").resolve())
    with temp_env(HOME=str(fake_home), PLUGIN_DATA=str(plugin_data), CODEX_HOME=str(d / "codex-home")):
        assert str(home_dir()) == expected, "PLUGIN_DATA / CODEX_HOME must not select the data directory"
    with temp_env(HOME=str(fake_home), TRACEKIN_HOME=str(d / "override")):
        assert home_dir() == (d / "override").resolve()
    # A companion left behind by a <= 0.4.3 hook in the PLUGIN_DATA directory.
    legacy_dir = plugin_data / "tracekin"
    legacy_dir.mkdir(parents=True)
    stale = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (legacy_dir / "runtime.json").write_text(json.dumps({"pid": stale.pid, "url": "http://127.0.0.1:1/#x", "mode": "production", "version": "0.4.3+codex.0"}))
    try:
        # SessionStart runs with the hook environment (PLUGIN_DATA set, no --home).
        started = run_cli("start", stdin=json.dumps({"cwd": str(root)}), env=hook_env, cwd=root)
        assert started.returncode == 0, started.stderr
        out = json.loads(started.stdout)
        assert out["tracekin"] == "started" and out["data_dir"] == expected, out
        assert (fake_home / ".tracekin" / "tracekin.sqlite3").exists() and not (legacy_dir / "tracekin.sqlite3").exists()
        assert stale.wait(timeout=5) != 0 and not (legacy_dir / "runtime.json").exists(), "legacy companion was not stopped"
        settle_companion(fake_home / ".tracekin")
        # The MCP server runs without PLUGIN_DATA and must read the same database.
        status = mcp_payload(mcp_calls(None, root, "tracekin_status", env=mcp_env)[2])
        assert status["data_dir"] == expected and status["sharing_state"] == "enabled" and status["current_project_authorized"] is True
        hooked = run_cli("hook", stdin=json.dumps(tool_event(root)), env=hook_env, cwd=root)
        assert hooked.returncode == 0 and hooked.stdout.strip() == "{}"
        assert mcp_payload(mcp_calls(None, root, "tracekin_status", env=mcp_env)[2])["counts"] == {"pending": 1, "sent": 0}
        assert cli_status(fake_home / ".tracekin")["counts"] == {"pending": 1, "sent": 0}
    finally:
        stop_companion(fake_home / ".tracekin")
        if stale.poll() is None:
            stale.kill()
            stale.wait()


def test_companion_starts_when_session_cwd_is_not_a_project(d, root):
    """A session launched from the home directory must not bind it, but the
    companion still has to come up and serve the scope already recorded."""
    fake_home = d / "home"
    fake_home.mkdir()
    home = fake_home / ".tracekin"
    Store(home).set_active_project(root)
    env = dict(base_env(), HOME=str(fake_home))
    try:
        started = json.loads(run_cli("start", stdin=json.dumps({"cwd": str(fake_home)}), env=env, cwd=fake_home).stdout)
        assert started["tracekin"] == "started" and started["project"] == str(fake_home)
        wait_for_runtime(home)
        status = cli_status(home)
        assert status["config"]["active_project"] == str(root.resolve()) and status["config"]["projects"] == [str(root.resolve())]
        assert status["current_project_authorized"] is True and status["sharing_state"] == "enabled"
        assert str(fake_home) not in status["config"]["projects"], "the home directory must never be bound"
    finally:
        stop_companion(home)


def test_legacy_codex_home_is_adopted_once(d, root):
    """A 0.4.x database under ~/.codex/tracekin is adopted by the first write path
    into ~/.tracekin; read paths only report it."""
    fake_home = d / "home"
    legacy = fake_home / ".codex" / "tracekin"
    legacy_store = Store(legacy)
    legacy_store.set_active_project(root)
    assert legacy_store.record(tool_event(root)) == "queued"
    stale = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (legacy / "runtime.json").write_text(json.dumps({"pid": stale.pid, "url": "http://127.0.0.1:1/#x", "mode": "production", "version": "0.4.6+codex.0"}))
    env = dict(base_env(), HOME=str(fake_home))
    new_home = fake_home / ".tracekin"
    # A companion already serving the shared directory (so no delivery is attempted
    # during the test): SessionStart must adopt the legacy database and keep it.
    new_home.mkdir(mode=0o700)
    current = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (new_home / "runtime.json").write_text(json.dumps({"pid": current.pid, "url": "http://127.0.0.1:1/#y", "mode": "production", "version": PLUGIN_VERSION}))
    try:
        # Pure-read status before any SessionStart: nothing is created or moved.
        before = json.loads(run_cli("status", env=env).stdout)
        assert before["initialized"] is False and before["legacy_data_dir"] == str(legacy) and before["data_dir"] == str(new_home)
        assert not (new_home / "tracekin.sqlite3").exists() and (legacy / "tracekin.sqlite3").exists()
        started = json.loads(run_cli("start", stdin=json.dumps({"cwd": str(root)}), env=env, cwd=root).stdout)
        assert started["tracekin"] == "already_running" and started["data_dir"] == str(new_home), started
        assert current.poll() is None, "the companion already serving the shared directory must be kept"
        after = json.loads(run_cli("status", env=env).stdout)
        assert after["initialized"] is True and after["legacy_data_dir"] is None
        assert after["counts"] == {"pending": 1, "sent": 0}, "queued events must survive the move"
        assert after["current_project_authorized"] is True and after["config"]["projects"] == [str(root)]
        assert (legacy / "tracekin.sqlite3.migrated").exists() and not (legacy / "tracekin.sqlite3").exists()
        assert stale.wait(timeout=5) != 0 and not (legacy / "runtime.json").exists(), "legacy companion was not stopped"
        # A second SessionStart finds nothing left to adopt.
        again = json.loads(run_cli("start", stdin=json.dumps({"cwd": str(root)}), env=env, cwd=root).stdout)
        assert again["tracekin"] == "already_running"
        assert json.loads(run_cli("status", env=env).stdout)["counts"] == {"pending": 1, "sent": 0}
    finally:
        for proc in (stale, current):
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    # An explicit TRACEKIN_HOME never triggers adoption.
    isolated = d / "isolated"
    assert Store(isolated).snapshot()["counts"] == {"pending": 0, "sent": 0}


def claude_event(root, kind, **fields):
    event = {"session_id": "claude-session", "prompt_id": "prompt-1", "transcript_path": "/private/claude/transcript.jsonl", "cwd": str(root), "permission_mode": "default", "hook_event_name": kind}
    if kind == "UserPromptSubmit":
        event["prompt"] = "PRIVATE_PROMPT"
    elif kind == "PostToolUse":
        event.update(tool_name="Bash", tool_input={"command": "PRIVATE_COMMAND"}, tool_use_id="toolu_01", tool_output="PRIVATE_OUTPUT", tool_output_is_error=False)
    elif kind == "Stop":
        event["stop_hook_active"] = False
    event.update(fields)
    return event


def test_claude_code_hook_payloads_are_normalized(d, root):
    assert detect_harness({"prompt_id": "p"}) == "claude-code" and detect_harness({"turn_id": "t"}) == "codex" and detect_harness({}) == "codex"
    harness, normalized = normalize_hook_event(claude_event(root, "PostToolUse"))
    assert harness == "claude-code" and normalized["turn_id"] == "prompt-1" and normalized["tool_response"] == "PRIVATE_OUTPUT"
    assert "turn_id" not in normalize_hook_event(claude_event(root, "Stop", prompt_id=None))[1], "without prompt_id the store assigns the turn"
    assert normalize_hook_event({"turn_id": "codex-turn", "hook_event_name": "Stop"})[0] == "codex"
    claude_env = dict(base_env(), TRACEKIN_HOME=str(d / "claude"), CLAUDE_PLUGIN_ROOT=str(PLUGIN_DIR), CLAUDE_PLUGIN_DATA=str(d / "claude-data"), CLAUDE_SESSION_ID="claude-session", CLAUDE_PROJECT_DIR=str(root))
    home = d / "claude"
    try:
        # SessionStart carries the Claude Code shape and binds the project.
        started = run_cli("start", stdin=json.dumps({"session_id": "claude-session", "cwd": str(root), "hook_event_name": "SessionStart", "source": "startup", "transcript_path": "/private/claude/transcript.jsonl"}), env=claude_env, cwd=root)
        assert json.loads(started.stdout)["tracekin"] == "started", started.stdout
        settle_companion(home)
        store = Store(home, create=False)
        assert store.snapshot()["current_project_authorized"] is True
        for kind in ("UserPromptSubmit", "PostToolUse", "Stop"):
            hooked = run_cli("hook", stdin=json.dumps(claude_event(root, kind)), env=claude_env, cwd=root)
            assert hooked.returncode == 0 and hooked.stdout.strip() == "{}", hooked.stderr
        events = {e["payload"]["event"]: e["payload"] for e in store.snapshot()["events"]}
        assert set(events) == {"UserPromptSubmit", "PostToolUse", "Stop"} and store.snapshot()["counts"] == {"pending": 3, "sent": 0}
        assert all(p["source"] == "claude_code_hook" and p["privacy_mode"] == "full_task" for p in events.values())
        assert events["UserPromptSubmit"]["task_data"] == {"prompt": "PRIVATE_PROMPT"}
        assert events["PostToolUse"]["task_data"] == {"tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT"} and events["PostToolUse"]["tool_category"] == "shell"
        assert events["Stop"]["task_data"] == {}, "Claude Code Stop carries no message and the transcript is never read"
        assert len({p["turn_id"] for p in events.values()}) == 1, "prompt_id is the shared turn identifier"
        raw = store.db.read_bytes()
        assert b"claude-session" not in raw and b"/private/claude/transcript.jsonl" not in raw and b"prompt-1" not in raw
        # Same prompt_id + tool_use_id is a duplicate; a new prompt is a new turn.
        assert store.record(claude_event(root, "PostToolUse")) == "duplicate"
        assert store.record(claude_event(root, "UserPromptSubmit", prompt_id="prompt-2", prompt="second")) == "queued"
        assert store.record(claude_event(root, "PostToolUse", prompt_id="prompt-2", tool_name="Read", tool_use_id="toolu_02", tool_input={"file_path": "/x"}, tool_output="contents")) == "queued"
        assert store.record(claude_event(root, "PostToolUse", prompt_id="prompt-2", tool_name="WebFetch", tool_use_id="toolu_03", tool_output="page")) == "queued"
        categories = {e["payload"].get("tool_category") for e in store.snapshot()["events"] if e["payload"]["event"] == "PostToolUse"}
        assert categories == {"shell", "read", "web"}
        # Session controls work from a Claude Code prompt, backticks included.
        hooked = run_cli("hook", stdin=json.dumps(claude_event(root, "UserPromptSubmit", prompt_id="prompt-3", prompt="`tracekin off`")), env=claude_env, cwd=root)
        assert hooked.stdout.strip() == "{}"
        assert store.snapshot()["session_controls"]["paused_sessions"] == 1 and store.snapshot()["counts"] == {"pending": 0, "sent": 0}
        assert store.record(claude_event(root, "PostToolUse", prompt_id="prompt-3", tool_use_id="toolu_04")) == "session_disabled"
        assert store.record(claude_event(root, "UserPromptSubmit", prompt_id="prompt-4", prompt="tracekin on")) == "session_enabled"
        # Forcing the Codex dialect on a Claude Code payload fails closed (no turn_id).
        assert store.record(claude_event(root, "PostToolUse", prompt_id="prompt-5", tool_use_id="toolu_05"), harness="codex") == "invalid"
        forced = run_cli("hook", "--harness", "codex", stdin=json.dumps(claude_event(root, "PostToolUse", prompt_id="prompt-6", tool_use_id="toolu_06")), env=claude_env, cwd=root)
        assert forced.returncode == 0 and forced.stdout.strip() == "{}" and store.snapshot()["counts"] == {"pending": 0, "sent": 0}
    finally:
        stop_companion(home)


def cursor_event(root, kind, **fields):
    event = {"conversation_id": "conv-secret", "generation_id": "gen-1", "model": "model-x", "hook_event_name": kind, "cursor_version": "2.0.0", "workspace_roots": [str(root)], "user_email": "PRIVATE_EMAIL@example.com", "transcript_path": "/private/cursor/transcript.jsonl"}
    if kind == "beforeSubmitPrompt":
        event.update(prompt="PRIVATE_PROMPT", attachments=[])
    elif kind == "postToolUse":
        event.update(tool_name="Shell", tool_input={"command": "PRIVATE_COMMAND"}, tool_output="PRIVATE_OUTPUT", tool_use_id="call-1", cwd=str(root), duration=12)
    elif kind == "stop":
        event.update(status="completed", loop_count=1)
    elif kind == "sessionStart":
        event.update(session_id="cursor-session-1", is_background_agent=False, composer_mode="agent")
    event.update(fields)
    return event


def test_cursor_hook_payloads_are_normalized(d, root):
    assert detect_harness(cursor_event(root, "stop")) == "cursor" and detect_harness({"conversation_id": "c"}) == "cursor"
    harness, normalized = normalize_hook_event(cursor_event(root, "postToolUse"))
    assert harness == "cursor" and normalized["hook_event_name"] == "PostToolUse" and normalized["session_id"] == "conv-secret"
    assert normalized["turn_id"] == "gen-1" and normalized["tool_response"] == "PRIVATE_OUTPUT" and normalized["cwd"] == str(root)
    assert normalize_hook_event(cursor_event(root, "stop"))[1]["cwd"] == str(root), "workspace_roots supplies the project when cwd is absent"
    assert tool_category("Shell") == "shell" and tool_category("run_terminal_cmd") == "shell" and tool_category("edit_file") == "edit" and tool_category("codebase_search") == "read" and tool_category("mcp__x__y") == "other" and tool_category("SomethingNew") == "other" and tool_category("WebSearchTool") == "web"
    home = d / "cursor"
    cursor_home = d / "dot-cursor"
    cursor_home.mkdir()
    env = dict(base_env(), TRACEKIN_HOME=str(home), CURSOR_PROJECT_DIR=str(root), CLAUDE_PROJECT_DIR=str(root))
    try:
        # User-level Cursor hooks run from ~/.cursor, not from the project.
        started = run_cli("start", "--harness", "cursor", stdin=json.dumps(cursor_event(root, "sessionStart")), env=env, cwd=cursor_home)
        assert started.returncode == 0 and json.loads(started.stdout) == {}, "Cursor sessionStart output must stay schema-clean"
        settle_companion(home)
        store = Store(home, create=False)
        assert store.snapshot()["current_project_authorized"] is True and store.snapshot()["config"]["projects"] == [str(root.resolve())]
        prompt = run_cli("hook", "--harness", "cursor", stdin=json.dumps(cursor_event(root, "beforeSubmitPrompt")), env=env, cwd=cursor_home)
        assert prompt.returncode == 0 and json.loads(prompt.stdout) == {"continue": True}, prompt.stdout
        for kind in ("postToolUse", "stop"):
            hooked = run_cli("hook", stdin=json.dumps(cursor_event(root, kind)), env=env, cwd=cursor_home)  # auto-detected
            assert hooked.returncode == 0 and json.loads(hooked.stdout) == {}, hooked.stdout
        events = {e["payload"]["event"]: e["payload"] for e in store.snapshot()["events"]}
        assert set(events) == {"UserPromptSubmit", "PostToolUse", "Stop"} and store.snapshot()["counts"] == {"pending": 3, "sent": 0}
        assert all(p["source"] == "cursor_hook" for p in events.values()) and len({p["turn_id"] for p in events.values()}) == 1
        assert events["PostToolUse"]["tool_category"] == "shell" and events["PostToolUse"]["task_data"] == {"tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT"}
        assert events["Stop"]["task_data"] == {} and events["UserPromptSubmit"]["task_data"] == {"prompt": "PRIVATE_PROMPT"}
        raw = store.db.read_bytes()
        assert b"conv-secret" not in raw and b"PRIVATE_EMAIL" not in raw and b"/private/cursor" not in raw and b"gen-1" not in raw
        assert store.record(cursor_event(root, "postToolUse")) == "duplicate"
        assert store.record(cursor_event(root, "postToolUse", generation_id="gen-2", tool_use_id="call-2")) == "queued"
        # Session controls: the control prompt is withheld but must not block Cursor.
        off = run_cli("hook", "--harness", "cursor", stdin=json.dumps(cursor_event(root, "beforeSubmitPrompt", generation_id="gen-3", prompt="`tracekin off`")), env=env, cwd=cursor_home)
        assert json.loads(off.stdout) == {"continue": True} and store.snapshot()["session_controls"]["paused_sessions"] == 1
        assert store.record(cursor_event(root, "postToolUse", generation_id="gen-3", tool_use_id="call-3")) == "session_disabled"
        assert store.record(cursor_event(root, "beforeSubmitPrompt", generation_id="gen-4", prompt="tracekin on")) == "session_enabled"
    finally:
        stop_companion(home)


def test_cursor_installer_merges_and_uninstalls_cleanly(d, root):
    cursor_home = d / "dot-cursor"
    cursor_home.mkdir()
    (cursor_home / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"beforeSubmitPrompt": [{"command": "./other.sh"}], "afterFileEdit": [{"command": "./fmt.sh"}]}}))
    (cursor_home / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"type": "stdio", "command": "other"}}}))
    for _ in range(2):  # idempotent
        installed = json.loads(run_cli("install-cursor", "--cursor-home", cursor_home).stdout)
        assert installed["tracekin"] == "installed"
    hooks = json.loads((cursor_home / "hooks.json").read_text())
    assert hooks["version"] == 1 and hooks["hooks"]["afterFileEdit"] == [{"command": "./fmt.sh"}]
    prompt_hooks = hooks["hooks"]["beforeSubmitPrompt"]
    assert prompt_hooks[0] == {"command": "./other.sh"} and len(prompt_hooks) == 2 and prompt_hooks[1]["timeout"] == 3
    assert set(hooks["hooks"]) == {"beforeSubmitPrompt", "afterFileEdit", "sessionStart", "postToolUse", "stop"}
    assert hooks["hooks"]["sessionStart"][0]["command"].endswith("/tracekin/start.sh") and hooks["hooks"]["sessionStart"][0]["timeout"] == 5
    mcp = json.loads((cursor_home / "mcp.json").read_text())
    assert mcp["mcpServers"]["other"] == {"type": "stdio", "command": "other"} and mcp["mcpServers"]["tracekin"]["args"][0].endswith("mcp_server.py")
    # The wrappers are argument-free executables that run the real hook.
    home = d / "cursor-home"
    Store(home).set_active_project(root)
    env = dict(base_env(), TRACEKIN_HOME=str(home))
    result = subprocess.run([str(cursor_home / "tracekin" / "hook.sh")], input=json.dumps(cursor_event(root, "beforeSubmitPrompt")), text=True, capture_output=True, env=env, cwd=cursor_home, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout) == {"continue": True}, result.stderr
    assert Store(home, create=False).snapshot()["counts"] == {"pending": 1, "sent": 0}
    removed = json.loads(run_cli("uninstall-cursor", "--cursor-home", cursor_home).stdout)
    assert removed["tracekin"] == "uninstalled" and removed["removed"] == 5
    hooks = json.loads((cursor_home / "hooks.json").read_text())
    assert hooks["hooks"] == {"beforeSubmitPrompt": [{"command": "./other.sh"}], "afterFileEdit": [{"command": "./fmt.sh"}]}
    assert json.loads((cursor_home / "mcp.json").read_text())["mcpServers"] == {"other": {"type": "stdio", "command": "other"}}
    assert not (cursor_home / "tracekin").exists()


def gemini_event(root, kind, **fields):
    event = {"session_id": "gemini-session-secret", "transcript_path": "/private/gemini/transcript.json", "cwd": str(root), "hook_event_name": kind, "timestamp": "2026-09-17T02:00:00.000Z"}
    if kind == "BeforeAgent":
        event["prompt"] = "PRIVATE_PROMPT"
    elif kind == "AfterTool":
        event.update(tool_name="run_shell_command", tool_input={"command": "PRIVATE_COMMAND"}, tool_response={"output": "PRIVATE_OUTPUT"})
    elif kind == "AfterAgent":
        event.update(prompt="PRIVATE_PROMPT", prompt_response="PRIVATE_ANSWER", stop_hook_active=False)
    elif kind == "SessionStart":
        event["source"] = "startup"
    event.update(fields)
    return event


def test_gemini_hook_payloads_get_session_turns(d, root):
    assert detect_harness(gemini_event(root, "AfterTool")) == "gemini" and detect_harness(gemini_event(root, "AfterAgent")) == "gemini"
    harness, normalized = normalize_hook_event(gemini_event(root, "AfterAgent"))
    assert harness == "gemini" and normalized["hook_event_name"] == "Stop" and normalized["last_assistant_message"] == "PRIVATE_ANSWER" and "prompt" not in normalized and "turn_id" not in normalized
    tool = normalize_hook_event(gemini_event(root, "AfterTool"))[1]
    assert tool["hook_event_name"] == "PostToolUse" and tool["tool_use_id"].endswith(":run_shell_command") and tool["tool_response"] == {"output": "PRIVATE_OUTPUT"}
    assert tool_category("run_shell_command") == "shell" and tool_category("write_file") == "edit" and tool_category("search_file_content") == "read" and tool_category("google_web_search") == "web"
    store = Store(d / "gemini")
    store.set_active_project(root)
    # Turn 1: prompt, tool, stop share one turn id; turn 2 starts with the next prompt.
    assert store.record(gemini_event(root, "BeforeAgent")) == "queued"
    assert store.record(gemini_event(root, "AfterTool")) == "queued"
    assert store.record(gemini_event(root, "AfterAgent")) == "queued"
    assert store.record(gemini_event(root, "BeforeAgent", timestamp="2026-09-17T02:01:00.000Z")) == "queued", "an identical second prompt is a new turn, not a duplicate"
    assert store.record(gemini_event(root, "AfterAgent", timestamp="2026-09-17T02:01:05.000Z")) == "queued"
    events = [e["payload"] for e in store.snapshot()["events"]]
    assert store.snapshot()["counts"] == {"pending": 5, "sent": 0} and all(p["source"] == "gemini_hook" for p in events)
    by_turn = {}
    for p in events:
        by_turn.setdefault(p["turn_id"], []).append(p["event"])
    assert len(by_turn) == 2 and sorted(len(v) for v in by_turn.values()) == [2, 3]
    stop = next(p for p in events if p["event"] == "Stop")
    assert stop["task_data"] == {"last_assistant_message": "PRIVATE_ANSWER"}
    post = next(p for p in events if p["event"] == "PostToolUse")
    assert post["tool_category"] == "shell" and post["task_data"]["tool_response"] == {"output": "PRIVATE_OUTPUT"}
    raw = store.db.read_bytes()
    assert b"gemini-session-secret" not in raw and b"/private/gemini" not in raw and b"turn-1" not in raw
    # Another session keeps its own counter; a re-fired identical tool event is a duplicate.
    assert store.record(gemini_event(root, "BeforeAgent", session_id="other")) == "queued"
    assert store.record(gemini_event(root, "AfterTool", session_id="other")) == "queued"
    assert store.record(gemini_event(root, "AfterTool", session_id="other")) == "duplicate"
    # Legacy database without the counter table still records every event.
    legacy = d / "gemini-legacy"
    db = write_legacy_db(legacy, legacy_config(root, consent_granted=True, consent_decision="allowed", sharing_enabled=True, share_all=True), FULL_SCHEMA)
    old = Store(legacy, create=False)
    assert old.record(gemini_event(root, "BeforeAgent")) == "queued" and old.record(gemini_event(root, "AfterAgent")) == "queued"
    assert {"session_turns", "control_turns"} <= tables_in(db), "the hook write path adds the auxiliary tables on demand"
    assert old.snapshot()["counts"] == {"pending": 2, "sent": 0} and len({e["payload"]["turn_id"] for e in old.snapshot()["events"]}) == 1
    # Session controls from a Gemini prompt; the CLI answers {} for every event.
    env = dict(base_env(), TRACEKIN_HOME=str(d / "gemini"), GEMINI_PROJECT_DIR=str(root), GEMINI_SESSION_ID="gemini-session-secret")
    off = run_cli("hook", "--harness", "gemini", stdin=json.dumps(gemini_event(root, "BeforeAgent", prompt="`tracekin off`")), env=env, cwd=root)
    assert off.returncode == 0 and json.loads(off.stdout) == {} and store.snapshot()["session_controls"]["paused_sessions"] == 1
    assert store.record(gemini_event(root, "AfterTool", timestamp="x")) == "session_disabled"
    assert store.record(gemini_event(root, "BeforeAgent", prompt="tracekin on")) == "session_enabled"
    assert store.record(gemini_event(root, "AfterTool", timestamp="2026-09-17T02:01:59.000Z")) == "control_turn", "the rest of the `tracekin on` turn is withheld"
    assert store.record(gemini_event(root, "BeforeAgent", prompt="next task", timestamp="2026-09-17T02:02:00.000Z")) == "queued"
    auto = run_cli("hook", stdin=json.dumps(gemini_event(root, "AfterTool", timestamp="2026-09-17T02:02:01.000Z")), env=env, cwd=root)
    # `tracekin off` dropped this session's five pending events; the other session's two remain, plus the new prompt and tool call.
    assert auto.returncode == 0 and json.loads(auto.stdout) == {} and store.snapshot()["counts"]["pending"] == 4, store.snapshot()["counts"]
    # SessionStart through the extension wrapper, from an unrelated directory.
    home = d / "gemini-ext"
    elsewhere = d / "elsewhere"
    elsewhere.mkdir()
    wrapper_env = dict(base_env(), TRACEKIN_HOME=str(home), GEMINI_PROJECT_DIR=str(root))
    try:
        started = subprocess.run([str(PLUGIN_DIR / "gemini" / "start.sh")], input=json.dumps(gemini_event(root, "SessionStart")), text=True, capture_output=True, env=wrapper_env, cwd=elsewhere, timeout=30)
        assert started.returncode == 0 and json.loads(started.stdout) == {}, started.stderr
        settle_companion(home)
        assert Store(home, create=False).snapshot()["current_project_authorized"] is True
        hooked = subprocess.run([str(PLUGIN_DIR / "gemini" / "hook.sh")], input=json.dumps(gemini_event(root, "BeforeAgent")), text=True, capture_output=True, env=wrapper_env, cwd=elsewhere, timeout=30)
        assert hooked.returncode == 0 and json.loads(hooked.stdout) == {} and Store(home, create=False).snapshot()["counts"] == {"pending": 1, "sent": 0}
    finally:
        stop_companion(home)


def test_gemini_installer_merges_settings_and_refuses_unparseable_files(d, root):
    gemini_home = d / "dot-gemini"
    gemini_home.mkdir()
    (gemini_home / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": {"BeforeAgent": [{"hooks": [{"type": "command", "command": "./other.sh"}]}], "SessionEnd": [{"hooks": [{"type": "command", "command": "./bye.sh"}]}]}, "mcpServers": {"other": {"command": "other"}}}))
    for _ in range(2):  # idempotent
        assert json.loads(run_cli("install-gemini", "--gemini-home", gemini_home).stdout)["tracekin"] == "installed"
    settings = json.loads((gemini_home / "settings.json").read_text())
    assert settings["theme"] == "dark" and settings["mcpServers"]["other"] == {"command": "other"} and settings["mcpServers"]["tracekin"]["args"][0].endswith("mcp_server.py")
    hooks = settings["hooks"]
    assert set(hooks) == {"BeforeAgent", "SessionEnd", "SessionStart", "AfterTool", "AfterAgent"}
    assert hooks["SessionEnd"] == [{"hooks": [{"type": "command", "command": "./bye.sh"}]}]
    assert hooks["BeforeAgent"][0] == {"hooks": [{"type": "command", "command": "./other.sh"}]} and len(hooks["BeforeAgent"]) == 2
    ours = hooks["BeforeAgent"][1]["hooks"][0]
    assert ours["command"].endswith("/tracekin/hook.sh") and ours["timeout"] == 3000 and ours["name"] == "tracekin"
    assert hooks["SessionStart"][0]["hooks"][0]["command"].endswith("/tracekin/start.sh") and hooks["SessionStart"][0]["hooks"][0]["timeout"] == 5000
    home = d / "gemini-installed"
    Store(home).set_active_project(root)
    result = subprocess.run([str(gemini_home / "tracekin" / "hook.sh")], input=json.dumps(gemini_event(root, "BeforeAgent")), text=True, capture_output=True, env=dict(base_env(), TRACEKIN_HOME=str(home)), cwd=root, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout) == {} and Store(home, create=False).snapshot()["counts"] == {"pending": 1, "sent": 0}
    removed = json.loads(run_cli("uninstall-gemini", "--gemini-home", gemini_home).stdout)
    assert removed["removed"] == 5
    settings = json.loads((gemini_home / "settings.json").read_text())
    assert settings["hooks"] == {"BeforeAgent": [{"hooks": [{"type": "command", "command": "./other.sh"}]}], "SessionEnd": [{"hooks": [{"type": "command", "command": "./bye.sh"}]}]}
    assert settings["mcpServers"] == {"other": {"command": "other"}} and settings["theme"] == "dark" and not (gemini_home / "tracekin").exists()
    # A settings file that does not parse (for example JSON with comments) is never clobbered.
    broken = d / "dot-gemini-broken"
    broken.mkdir()
    (broken / "settings.json").write_text('{ // comment\n  "theme": "dark" }')
    failed = run_cli("install-gemini", "--gemini-home", broken)
    assert failed.returncode != 0 and (broken / "settings.json").read_text().startswith("{ // comment")


NODE = shutil.which("node") or shutil.which("bun")


def opencode_driver(d, root, home):
    """A tiny ESM script that loads the bundled OpenCode plugin and replays hook calls."""
    driver = d / "driver.mjs"
    driver.write_text(
        f"import {{ TracekinPlugin }} from {json.dumps((PLUGIN_DIR / 'opencode' / 'tracekin.js').as_uri())};\n"
        "// A stand-in for the OpenCode SDK client: session.get answers from a table and throws otherwise.\n"
        "const known = JSON.parse(process.argv[3] || '{}');\n"
        "const client = { session: { get: async ({ path }) => { if (!(path.id in known)) throw new Error('not found'); return { data: known[path.id] }; } } };\n"
        f"const hooks = await TracekinPlugin({{ directory: {json.dumps(str(root))}, worktree: {json.dumps(str(root))}, project: {{}}, client, $: undefined }});\n"
        "for (const step of JSON.parse(process.argv[2])) {\n"
        "  if (step.kind === 'event') await hooks.event({ event: step.event });\n"
        "  else if (step.kind === 'chat') await hooks['chat.message'](step.input, step.output);\n"
        "  else if (step.kind === 'tool') await hooks['tool.execute.after'](step.input, step.output);\n"
        "}\n"
        "console.log('driver ok');\n",
        encoding="utf-8",
    )

    def drive(steps, known=None):
        result = subprocess.run([NODE, str(driver), json.dumps(steps), json.dumps(known or {})], env=dict(base_env(), TRACEKIN_HOME=str(home), TRACEKIN_PYTHON=sys.executable), capture_output=True, text=True, cwd=root, timeout=180)
        assert result.returncode == 0 and "driver ok" in result.stdout, result.stderr
    return drive


def test_opencode_plugin_bridges_events_to_the_store(d, root):
    if not NODE:
        print("  (no node or bun on PATH; OpenCode plugin test skipped)")
        return
    home = d / "opencode"
    drive = opencode_driver(d, root, home)
    session = "oc-session-secret"
    try:
        drive([{"kind": "event", "event": {"type": "session.created", "properties": {"info": {"id": session, "projectID": "p", "directory": str(root), "title": "t"}}}}])
        settle_companion(home)
        store = Store(home, create=False)
        assert store.snapshot()["current_project_authorized"] is True and store.snapshot()["config"]["projects"] == [str(root.resolve())]
        drive([
            {"kind": "chat", "input": {"sessionID": session, "messageID": "m1"}, "output": {"message": {"id": "m1", "role": "user"}, "parts": [{"type": "text", "text": "PRIVATE_PROMPT"}, {"type": "text", "text": "SYNTHETIC_HIDDEN", "synthetic": True}]}},
            {"kind": "tool", "input": {"tool": "bash", "sessionID": session, "callID": "call-1", "args": {"command": "PRIVATE_COMMAND"}}, "output": {"title": "ls", "output": "PRIVATE_OUTPUT", "metadata": {}}},
            {"kind": "event", "event": {"type": "message.updated", "properties": {"info": {"id": "a1", "sessionID": session, "role": "assistant"}}}},
            {"kind": "event", "event": {"type": "message.part.updated", "properties": {"part": {"id": "p1", "sessionID": session, "messageID": "a1", "type": "text", "text": "PRIVATE_ANSWER"}}}},
            {"kind": "event", "event": {"type": "session.idle", "properties": {"sessionID": session}}},
            {"kind": "event", "event": {"type": "session.created", "properties": {"info": {"id": "child", "parentID": session, "directory": str(root)}}}},
        ])
        events = {e["payload"]["event"]: e["payload"] for e in store.snapshot()["events"]}
        assert set(events) == {"UserPromptSubmit", "PostToolUse", "Stop"} and store.snapshot()["counts"] == {"pending": 3, "sent": 0}
        assert all(p["source"] == "opencode_hook" for p in events.values()) and len({p["turn_id"] for p in events.values()}) == 1
        assert events["UserPromptSubmit"]["task_data"] == {"prompt": "PRIVATE_PROMPT"}, "synthetic parts are not the user's prompt"
        assert events["PostToolUse"]["tool_category"] == "shell" and events["PostToolUse"]["task_data"] == {"tool_input": {"command": "PRIVATE_COMMAND"}, "tool_response": "PRIVATE_OUTPUT"}
        assert events["Stop"]["task_data"] == {"last_assistant_message": "PRIVATE_ANSWER"}
        raw = store.db.read_bytes()
        assert session.encode() not in raw and b"SYNTHETIC_HIDDEN" not in raw
        assert not (home / "runtime.json").exists(), "a subagent session must not restart the companion"
        # Subagents run in child sessions. Their tool calls belong to the root session and turn; the task
        # text the model writes for them is not a user prompt, and a child going idle is not a Stop.
        tool = lambda sid, call, output: {"kind": "tool", "input": {"tool": "grep", "sessionID": sid, "callID": call, "args": {"pattern": "x"}}, "output": {"title": "", "output": output, "metadata": {}}}
        drive([
            {"kind": "chat", "input": {"sessionID": "child", "messageID": "cm1"}, "output": {"parts": [{"type": "text", "text": "SUBAGENT_TASK_TEXT"}]}},
            tool("child", "child-call", "CHILD_OUTPUT"),
            {"kind": "event", "event": {"type": "session.created", "properties": {"info": {"id": "grandchild", "parentID": "child", "directory": str(root)}}}},
            tool("grandchild", "grandchild-call", "GRANDCHILD_OUTPUT"),
            {"kind": "event", "event": {"type": "session.idle", "properties": {"sessionID": "child"}}},
            # Sessions first seen mid-life are looked up once; an id that cannot be looked up is a root.
            tool("resumed-child", "resumed-call", "RESUMED_CHILD_OUTPUT"),
            {"kind": "chat", "input": {"sessionID": "resumed-root", "messageID": "rm1"}, "output": {"parts": [{"type": "text", "text": "RESUMED_ROOT_PROMPT"}]}},
        ], known={"resumed-child": {"id": "resumed-child", "parentID": session}, "child": {"id": "child", "parentID": session}, "grandchild": {"id": "grandchild", "parentID": "child"}})
        payloads = [e["payload"] for e in store.snapshot()["events"]]
        assert store.snapshot()["counts"] == {"pending": 7, "sent": 0}, store.snapshot()["counts"]
        assert [p["event"] for p in payloads].count("Stop") == 1 and [p["event"] for p in payloads].count("UserPromptSubmit") == 2
        root_session = events["UserPromptSubmit"]["session_id"]
        subagent_tools = [p for p in payloads if p["event"] == "PostToolUse" and p["task_data"]["tool_response"] in ("CHILD_OUTPUT", "GRANDCHILD_OUTPUT", "RESUMED_CHILD_OUTPUT")]
        assert len(subagent_tools) == 3 and all(p["session_id"] == root_session and p["turn_id"] == events["UserPromptSubmit"]["turn_id"] for p in subagent_tools)
        assert [p for p in payloads if p["event"] == "UserPromptSubmit" and p["session_id"] != root_session][0]["task_data"] == {"prompt": "RESUMED_ROOT_PROMPT"}
        assert b"SUBAGENT_TASK_TEXT" not in store.db.read_bytes()
        # `tracekin off` typed in the root session pauses it, drops its pending events, and covers its subagents.
        drive([
            {"kind": "chat", "input": {"sessionID": session, "messageID": "m2"}, "output": {"parts": [{"type": "text", "text": "`tracekin off`"}]}},
            {"kind": "tool", "input": {"tool": "read", "sessionID": session, "callID": "call-2", "args": {"filePath": "/x"}}, "output": {"title": "", "output": "PRIVATE_FILE", "metadata": {}}},
            tool("child", "child-after-off", "CHILD_AFTER_OFF"),
            tool("grandchild", "grandchild-after-off", "GRANDCHILD_AFTER_OFF"),
        ], known={"child": {"id": "child", "parentID": session}, "grandchild": {"id": "grandchild", "parentID": "child"}})
        status = store.snapshot()
        assert status["session_controls"]["paused_sessions"] == 1 and status["counts"] == {"pending": 1, "sent": 0}, status["counts"]
        raw = store.db.read_bytes()
        assert b"PRIVATE_FILE" not in raw and b"CHILD_AFTER_OFF" not in raw and b"GRANDCHILD_AFTER_OFF" not in raw and b"CHILD_OUTPUT" not in raw
    finally:
        stop_companion(home)


def test_opencode_installer_writes_loader_and_mcp(d, root):
    opencode_home = d / "dot-opencode"
    opencode_home.mkdir()
    (opencode_home / "opencode.json").write_text(json.dumps({"$schema": "https://opencode.ai/config.json", "theme": "x", "plugin": ["some-npm-plugin"], "mcp": {"other": {"type": "local", "command": ["other"]}}}))
    for _ in range(2):  # idempotent
        assert json.loads(run_cli("install-opencode", "--opencode-home", opencode_home).stdout)["tracekin"] == "installed"
    loader = (opencode_home / "plugins" / "tracekin.js").read_text()
    assert "export { TracekinPlugin } from" in loader and str(PLUGIN_DIR / "opencode" / "tracekin.js") in loader
    config = json.loads((opencode_home / "opencode.json").read_text())
    assert config["theme"] == "x" and config["plugin"] == ["some-npm-plugin"] and config["mcp"]["other"] == {"type": "local", "command": ["other"]}
    assert config["mcp"]["tracekin"]["type"] == "local" and config["mcp"]["tracekin"]["command"][1].endswith("mcp_server.py") and config["mcp"]["tracekin"]["enabled"] is True
    if NODE:
        probe = subprocess.run([NODE, "--input-type=module", "-e", f"import {{ TracekinPlugin }} from {json.dumps((opencode_home / 'plugins' / 'tracekin.js').as_uri())}; console.log(typeof TracekinPlugin)"], capture_output=True, text=True, timeout=60)
        assert probe.returncode == 0 and probe.stdout.strip() == "function", probe.stderr
    removed = json.loads(run_cli("uninstall-opencode", "--opencode-home", opencode_home).stdout)
    assert removed["removed"] == 2 and not (opencode_home / "plugins" / "tracekin.js").exists()
    config = json.loads((opencode_home / "opencode.json").read_text())
    assert config["mcp"] == {"other": {"type": "local", "command": ["other"]}} and config["plugin"] == ["some-npm-plugin"]
    # JSONC (comments) is valid for OpenCode but not for us: never overwrite it.
    jsonc = d / "dot-opencode-jsonc"
    jsonc.mkdir()
    (jsonc / "opencode.json").write_text('{ // comment\n  "theme": "x" }')
    failed = run_cli("install-opencode", "--opencode-home", jsonc)
    assert failed.returncode != 0 and (jsonc / "opencode.json").read_text().startswith("{ // comment") and not (jsonc / "plugins").exists()


def test_hook_debug_trace_is_opt_in_and_keys_only(d, root):
    store = Store(d / "trace")
    store.set_active_project(root)
    hook(store.home, tool_event(root, turn="no-trace", call="c0"))
    assert not (store.home / "hook-debug.log").exists(), "tracing must be opt-in"
    (store.home / "debug-hooks").touch()
    hook(store.home, claude_event(root, "PostToolUse", prompt_id="trace-1", tool_use_id="toolu_trace", tool_output="PRIVATE_OUTPUT"))
    hook(store.home, prompt_event(root, "tracekin status", turn="trace-status"))
    lines = [json.loads(line) for line in (store.home / "hook-debug.log").read_text().splitlines()]
    assert [(l["event"], l["harness"], l["result"]) for l in lines] == [("PostToolUse", "claude-code", "queued"), ("UserPromptSubmit", "codex", "session_enabled")]
    assert "tool_output" in lines[0]["keys"] and "prompt_id" in lines[0]["keys"], "the trace records the raw harness field names"
    raw = (store.home / "hook-debug.log").read_text()
    assert "PRIVATE" not in raw and "toolu_trace" not in raw and str(root) not in raw and "tracekin status" not in raw, "trace must never contain values"


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
    assert b"PRIVATE_COMMAND" not in store.db.read_bytes(), "dropped events are overwritten in the file, not just unlinked"
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


def test_control_turns_are_withheld_entirely(d, root):
    """`tracekin status` / `tracekin on` keep the session sharing, so the rest of that
    turn (the status tool call and the model's confirmation) must not be uploaded either."""
    store = Store(d / "control-turns")
    store.set_active_project(root)
    stop = lambda turn, text: {"hook_event_name": "Stop", "session_id": "session-secret", "turn_id": turn, "cwd": str(root), "last_assistant_message": text}
    assert store.record(tool_event(root, turn="work-1", call="w1")) == "queued"
    # Turn ids (Codex / Claude Code / Cursor): everything sharing the control prompt's turn is dropped.
    assert store.record(prompt_event(root, "tracekin status", turn="status-turn")) == "session_enabled"
    assert store.record(tool_event(root, turn="status-turn", call="status-tool", tool_name="mcp__tracekin__tracekin_status", tool_response="STATUS_TOOL_OUTPUT")) == "control_turn"
    assert store.record(stop("status-turn", "STATUS_REPLY_TEXT")) == "control_turn"
    assert store.snapshot()["counts"] == {"pending": 1, "sent": 0}
    # The next ordinary turn is captured again.
    assert store.record(prompt_event(root, "ordinary prompt", turn="work-2")) == "queued"
    assert store.record(stop("work-2", "ORDINARY_REPLY")) == "queued"
    # `tracekin on` after an off: the resume turn is withheld, the one after it is not.
    assert store.record(prompt_event(root, "tracekin off", turn="off-turn")) == "session_disabled"
    assert store.record(prompt_event(root, "tracekin on", turn="on-turn")) == "session_enabled"
    assert store.record(stop("on-turn", "RESUMED_REPLY_TEXT")) == "control_turn"
    assert store.record(tool_event(root, turn="work-3", call="w3")) == "queued"
    raw = store.db.read_bytes()
    assert b"STATUS_TOOL_OUTPUT" not in raw and b"STATUS_REPLY_TEXT" not in raw and b"RESUMED_REPLY_TEXT" not in raw and b"status-turn" not in raw
    cfg = store.snapshot()["config"]
    assert cfg["consent_decision"] == "allowed" and cfg["sharing_enabled"] is True
    # No turn ids (Gemini CLI / OpenCode): the control prompt opens its own counted turn.
    assert store.record(gemini_event(root, "BeforeAgent", prompt="first task")) == "queued"
    assert store.record(gemini_event(root, "BeforeAgent", prompt="tracekin status", timestamp="2026-09-17T03:00:00.000Z")) == "session_enabled"
    assert store.record(gemini_event(root, "AfterTool", timestamp="2026-09-17T03:00:01.000Z", tool_response={"output": "GEMINI_STATUS_OUTPUT"})) == "control_turn"
    assert store.record(gemini_event(root, "AfterAgent", timestamp="2026-09-17T03:00:02.000Z", prompt_response="GEMINI_STATUS_REPLY")) == "control_turn"
    assert store.record(gemini_event(root, "BeforeAgent", prompt="second task", timestamp="2026-09-17T03:01:00.000Z")) == "queued"
    assert store.record(gemini_event(root, "AfterAgent", timestamp="2026-09-17T03:01:05.000Z", prompt_response="SECOND_REPLY")) == "queued"
    raw = store.db.read_bytes()
    assert b"GEMINI_STATUS_OUTPUT" not in raw and b"GEMINI_STATUS_REPLY" not in raw and b"SECOND_REPLY" in raw
    # A database from before this table existed is protected by the first hook call, not only by SessionStart.
    legacy = d / "control-legacy"
    db = write_legacy_db(legacy, legacy_config(root, consent_granted=True, consent_decision="allowed", sharing_enabled=True, share_all=True), FULL_SCHEMA)
    old = Store(legacy, create=False)
    assert old.record(prompt_event(root, "tracekin status", turn="legacy-status")) == "session_enabled"
    assert old.record(stop("legacy-status", "LEGACY_STATUS_REPLY")) == "control_turn"
    assert "control_turns" in tables_in(db) and b"LEGACY_STATUS_REPLY" not in db.read_bytes()
    # The real hook reports the outcome in the keys-only trace.
    (store.home / "debug-hooks").touch()
    hook(store.home, prompt_event(root, "tracekin status", turn="traced-status"))
    hook(store.home, stop("traced-status", "TRACED_REPLY"))
    results = [json.loads(line)["result"] for line in (store.home / "hook-debug.log").read_text().splitlines()]
    assert results == ["session_enabled", "control_turn"]


def test_session_commands_tolerate_client_formatting(d, root):
    """A control line wrapped in backticks or quotes, with punctuation, or followed
    by the task on later lines must still be recognized and never uploaded."""
    recognized = {
        "`tracekin` off\n": "off", "`tracekin off`": "off", "**tracekin off**": "off", "\"tracekin off\"": "off",
        "Tracekin OFF.": "off", "/tracekin off!": "off", "tracekin  off\n\n请帮我改一下这个私密项目的代码": "off",
        "「tracekin 关闭本会话」": "off", "tracekin on\n": "on", "`tracekin on`。": "on", "tracekin status?": "status",
        "tracekin 状态": "status",
    }
    for prompt, expected in recognized.items():
        assert session_command(prompt) == expected, prompt
    for prompt in ("how does tracekin off work?", "tracekin off 是什么意思", "please run tracekin off for me", "tracekin", "off", ""):
        assert session_command(prompt) is None, prompt
    assert session_command(None) is None
    store = Store(d / "commands")
    store.set_active_project(root)
    hook(store.home, prompt_event(root, "`tracekin` off\n", turn="formatted-off"))
    status = store.snapshot()
    assert status["session_controls"]["paused_sessions"] == 1 and status["counts"] == {"pending": 0, "sent": 0}
    assert store.record(prompt_event(root, "private task", turn="after-off")) == "session_disabled"
    hook(store.home, prompt_event(root, "**tracekin on**", turn="formatted-on"))
    assert store.snapshot()["session_controls"]["paused_sessions"] == 0
    assert store.record(prompt_event(root, "tracekin status?", turn="formatted-status")) == "session_enabled"
    assert store.snapshot()["counts"] == {"pending": 0, "sent": 0}, "control prompts must never be queued"


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


def test_delivery_outcomes_never_block_the_queue(d, root):
    store = Store(d / "deliver")
    store.set_active_project(root)
    assert store.record(tool_event(root, turn="t1", call="c1")) == "queued"
    assert store.record(tool_event(root, turn="t2", call="c2")) == "queued"

    def http_error(code, body=b'{"error":"unauthorized"}'):
        def sender(endpoint, payload, token):
            raise urllib.error.HTTPError(endpoint, code, "error", {}, io.BytesIO(body))
        return sender

    def raising(error):
        def sender(endpoint, payload, token):
            raise error
        return sender

    # Auth and transport failures keep the event and report the reason.
    assert store.deliver_one(http_error(401)) == "unauthorized" and store.last_delivery_error == "HTTP 401 unauthorized"
    assert store.deliver_one(http_error(503, b"upstream")) == "retry" and store.last_delivery_error == "HTTP 503"
    assert store.deliver_one(raising(urllib.error.URLError("timed out"))) == "retry" and store.last_delivery_error == "URLError"
    assert store.deliver_one(lambda *_: False) == "retry" and store.last_delivery_error == "unacknowledged"
    assert store.snapshot()["counts"] == {"pending": 2, "sent": 0}
    # A receiver that refuses the event itself must not block the events behind it.
    assert store.deliver_one(http_error(413, b'{"error":"body_too_large"}')) == "rejected"
    assert store.last_delivery_error == "HTTP 413 body_too_large"
    assert store.snapshot()["counts"] == {"pending": 1, "sent": 0}
    # The write lock is released during the send: hooks keep recording meanwhile.
    seen = {}

    def slow_receiver(endpoint, payload, token):
        seen.update(endpoint=endpoint, token=token, event=payload["event"])
        assert store.record(tool_event(root, session="other-session", turn="t3", call="c3")) == "queued"
        return True

    assert store.deliver_one(slow_receiver) == "sent" and store.last_delivery_error is None
    assert seen == {"endpoint": PLATFORM_ENDPOINT, "token": "", "event": "PostToolUse"}
    assert store.snapshot()["counts"] == {"pending": 1, "sent": 1}
    # A global deny acknowledged while a request is in flight lets it finish; nothing new starts.

    def deny_mid_flight(endpoint, payload, token):
        store.deny_sharing()
        return True

    assert store.deliver_one(deny_mid_flight) == "sent"
    assert store.deliver_one(lambda *_: True) == "disabled"
    assert store.snapshot()["counts"] == {"pending": 0, "sent": 1}


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
    assert tables_in(db) >= set(TABLES) and "session_turns" in tables_in(db)
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
