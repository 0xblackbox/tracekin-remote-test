#!/usr/bin/env python3
"""Tracekin local companion. Standard-library only; consent precedes collection."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_INPUT = 262144
MAX_EVENTS = 1000
EVENT_TTL = 7 * 86400
SESSION_OVERRIDE_TTL = 30 * 86400
SCHEMA = "tracekin.activity.v1"
PLUGIN_VERSION = "0.2.2+codex.20260916053911"
SESSION_COMMANDS = {
    "tracekin off": "off",
    "/tracekin off": "off",
    "tracekin 关闭本会话": "off",
    "tracekin on": "on",
    "/tracekin on": "on",
    "tracekin 开启本会话": "on",
    "tracekin status": "status",
    "/tracekin status": "status",
    "tracekin 状态": "status",
}


def home_dir():
    default = Path(os.environ["PLUGIN_DATA"]) / "tracekin" if os.environ.get("PLUGIN_DATA") else Path.home() / ".codex" / "tracekin"
    return Path(os.environ.get("TRACEKIN_HOME", str(default))).expanduser().resolve()


class InputError(ValueError):
    pass


def valid_endpoint(value, demo_endpoint=None):
    if not isinstance(value, str) or len(value) > 2048:
        raise InputError("请填写有效的 HTTPS 接收地址")
    if demo_endpoint and value == demo_endpoint:
        return value
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment or p.query:
        raise InputError("接收地址应为 HTTPS，且不包含账号、查询参数或片段")
    try:
        p.port
    except ValueError as e:
        raise InputError("端口无效") from e
    return value


class Store:
    def __init__(self, home, demo=False, create=True):
        self.home = Path(home).resolve()
        self.db = self.home / "tracekin.sqlite3"
        self.demo = demo
        self.demo_endpoint = None
        if not create:
            return
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','sent')), created REAL NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS session_overrides (session_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), updated REAL NOT NULL)")
            if not c.execute("SELECT 1 FROM config").fetchone():
                cfg = dict(consent_granted=False, sharing_enabled=False, share_all=False, projects=[], endpoint="", endpoint_token="", use_existing_pet=True, pet={"name": "Trace", "concept": "一只小巧、温暖的紫色绒毛伙伴，安静地陪我写代码"}, salt=secrets.token_hex(32), demo=demo)
                c.execute("INSERT INTO config VALUES(1, ?)", (json.dumps(cfg),))
            cfg = self.read_config(c)
            migrated = False
            if "consent_granted" not in cfg:
                cfg["consent_granted"] = cfg.get("sharing_enabled") is True
                migrated = True
            if "use_existing_pet" not in cfg:
                # Existing installs predate the original-pet switch. Default to
                # reusing the currently selected Codex pet when migrating.
                cfg["use_existing_pet"] = True
                migrated = True
            if migrated:
                c.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(cfg, ensure_ascii=False),))
            if cfg["demo"] != demo:
                raise InputError("演示与正式模式需要使用独立的数据目录")
        self.db.chmod(0o600)

    def connect(self):
        c = sqlite3.connect(self.db, timeout=4)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def read_config(c):
        return json.loads(c.execute("SELECT value FROM config WHERE id=1").fetchone()[0])

    def enabled(self):
        if not self.db.exists():
            return False
        try:
            with self.connect() as c:
                cfg = self.read_config(c)
                if cfg.get("sharing_enabled") is not True or cfg.get("consent_granted") is not True:
                    return False
                if not cfg.get("projects") or not cfg.get("endpoint"):
                    return False
                if cfg.get("demo"):
                    parsed = urllib.parse.urlsplit(cfg["endpoint"])
                    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                valid_endpoint(cfg["endpoint"])
                return True
        except (sqlite3.Error, ValueError, TypeError):
            return False

    @staticmethod
    def prune(c):
        # Retain bounded local receipts for deduplication, not full transcripts.
        c.execute("DELETE FROM events WHERE created < ?", (time.time() - EVENT_TTL,))
        c.execute("DELETE FROM session_overrides WHERE updated < ?", (time.time() - SESSION_OVERRIDE_TTL,))

    def snapshot(self):
        with self.connect() as c:
            self.prune(c)
            cfg = self.read_config(c)
            cfg.pop("salt", None)
            cfg.pop("endpoint_token", None)
            counts = {r[0]: r[1] for r in c.execute("SELECT status, count(*) FROM events GROUP BY status")}
            paused_sessions = c.execute("SELECT count(*) FROM session_overrides WHERE enabled=0").fetchone()[0]
            events = [{"payload": json.loads(r["payload"]), "status": r["status"]} for r in c.execute("SELECT * FROM events ORDER BY created DESC LIMIT 12")]
        return {"config": cfg, "counts": {"pending": counts.get("pending", 0), "sent": counts.get("sent", 0)}, "events": events, "session_controls": {"paused_sessions": paused_sessions, "commands": ["tracekin off", "tracekin on", "tracekin status"]}, "schema": SCHEMA, "native_pet": "configured_in_codex", "proof_status": "activity_only_not_training_proof"}

    def configure(self, changes):
        if not isinstance(changes, dict) or set(changes) - {"consent_granted", "sharing_enabled", "share_all", "projects", "endpoint", "endpoint_token", "use_existing_pet", "pet"}:
            raise InputError("设置字段无效")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cfg = self.read_config(c)
            old_scope = (cfg["projects"], cfg["endpoint"], cfg.get("endpoint_token", ""))
            if "pet" in changes:
                pet = changes["pet"]
                if not isinstance(pet, dict) or set(pet) != {"name", "concept"} or not all(isinstance(v, str) for v in pet.values()):
                    raise InputError("宠物资料无效")
                if not 1 <= len(pet["name"].strip()) <= 24 or not 1 <= len(pet["concept"].strip()) <= 500:
                    raise InputError("名字需 1–24 字，描述需 1–500 字")
                cfg["pet"] = {k: v.strip() for k, v in pet.items()}
            if "projects" in changes:
                projects = changes["projects"]
                if not isinstance(projects, list) or len(projects) > 20 or any(not isinstance(p, str) or not Path(p).expanduser().is_absolute() for p in projects):
                    raise InputError("每行填写一个项目的完整目录，最多 20 个")
                resolved = [str(Path(p).expanduser().resolve()) for p in projects]
                if any(not Path(p).is_dir() or Path(p) in (Path("/"), Path.home()) for p in resolved):
                    raise InputError("请选择存在的具体项目目录，而不是根目录或整个用户目录")
                cfg["projects"] = list(dict.fromkeys(resolved))
            if "endpoint" in changes:
                endpoint = changes["endpoint"]
                cfg["endpoint"] = valid_endpoint(endpoint, self.demo_endpoint) if endpoint else ""
            if "endpoint_token" in changes:
                endpoint_token = changes["endpoint_token"]
                if not isinstance(endpoint_token, str) or len(endpoint_token) > 4096 or any(ord(ch) < 32 for ch in endpoint_token):
                    raise InputError("接收服务令牌无效")
                cfg["endpoint_token"] = endpoint_token
            if "use_existing_pet" in changes:
                if type(changes["use_existing_pet"]) is not bool:
                    raise InputError("原有宠物开关应为布尔值")
                cfg["use_existing_pet"] = changes["use_existing_pet"]
            if "sharing_enabled" in changes:
                if type(changes["sharing_enabled"]) is not bool:
                    raise InputError("共享开关应为布尔值")
                cfg["sharing_enabled"] = changes["sharing_enabled"]
            if "consent_granted" in changes:
                if type(changes["consent_granted"]) is not bool:
                    raise InputError("授权状态应为布尔值")
                cfg["consent_granted"] = changes["consent_granted"]
                if not cfg["consent_granted"]:
                    cfg["sharing_enabled"] = False
            if "share_all" in changes:
                if type(changes["share_all"]) is not bool:
                    raise InputError("全部任务开关应为布尔值")
                cfg["share_all"] = changes["share_all"]
            # Changing scope or destination requires a fresh explicit opt-in.
            scope_changed = old_scope != (cfg["projects"], cfg["endpoint"], cfg.get("endpoint_token", ""))
            if scope_changed and not (changes.get("consent_granted") is True and changes.get("sharing_enabled") is True):
                cfg["sharing_enabled"] = False
                cfg["consent_granted"] = False
            if cfg["sharing_enabled"] and not cfg.get("consent_granted"):
                raise InputError("请先在本地面板完成一次共享授权")
            if cfg["sharing_enabled"] and (not cfg["projects"] or not cfg["endpoint"]):
                raise InputError("先配置项目目录和接收地址，再开启共享")
            if cfg["sharing_enabled"]:
                valid_endpoint(cfg["endpoint"], self.demo_endpoint)
            if not cfg["sharing_enabled"] or scope_changed:
                c.execute("DELETE FROM events WHERE status='pending'")
            if not cfg.get("consent_granted"):
                c.execute("DELETE FROM session_overrides")
            c.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(cfg, ensure_ascii=False),))
        return self.snapshot()

    @staticmethod
    def digest(cfg, *values):
        raw = json.dumps(values, separators=(",", ":")).encode()
        return hmac.new(bytes.fromhex(cfg["salt"]), raw, hashlib.sha256).hexdigest()

    @staticmethod
    def parse_session_command(event):
        if event.get("hook_event_name") != "UserPromptSubmit" or not isinstance(event.get("prompt"), str):
            return None
        return SESSION_COMMANDS.get(" ".join(event["prompt"].strip().split()).lower())

    def apply_session_command(self, c, cfg, session_id, command):
        session_hash = self.digest(cfg, "session", session_id)
        if command == "off":
            c.execute("INSERT OR REPLACE INTO session_overrides VALUES (?, 0, ?)", (session_hash, time.time()))
            for row in c.execute("SELECT id, payload FROM events WHERE status='pending'").fetchall():
                try:
                    if json.loads(row["payload"]).get("session_id") == session_hash:
                        c.execute("DELETE FROM events WHERE id=?", (row["id"],))
                except (ValueError, TypeError):
                    continue
            return "session_disabled"
        if command == "on":
            c.execute("DELETE FROM session_overrides WHERE session_id=?", (session_hash,))
            return "session_enabled" if cfg.get("sharing_enabled") else "global_disabled"
        if not cfg.get("sharing_enabled"):
            return "global_disabled"
        row = c.execute("SELECT enabled FROM session_overrides WHERE session_id=?", (session_hash,)).fetchone()
        return "session_disabled" if row and row[0] == 0 else "session_enabled"

    def record(self, event):
        if not isinstance(event, dict):
            return "invalid"
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cfg = self.read_config(c)
            kind = event.get("hook_event_name")
            if kind not in {"PostToolUse", "Stop", "UserPromptSubmit"}:
                return "unsupported"
            if not isinstance(event.get("session_id"), str) or not event["session_id"] or len(event["session_id"]) > 4096:
                return "invalid"
            command = self.parse_session_command(event)
            if command:
                return self.apply_session_command(c, cfg, event["session_id"], command)
            if not cfg["sharing_enabled"] or not cfg.get("consent_granted"):
                return "disabled"
            session_hash = self.digest(cfg, "session", event["session_id"])
            row = c.execute("SELECT enabled FROM session_overrides WHERE session_id=?", (session_hash,)).fetchone()
            if row and row[0] == 0:
                return "session_disabled"
            for key in ("turn_id", "cwd"):
                if not isinstance(event.get(key), str) or not event[key] or len(event[key]) > 4096:
                    return "invalid"
            if not Path(event["cwd"]).is_absolute():
                return "invalid"
            cwd = Path(event["cwd"]).resolve()
            matched = [p for p in cfg["projects"] if cwd == Path(p) or Path(p) in cwd.parents]
            # Full-task mode controls payload detail, never project scope. A
            # configured project list is always required so the quick-start
            # consent cannot silently capture unrelated working directories.
            if not matched:
                return "excluded"
            call_id = event.get("tool_use_id") if kind == "PostToolUse" else ("prompt" if kind == "UserPromptSubmit" else "turn-end")
            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 4096:
                return "invalid"
            event_id = self.digest(cfg, event["session_id"], event["turn_id"], kind, call_id)
            payload = {
                "schema": SCHEMA, "id": event_id,
                "project_id": self.digest(cfg, "project", max(matched, key=len)),
                "session_id": session_hash,
                "turn_id": self.digest(cfg, "turn", event["turn_id"]),
                "event": kind, "observed_at": int(time.time()),
                "source": "codex_hook", "synthetic": cfg["demo"], "privacy_mode": "full_task" if cfg.get("share_all") else "activity_only",
            }
            if cfg.get("share_all"):
                # Full mode is deliberately explicit: these are the fields the hook can see.
                # The transcript file itself is never opened implicitly.
                task_data = {}
                for field in ("prompt", "last_assistant_message", "tool_input", "tool_response"):
                    if field in event:
                        task_data[field] = event[field]
                payload["task_data"] = task_data
            if kind == "PostToolUse":
                # Never serialize tool names, commands, outputs, prompts, or transcript paths.
                name = event.get("tool_name")
                payload["tool_category"] = {"Bash": "shell", "exec_command": "shell", "apply_patch": "edit", "Read": "read", "Write": "edit", "Edit": "edit"}.get(name, "other") if isinstance(name, str) else "other"
            self.prune(c)
            if c.execute("SELECT count(*) FROM events").fetchone()[0] >= MAX_EVENTS:
                return "full"
            changed = c.execute("INSERT OR IGNORE INTO events VALUES (?, ?, 'pending', ?)", (event_id, json.dumps(payload), time.time())).rowcount
            return "queued" if changed else "duplicate"

    def deliver_one(self, sender):
        # Hold the write transaction through bounded delivery. After disable is acknowledged,
        # no old queued event can start transmission. A request already in flight may finish.
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cfg = self.read_config(c)
            if not cfg["sharing_enabled"]:
                return "disabled"
            self.prune(c)
            row = c.execute("SELECT * FROM events WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return "empty"
            valid_endpoint(cfg["endpoint"], self.demo_endpoint)
            payload = json.loads(row["payload"])
            try:
                acknowledged = sender(cfg["endpoint"], payload, cfg.get("endpoint_token", ""))
            except (OSError, ValueError, urllib.error.URLError):
                return "retry"
            if acknowledged is not True:
                return "retry"
            c.execute("UPDATE events SET status='sent' WHERE id=?", (row["id"],))
            return "sent"

    def clear_local(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            cfg = self.read_config(c)
            cfg["sharing_enabled"] = False
            cfg["consent_granted"] = False
            c.execute("UPDATE config SET value=? WHERE id=1", (json.dumps(cfg),))
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM session_overrides")
        return self.snapshot()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_https(endpoint, payload, endpoint_token=""):
    body = json.dumps({"schema": SCHEMA, "events": [payload]}).encode()
    headers = {"Content-Type": "application/json", "Idempotency-Key": payload["id"]}
    if endpoint_token:
        headers["Authorization"] = "Bearer " + endpoint_token
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=2) as response:
        result = json.loads(response.read(65537))
        return isinstance(result, dict) and isinstance(result.get("accepted"), list) and payload["id"] in result["accepted"]


def run_hook(home):
    store = Store(home, create=False)
    # The off path intentionally does not read stdin or inspect session log files.
    if not store.enabled():
        return
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return
    store.record(json.loads(raw))


def start_companion(home):
    """Start the current loopback UI, replacing a companion from an older plugin cache."""
    runtime = Path(home) / "runtime.json"
    if runtime.exists():
        try:
            info = json.loads(runtime.read_text())
            pid = int(info.get("pid", 0))
            os.kill(pid, 0)
            if info.get("version") == PLUGIN_VERSION:
                return "already_running"
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            runtime.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            runtime.unlink(missing_ok=True)
    root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    Path(home).mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(os.devnull, "w") as sink:
        subprocess.Popen([sys.executable, str(root / "scripts" / "serve.py"), "--home", str(home)], stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True, close_fds=True)
    return "started"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["hook", "status", "start"])
    parser.add_argument("--home", type=Path, default=home_dir())
    args = parser.parse_args()
    if args.action == "start":
        try:
            print(json.dumps({"tracekin": start_companion(args.home)}))
        except (OSError, ValueError, TypeError):
            print(json.dumps({"tracekin": "not_started"}))
    elif args.action == "hook":
        try:
            run_hook(args.home)
        except (OSError, ValueError, TypeError, sqlite3.Error):
            pass  # A companion must not block the user's Codex task.
        print("{}")
    elif not (args.home / "tracekin.sqlite3").exists():
        print(json.dumps({"sharing_enabled": False, "initialized": False}))
    else:
        print(json.dumps(Store(args.home, create=False).snapshot(), ensure_ascii=False))


if __name__ == "__main__":
    main()
