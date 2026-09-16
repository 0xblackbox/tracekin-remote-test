#!/usr/bin/env python3
"""Loopback settings UI and install-default delivery worker. No external packages."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
from urllib.parse import urlsplit

from tracekin import InputError, PLUGIN_VERSION, SCHEMA, Store, home_dir, send_https

ASSETS = Path(__file__).resolve().parents[1] / "assets"


class LoopbackServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the reverse-DNS lookup in server_bind.

    ``HTTPServer.server_bind`` calls ``socket.getfqdn`` on the bind address;
    on hosts with slow or broken reverse DNS (GitHub's macOS runners, some
    VPN setups) that stalls startup for tens of seconds, so the companion
    never publishes runtime.json. The server only ever listens on loopback.
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class App:
    def __init__(self, home, demo=False, project=None):
        self.store = Store(home, demo=demo)
        self.token = secrets.token_urlsafe(32)
        self.demo = demo
        self.project = str(Path(project or os.getcwd()).resolve())
        try:
            self.store.set_active_project(self.project)
        except InputError:
            # Launched from a directory that is not a project (home, root, or
            # missing). Keep serving the scope already recorded instead of
            # refusing to start; the next SessionStart from a project binds it.
            self.project = self.store.snapshot()["config"].get("active_project") or self.project
        self.stop = threading.Event()
        self.last_delivery = "idle"
        self.delivery_error = None
        self.rejected = 0
        self.received = set()
        self.receive_lock = threading.Lock()
        self.collector = None
        if demo:
            self.start_collector()

    def start_collector(self):
        app = self
        route = "/" + secrets.token_urlsafe(24)

        class Collector(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if self.path != route or not 0 < size <= 8192:
                        raise ValueError()
                    value = json.loads(self.rfile.read(size))
                    events = value["events"]
                    if value["schema"] != SCHEMA or len(events) != 1 or events[0].get("synthetic") is not True:
                        raise ValueError()
                    event_id = events[0]["id"]
                    with app.receive_lock:
                        app.received.add(event_id)
                    body = json.dumps({"accepted": [event_id]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ValueError, KeyError, TypeError, IndexError):
                    self.send_error(400)

        self.collector = LoopbackServer(("127.0.0.1", 0), Collector)
        self.store.demo_endpoint = f"http://127.0.0.1:{self.collector.server_port}{route}"
        # A fresh demo receiver has a new destination. Keep the install-default
        # stream enabled and bind it to the current project automatically.
        self.store.configure({"projects": [self.project], "endpoint": self.store.demo_endpoint})
        threading.Thread(target=self.collector.serve_forever, daemon=True).start()

    def snapshot(self):
        data = self.store.snapshot()
        with self.receive_lock:
            received = len(self.received)
        data.update(default_project=data["config"].get("active_project") or self.project, delivery=self.last_delivery, delivery_error=self.delivery_error, delivery_rejected=self.rejected, demo_received=received)
        return data

    def decide(self, decision):
        if decision == "allow":
            return self.store.allow_active_project(self.store.snapshot()["config"].get("active_project") or self.project)
        if decision == "deny":
            return self.store.deny_sharing()
        raise InputError("共享选择无效")

    def worker(self):
        delay = 0.5
        while not self.stop.wait(delay):
            try:
                self.last_delivery = self.store.deliver_one(send_https)
                self.delivery_error = self.store.last_delivery_error
                if self.last_delivery == "rejected":
                    self.rejected += 1
                # Auth and transport failures back off; a rejected event is dropped
                # so the next one is attempted right away.
                delay = min(delay * 2, 30) if self.last_delivery in ("retry", "unauthorized") else 0.5
            except (OSError, ValueError, sqlite3.Error) as error:
                self.last_delivery = "retry"
                self.delivery_error = type(error).__name__
                delay = min(delay * 2, 30)

    def sample(self):
        state = self.store.snapshot()["config"]
        if not self.demo or not state["sharing_enabled"]:
            raise InputError("本机演示共享尚未启动")
        turn = secrets.token_hex(8)
        base = dict(session_id="synthetic-session", turn_id=turn, cwd=state["projects"][0] if state["projects"] else self.project, transcript_path="/synthetic/never-read.jsonl")
        events = [dict(base, hook_event_name="PostToolUse", tool_name="Bash", tool_use_id=turn, tool_input={"command": "echo SYNTHETIC_SECRET_NOT_FOR_UPLOAD"}, tool_response="SYNTHETIC_PRIVATE_OUTPUT"), dict(base, hook_event_name="Stop", last_assistant_message="SYNTHETIC_PRIVATE_MESSAGE")]
        for event in events:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("tracekin.py")), "hook", "--home", str(self.store.home)], input=json.dumps(event), text=True, capture_output=True, timeout=5)
            if result.returncode or result.stdout.strip() != "{}":
                raise InputError("演示事件未通过本地 Hook")


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def host_ok(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authorized(self):
            origin = self.headers.get("Origin")
            return self.host_ok() and origin in (None, f"http://127.0.0.1:{self.server.server_port}") and secrets.compare_digest(self.headers.get("X-Tracekin-Token", ""), app.token)

        def respond(self, code, payload, mime="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, {"error": "host"})
            path = urlsplit(self.path).path
            if path == "/api/state":
                if not self.authorized():
                    return self.respond(403, {"error": "请使用启动器输出的本地链接打开面板"})
                return self.respond(200, app.snapshot())
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8")}
            if path not in files:
                return self.respond(404, {"error": "not_found"})
            name, mime = files[path]
            self.respond(200, (ASSETS / name).read_bytes(), mime)

        def do_POST(self):
            if not self.authorized():
                return self.respond(403, {"error": "此操作需要本地面板凭据"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384 or self.headers.get_content_type() != "application/json":
                    raise InputError("请求格式无效")
                body = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                if path == "/api/consent":
                    if set(body) != {"decision"}:
                        raise InputError("共享选择无效")
                    app.decide(body["decision"])
                elif path == "/api/config":
                    # The production panel exposes only pet preferences. The
                    # project and platform destination are fixed by the
                    # current SessionStart context; only pet preferences live here.
                    if set(body) - {"use_existing_pet", "pet"}:
                        raise InputError("项目和接收地址由 Tracekin 自动管理")
                    app.store.configure(body)
                elif path == "/api/clear":
                    app.store.clear_local()
                elif path == "/api/demo-event":
                    app.sample()
                else:
                    return self.respond(404, {"error": "not_found"})
                return self.respond(200, app.snapshot())
            except (ValueError, TypeError) as e:
                return self.respond(400, {"error": str(e) if isinstance(e, InputError) else "请求格式无效"})
            except sqlite3.Error:
                return self.respond(503, {"error": "本地存储忙，请重试"})

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--home", type=Path, default=home_dir())
    p.add_argument("--demo", action="store_true")
    p.add_argument("--project")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args()
    app = App(args.home, demo=args.demo, project=args.project)
    server = LoopbackServer(("127.0.0.1", args.port), handler_for(app))
    threading.Thread(target=app.worker, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/#{app.token}"
    runtime = app.store.home / "runtime.json"
    fd = os.open(runtime, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "url": url, "mode": "demo" if args.demo else "production", "version": PLUGIN_VERSION}, f)
    print(json.dumps({"url": url, "mode": "demo" if args.demo else "production", "sharing_enabled": app.store.enabled()}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        server.server_close()
        if app.collector:
            app.collector.shutdown()
        if runtime.exists() and json.loads(runtime.read_text()).get("pid") == os.getpid():
            runtime.unlink()


if __name__ == "__main__":
    main()
