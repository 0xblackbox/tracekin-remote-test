#!/bin/sh
# Cursor plugin hook: sessionStart. Resolves the plugin root from its own
# location so it works whatever working directory Cursor uses.
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
exec "$PY" "$HERE/../scripts/tracekin.py" start --harness cursor
