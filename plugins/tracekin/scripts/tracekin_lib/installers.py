"""Installers for harnesses without a plugin manifest of their own.

Cursor, Gemini CLI and OpenCode read hooks and MCP servers from user-level
config files. Each installer merges Tracekin's entries into those files,
keeps everything that is not ours, and replaces its own entries on re-run;
each uninstaller removes only what the installer wrote. A config file that
cannot be parsed as JSON is never overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from .common import ENTRY_SCRIPT, MCP_SERVER, PLUGIN_DIR, PLUGIN_VERSION, InputError, home_dir

CURSOR_HOOKS = {"sessionStart": ("start", 5), "beforeSubmitPrompt": ("hook", 3), "postToolUse": ("hook", 3), "stop": ("hook", 3)}
GEMINI_HOOKS = {"SessionStart": ("start", 5000), "BeforeAgent": ("hook", 3000), "AfterTool": ("hook", 3000), "AfterAgent": ("hook", 3000)}


def load_json_object(path):
    """Existing config to merge into; a present-but-unparseable file is never overwritten."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"无法解析 {path}，为避免覆盖你的配置已中止：{error}") from error
    return data if isinstance(data, dict) else {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_wrappers(wrapper_dir, harness):
    """Argument-free wrapper scripts so a harness only needs a path to run."""
    wrapper_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    wrappers = {}
    for action in ("start", "hook"):
        wrapper = wrapper_dir / f"{action}.sh"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ENTRY_SCRIPT}" {action} --harness {harness}\n', encoding="utf-8")
        wrapper.chmod(0o755)
        wrappers[action] = str(wrapper)
    return wrappers


def cursor_paths(cursor_home):
    cursor_home = Path(cursor_home).expanduser().resolve()
    return cursor_home, cursor_home / "hooks.json", cursor_home / "mcp.json", cursor_home / "tracekin"


def install_cursor(cursor_home):
    """Register Tracekin with Cursor, which has no plugin manifest.

    Writes two argument-free wrapper scripts under ``<cursor_home>/tracekin/``
    and merges entries into ``hooks.json`` and ``mcp.json``, keeping every
    hook and server that is not ours. Re-running replaces our entries.
    """
    cursor_home, hooks_path, mcp_path, wrapper_dir = cursor_paths(cursor_home)
    hooks = load_json_object(hooks_path)
    mcp_existing = load_json_object(mcp_path)
    wrappers = write_wrappers(wrapper_dir, "cursor")
    hooks.setdefault("version", 1)
    table = hooks.get("hooks") if isinstance(hooks.get("hooks"), dict) else {}
    for event, (action, timeout) in CURSOR_HOOKS.items():
        entries = [e for e in (table.get(event) or []) if not (isinstance(e, dict) and str(e.get("command", "")).startswith(str(wrapper_dir)))]
        entries.append({"command": wrappers[action], "timeout": timeout})
        table[event] = entries
    hooks["hooks"] = table
    write_json(hooks_path, hooks)
    mcp = mcp_existing
    servers = mcp.get("mcpServers") if isinstance(mcp.get("mcpServers"), dict) else {}
    servers["tracekin"] = {"type": "stdio", "command": sys.executable, "args": [str(MCP_SERVER)]}
    mcp["mcpServers"] = servers
    write_json(mcp_path, mcp)
    return {"tracekin": "installed", "harness": "cursor", "hooks": str(hooks_path), "mcp": str(mcp_path), "wrappers": wrappers, "data_dir": str(home_dir()), "version": PLUGIN_VERSION}


def uninstall_cursor(cursor_home):
    """Remove only Tracekin's hook entries, MCP server and wrappers."""
    cursor_home, hooks_path, mcp_path, wrapper_dir = cursor_paths(cursor_home)
    removed = 0
    hooks = load_json_object(hooks_path)
    table = hooks.get("hooks") if isinstance(hooks.get("hooks"), dict) else {}
    for event in list(table):
        kept = [e for e in (table.get(event) or []) if not (isinstance(e, dict) and str(e.get("command", "")).startswith(str(wrapper_dir)))]
        removed += len(table.get(event) or []) - len(kept)
        if kept:
            table[event] = kept
        else:
            table.pop(event, None)
    if hooks_path.exists():
        hooks["hooks"] = table
        write_json(hooks_path, hooks)
    mcp = load_json_object(mcp_path)
    servers = mcp.get("mcpServers") if isinstance(mcp.get("mcpServers"), dict) else {}
    if servers.pop("tracekin", None) is not None:
        removed += 1
        mcp["mcpServers"] = servers
        write_json(mcp_path, mcp)
    for wrapper in ("start.sh", "hook.sh"):
        (wrapper_dir / wrapper).unlink(missing_ok=True)
    try:
        wrapper_dir.rmdir()
    except OSError:
        pass
    return {"tracekin": "uninstalled", "harness": "cursor", "removed": removed}


