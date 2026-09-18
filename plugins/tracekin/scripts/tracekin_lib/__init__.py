"""Tracekin library modules, shared by the entry script, the companion and the MCP server.

``tracekin.py`` stays the single entry point and re-exports the public
names; these modules never import it, so there are no import cycles.

- ``common``: version, contract constants, ``InputError``, data-directory rules
- ``adapters``: harness dialects (Codex, Claude Code, Cursor, Gemini CLI, OpenCode)
- ``delivery``: the HTTPS sender
- ``installers``: Cursor / Gemini CLI / OpenCode user-config installers
"""
