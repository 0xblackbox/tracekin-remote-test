"""Harness dialects: tell the five hook payload shapes apart and map them onto
the Codex-shaped event that ``Store.record`` understands.

Also home to the pieces that differ per harness around the event itself:
coarse tool categories, the `tracekin off|on|status` control commands, the
stdout an observing hook must answer with, and the SessionStart project.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from .common import MAX_INPUT, InputError

HARNESSES = ("codex", "claude-code", "cursor", "gemini", "opencode")
HOOK_EVENTS = {"PostToolUse", "Stop", "UserPromptSubmit"}
# Cursor and Gemini CLI name their lifecycle events differently; map them onto the shared shape.
CURSOR_EVENTS = {"sessionStart": "SessionStart", "beforeSubmitPrompt": "UserPromptSubmit", "postToolUse": "PostToolUse", "stop": "Stop"}
GEMINI_EVENTS = {"SessionStart": "SessionStart", "BeforeAgent": "UserPromptSubmit", "AfterTool": "PostToolUse", "AfterAgent": "Stop"}
# Coarse tool categories; never the tool name, command, or arguments.
TOOL_CATEGORIES = {
    "Bash": "shell", "bash": "shell", "exec_command": "shell", "shell": "shell", "Shell": "shell", "run_terminal_cmd": "shell", "run_shell_command": "shell",
    "apply_patch": "edit", "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit", "edit_file": "edit", "search_replace": "edit", "write": "edit", "write_file": "edit", "replace": "edit", "edit": "edit", "multiedit": "edit", "patch": "edit",
    "Read": "read", "Glob": "read", "Grep": "read", "LS": "read", "read_file": "read", "list_dir": "read", "grep": "read", "codebase_search": "read", "glob_file_search": "read", "glob": "read", "search_file_content": "read", "list_directory": "read", "read_many_files": "read", "read": "read", "list": "read",
    "WebFetch": "web", "WebSearch": "web", "web_search": "web", "fetch": "web", "web_fetch": "web", "google_web_search": "web", "webfetch": "web", "websearch": "web",
    "Task": "agent", "Agent": "agent", "task": "agent",
}
# Ordered: "web" must win over "search" (WebSearch), "shell" over "read" (read_shell_output).
TOOL_CATEGORY_HINTS = (("web", "web"), ("fetch", "web"), ("shell", "shell"), ("terminal", "shell"), ("bash", "shell"), ("edit", "edit"), ("write", "edit"), ("patch", "edit"), ("replace", "edit"), ("read", "read"), ("grep", "read"), ("search", "read"), ("glob", "read"), ("list", "read"), ("agent", "agent"), ("task", "agent"))
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
COMMAND_DECORATION = re.compile(r"[`*_~\"'“”‘’「」（）()\[\]<>]")
COMMAND_TRAILING_PUNCTUATION = re.compile(r"[\s.。!！?？,，;；:：]+$")


def tool_category(name):
    """Coarse category for any harness's tool name; the name itself never leaves the machine."""
    if not isinstance(name, str) or not name:
        return "other"
    if name in TOOL_CATEGORIES:
        return TOOL_CATEGORIES[name]
    if name.startswith("mcp__") or name.startswith("mcp_"):
        return "other"
    lowered = name.lower()
    for hint, category in TOOL_CATEGORY_HINTS:
        if hint in lowered:
            return category
    return "other"


def normalize_command_text(text):
    """Reduce a typed control line to the bare command: drop markdown/quote
    decorations, trailing punctuation, letter case and extra whitespace."""
    text = COMMAND_DECORATION.sub("", text)
    text = COMMAND_TRAILING_PUNCTUATION.sub("", text.strip())
    return " ".join(text.lower().split())


def session_command(prompt):
    """Recognize `tracekin off|on|status` even when the client wrapped it in
    backticks or quotes, added punctuation, or put a task after the first line.

    The privacy-safe direction wins: a prompt that starts with the command line
    is treated as the command, so nothing from that turn is uploaded.
    """
    if not isinstance(prompt, str):
        return None
    whole = normalize_command_text(prompt)
    if whole in SESSION_COMMANDS:
        return SESSION_COMMANDS[whole]
    lines = [line for line in prompt.strip().splitlines() if line.strip()]
    if lines:
        return SESSION_COMMANDS.get(normalize_command_text(lines[0]))
    return None


def detect_harness(event):
    """Tell Codex, Claude Code, Cursor, Gemini CLI and OpenCode hook payloads apart."""
    name = event.get("hook_event_name")
    if name in CURSOR_EVENTS or "conversation_id" in event or "generation_id" in event:
        return "cursor"
    if name in ("BeforeAgent", "AfterAgent", "AfterTool", "BeforeTool") or "prompt_response" in event:
        return "gemini"
    if event.get("harness") == "opencode" or "worktree" in event:
        return "opencode"
    if "prompt_id" in event or "tool_output" in event or "tool_output_is_error" in event:
        return "claude-code"
    return "codex"