def install_gemini(gemini_home):
    """Register Tracekin in Gemini CLI's user settings.

    Gemini CLI reads ``hooks`` and ``mcpServers`` from ``~/.gemini/settings.json``
    (hook timeouts in milliseconds). Entries pointing into
    ``<gemini_home>/tracekin/`` are ours and are replaced on re-install;
    everything else in the file is kept. The extension in this repository
    (``gemini-extension.json`` + ``hooks/hooks.json``) is the alternative.
    """
    gemini_home = Path(gemini_home).expanduser().resolve()
    settings_path = gemini_home / "settings.json"
    wrapper_dir = gemini_home / "tracekin"
    settings = load_json_object(settings_path)
    wrappers = write_wrappers(wrapper_dir, "gemini")
    table = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    for event, (action, timeout) in GEMINI_HOOKS.items():
        groups = []
        for group in table.get(event) or []:
            if not isinstance(group, dict):
                continue
            kept = [h for h in (group.get("hooks") or []) if not (isinstance(h, dict) and str(h.get("command", "")).startswith(str(wrapper_dir)))]
            if kept:
                groups.append(dict(group, hooks=kept))
        groups.append({"hooks": [{"type": "command", "command": wrappers[action], "timeout": timeout, "name": "tracekin"}]})
        table[event] = groups
    settings["hooks"] = table
    servers = settings.get("mcpServers") if isinstance(settings.get("mcpServers"), dict) else {}
    servers["tracekin"] = {"command": sys.executable, "args": [str(MCP_SERVER)]}
    settings["mcpServers"] = servers
    write_json(settings_path, settings)
    return {"tracekin": "installed", "harness": "gemini", "settings": str(settings_path), "wrappers": wrappers, "data_dir": str(home_dir()), "version": PLUGIN_VERSION}


def uninstall_gemini(gemini_home):
    """Remove only Tracekin's hook groups, MCP server and wrappers from Gemini CLI settings."""
    gemini_home = Path(gemini_home).expanduser().resolve()
    settings_path = gemini_home / "settings.json"
    wrapper_dir = gemini_home / "tracekin"
    removed = 0
    settings = load_json_object(settings_path)
    table = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    for event in list(table):
        groups = []
        for group in table.get(event) or []:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            kept = [h for h in (hooks or []) if not (isinstance(h, dict) and str(h.get("command", "")).startswith(str(wrapper_dir)))]
            removed += len(hooks or []) - len(kept)
            if kept:
                groups.append(dict(group, hooks=kept))
        if groups:
            table[event] = groups
        else:
            table.pop(event, None)
    servers = settings.get("mcpServers") if isinstance(settings.get("mcpServers"), dict) else {}
    if servers.pop("tracekin", None) is not None:
        removed += 1
    if settings_path.exists():
        settings["hooks"] = table
        settings["mcpServers"] = servers
        write_json(settings_path, settings)
    for wrapper in ("start.sh", "hook.sh"):
        (wrapper_dir / wrapper).unlink(missing_ok=True)
    try:
        wrapper_dir.rmdir()
    except OSError:
        pass
    return {"tracekin": "uninstalled", "harness": "gemini", "removed": removed}


def install_opencode(opencode_home):
    """Register Tracekin with OpenCode.

    OpenCode auto-loads JS plugins from ``~/.config/opencode/plugins/`` and
    reads MCP servers from ``~/.config/opencode/opencode.json``. Write a
    one-line loader that re-exports the bundled plugin from this checkout
    (so ``git pull`` upgrades it) and merge the MCP server into the config,
    keeping everything else. A config that cannot be parsed as JSON (for
    example JSONC with comments) is left untouched and reported.
    """
    opencode_home = Path(opencode_home).expanduser().resolve()
    plugin_source = PLUGIN_DIR / "opencode" / "tracekin.js"
    loader = opencode_home / "plugins" / "tracekin.js"
    config_path = opencode_home / "opencode.json"
    config = load_json_object(config_path)
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text(f'// Tracekin loader written by tracekin.py install-opencode; re-exports the checkout so git pull upgrades it.\nexport {{ TracekinPlugin }} from {json.dumps(str(plugin_source))};\n', encoding="utf-8")
    servers = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    servers["tracekin"] = {"type": "local", "command": [sys.executable, str(MCP_SERVER)], "enabled": True}
    config["mcp"] = servers
    config.setdefault("$schema", "https://opencode.ai/config.json")
    write_json(config_path, config)
    return {"tracekin": "installed", "harness": "opencode", "plugin": str(loader), "config": str(config_path), "data_dir": str(home_dir()), "version": PLUGIN_VERSION}


def uninstall_opencode(opencode_home):
    """Remove only Tracekin's loader and MCP server from OpenCode's user config."""
    opencode_home = Path(opencode_home).expanduser().resolve()
    loader = opencode_home / "plugins" / "tracekin.js"
    config_path = opencode_home / "opencode.json"
    removed = 0
    if loader.exists():
        loader.unlink()
        removed += 1
    config = load_json_object(config_path)
    servers = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    if servers.pop("tracekin", None) is not None:
        removed += 1
        config["mcp"] = servers
        write_json(config_path, config)
    return {"tracekin": "uninstalled", "harness": "opencode", "removed": removed}
