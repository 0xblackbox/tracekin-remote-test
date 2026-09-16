#!/bin/sh
# Gemini CLI extension hook: BeforeAgent / AfterTool / AfterAgent. Resolves the
# plugin root from its own location so it works whatever working directory the
# CLI uses.
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
exec "$PY" "$HERE/../scripts/tracekin.py" hook --harness gemini