def normalize_hook_event(event, harness="auto"):
    """Map a harness-specific hook payload onto the Codex-shaped event that
    ``Store.record`` understands. Returns ``(harness, event)``.

    Claude Code: the per-turn identifier is ``prompt_id`` (not ``turn_id``),
    PostToolUse may carry ``tool_output`` (not ``tool_response``), and Stop
    may carry no assistant message. Cursor: events are named
    ``beforeSubmitPrompt`` / ``postToolUse`` / ``stop``, the identifiers are
    ``conversation_id`` / ``generation_id``, the project comes from
    ``workspace_roots``, and Stop never carries the assistant message. The
    transcript file is never read to fill any gap.
    """
    if harness == "auto":
        harness = detect_harness(event)
    if harness == "codex":
        return harness, event
    if harness == "opencode":
        # The bundled OpenCode plugin (opencode/tracekin.js) already emits the
        # shared shape; it carries no turn id, so Store.record counts turns.
        normalized = dict(event)
        normalized.pop("turn_id", None)
        return harness, normalized
    if harness == "gemini":
        # Gemini CLI: BeforeAgent / AfterTool / AfterAgent, no turn or call ids,
        # tool_response is an object, AfterAgent carries prompt_response.
        normalized = dict(event)
        name = event.get("hook_event_name")
        normalized["hook_event_name"] = GEMINI_EVENTS.get(name, name)
        normalized.pop("turn_id", None)  # assigned per session by Store.record
        if normalized["hook_event_name"] == "PostToolUse" and not normalized.get("tool_use_id"):
            normalized["tool_use_id"] = f"{event.get('timestamp') or int(time.time() * 1000)}:{event.get('tool_name', '')}"[:4096]
        if normalized["hook_event_name"] == "Stop":
            if "last_assistant_message" not in normalized and isinstance(event.get("prompt_response"), str):
                normalized["last_assistant_message"] = event["prompt_response"]
            normalized.pop("prompt", None)  # already captured by the BeforeAgent event
        return harness, normalized
    if harness == "cursor":
        normalized = dict(event)
        normalized["hook_event_name"] = CURSOR_EVENTS.get(event.get("hook_event_name"), event.get("hook_event_name"))
        if not normalized.get("session_id") or normalized["hook_event_name"] != "SessionStart":
            normalized["session_id"] = event.get("conversation_id") or event.get("session_id")
        normalized["turn_id"] = event.get("generation_id") or event.get("turn_id") or ("t-" + str(event.get("tool_use_id") or int(time.time() * 1000)))
        if not normalized.get("cwd"):
            roots = event.get("workspace_roots")
            normalized["cwd"] = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else (os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or "")
        if "tool_response" not in normalized and "tool_output" in normalized:
            normalized["tool_response"] = normalized["tool_output"]
        if not normalized.get("tool_use_id") and normalized["hook_event_name"] == "PostToolUse":
            normalized["tool_use_id"] = "call-" + str(int(time.time() * 1000))
        return harness, normalized
    if harness != "claude-code":
        raise InputError("未知的 harness")
    normalized = dict(event)
    if not normalized.get("turn_id"):
        turn = normalized.get("prompt_id")
        if isinstance(turn, str) and turn:
            normalized["turn_id"] = turn
        else:
            # Older builds without prompt_id: Store.record assigns a per-session turn.
            normalized.pop("turn_id", None)
    if "tool_response" not in normalized and "tool_output" in normalized:
        normalized["tool_response"] = normalized["tool_output"]
    return harness, normalized


def hook_response(harness, event):
    """The stdout a harness expects from an observing hook.

    Cursor's ``beforeSubmitPrompt`` must answer ``{"continue": true}`` or the
    prompt is blocked; every other hook, on every harness, accepts ``{}``.
    """
    name = event.get("hook_event_name") if isinstance(event, dict) else None
    if harness == "cursor" and name in ("beforeSubmitPrompt", "UserPromptSubmit"):
        return {"continue": True}
    return {}


def session_start_project():
    """The project a SessionStart hook should bind.

    Codex and Claude Code send ``cwd``; Cursor sends ``workspace_roots`` and
    runs user-level hooks from ``~/.cursor``, so the harness-provided project
    variables are preferred over the process working directory.
    """
    fallback = os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if sys.stdin.isatty():
        return fallback
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return fallback
    try:
        value = json.loads(raw) if raw else {}
        if not isinstance(value, dict):
            return fallback
        cwd = value.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
        roots = value.get("workspace_roots")
        if isinstance(roots, list) and roots and isinstance(roots[0], str) and roots[0]:
            return roots[0]
        return fallback
    except (ValueError, TypeError):
        return fallback
