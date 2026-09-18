"""Version, contract constants, the input error type and data-directory rules.

Everything here is needed by more than one module and depends on nothing
else in the plugin.
"""
from __future__ import annotations

import os
from pathlib import Path

PLUGIN_VERSION = "0.8.3+build.20260918081205"
SCHEMA = "tracekin.activity.v1"
PLATFORM_PROFILE = "tracekin_cloud_v1"
PLATFORM_ENDPOINT = "https://tracekin-ingest-guhpxpyula-as.a.run.app/ingest"
DEFAULT_POLICY = "enabled_on_install; SessionStart binds the current project; `tracekin off` pauses only the current session; only tracekin_deny disables globally"
MAX_INPUT = 262144

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = SCRIPTS_DIR.parent
ENTRY_SCRIPT = SCRIPTS_DIR / "tracekin.py"
MCP_SERVER = SCRIPTS_DIR / "mcp_server.py"


class InputError(ValueError):
    pass


def default_home_dir():
    """Harness-neutral data directory shared by every surface on this machine."""
    return (Path.home() / ".tracekin").resolve()


def home_dir():
    """Shared local data directory for hooks, the panel, and the MCP server.

    One directory per machine, whichever harness (Codex, Claude Code) is
    running: one consent record, one queue, one companion. Harness-provided
    variables such as ``PLUGIN_DATA`` / ``CLAUDE_PLUGIN_DATA`` are injected
    into hook commands only, never into a plugin's MCP server, so they must
    not select the directory. ``TRACEKIN_HOME`` remains an explicit override.
    """
    override = os.environ.get("TRACEKIN_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return default_home_dir()


def legacy_home_dirs():
    """Data directories used by versions <= 0.4.x, oldest last."""
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    candidates = [(base / "tracekin").resolve()]
    for variable in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        value = os.environ.get(variable)
        if value:
            candidates.append((Path(value).expanduser() / "tracekin").resolve())
    return list(dict.fromkeys(candidates))
