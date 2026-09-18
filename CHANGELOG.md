# Changelog

English | [简体中文](CHANGELOG.zh-CN.md)

Versions are `X.Y.Z+build.<timestamp>` (`+codex.<timestamp>` for 0.4.x). The timestamp only exists so plugin caches notice a new build.

## 0.8.3 · 2026-09-18

- Code split, no behavior change: `scripts/tracekin.py` (1306 lines) now keeps only the entry point, the SQLite store, the sharing policy and the companion process (848 lines). The parts that vary per harness moved into `scripts/tracekin_lib/`: `common.py` (version, contract constants, data directories), `adapters.py` (the Codex / Claude Code / Cursor / Gemini CLI / OpenCode dialects, tool categories, control commands, hook stdout), `delivery.py` (the HTTPS sender) and `installers.py` (Cursor / Gemini CLI / OpenCode).
- Every entry point is unchanged (`tracekin.py hook | start | status | install-*`, the wrappers, the OpenCode plugin), and `serve.py`, `mcp_server.py` and the tests keep importing the public names from `tracekin`. The library never imports the entry script back; a new test checks that and that the facade exports what the other scripts need.
- The version constant now lives in `scripts/tracekin_lib/common.py`; the version-sync test checks that file too.

## 0.8.2 · 2026-09-18

- Privacy fix for OpenCode: subagents run in child sessions with their own `sessionID`, so `tracekin off` in the session the user was typing in did not cover their tool calls. The plugin now tracks each session's parent (from `session.created` / `session.updated`, with one SDK lookup for sessions first seen mid-life) and reports subagent tool calls under the root session and its current turn.
- The task text the model writes for a subagent is no longer uploaded as a user prompt or parsed as a control command, and a subagent going idle no longer produces a Stop event.
- Events dropped locally (`tracekin off`, `tracekin_deny`, clearing, pruning, receiver rejections) are now overwritten in the database file (`PRAGMA secure_delete`); before, their plain text stayed in the file's free pages on disk. This concerned the local file only; dropped events were never uploaded.

## 0.8.1 · 2026-09-17

- Privacy fix: the whole turn a control command opens is withheld. Previously `tracekin status` and `tracekin on` kept the prompt back but still uploaded the status tool call and the model's confirmation from the same turn. Such turns are now recorded in a `control_turns` table and later events of the turn return `control_turn`; harnesses without a turn id give the control prompt its own counted turn.
- The hook write path creates the auxiliary tables (`session_turns`, `control_turns`) on demand, so a database from an older version is covered by its first hook call instead of its next `SessionStart`.

## 0.8.0 · 2026-09-17

- Added OpenCode: `opencode/tracekin.js`, an ESM plugin with no npm dependencies, subscribes to `session.created` / `chat.message` / `tool.execute.after` / `session.idle`, translates them into the same payloads the other harnesses send, and hands them to `tracekin.py`. The assistant reply comes from the latest assistant text part; subagent sessions do not restart the companion.
- `tracekin.py install-opencode` writes a loader into `~/.config/opencode/plugins/` and merges the MCP server into `opencode.json`; `uninstall-opencode` removes only its own entries.
- The test suite drives the real plugin file with Node end to end, and skips when Node is not available.
- Tool categories now cover OpenCode's built-in tool names.

## 0.7.0 · 2026-09-17

- Added Gemini CLI: `SessionStart` / `BeforeAgent` / `AfterTool` / `AfterAgent` are normalized onto the shared event model, `AfterAgent`'s `prompt_response` becomes the assistant reply, and the `tool_response` object is kept as is.
- Harnesses without a turn id get turns counted per session in the database (new `session_turns` table): a new prompt opens the next turn and tool and stop events join the current one; a legacy database without the table falls back to a unique id per event.
- Two install paths: the repository root is the Gemini extension (`gemini-extension.json` plus `hooks/hooks.json`, `gemini extensions install <repo>`), and `tracekin.py install-gemini` merges into `~/.gemini/settings.json` (idempotent, removes only its own entries).
- Installers stop instead of overwriting an existing config file they cannot parse (for example JSON with comments); the Cursor installer benefits too.
- Tool categories now cover Gemini's built-in tool names.

## 0.6.1 · 2026-09-17

- The companion no longer performs a reverse-DNS lookup on `127.0.0.1` at startup. Where reverse resolution is slow (GitHub's macOS runners, some VPN setups) that lookup stalled the companion for tens of seconds and `runtime.json` never appeared in time.
- The companion's output is logged to `~/.tracekin/companion.log` to diagnose a dashboard that does not come up.
- Tests no longer leave a temporary companion running while events are queued, so no test data is delivered to the real receiver.
- Documentation reorganized: a project README, a topic-based technical reference, a CHANGELOG and a standalone cross-machine acceptance walkthrough; a GitHub Actions test workflow and the MIT license were added.

## 0.6.0 · 2026-09-16

- Added Cursor: `sessionStart` / `beforeSubmitPrompt` / `postToolUse` / `stop` are normalized onto the shared event model, `conversation_id` / `generation_id` serve as session and turn ids, the project comes from `workspace_roots`, and `beforeSubmitPrompt` is answered with `{"continue": true}`.
- Two install paths: `tracekin.py install-cursor` merges into `~/.cursor/hooks.json` and `mcp.json` (idempotent, removes only its own entries), and `.cursor-plugin/plugin.json` serves `~/.cursor/plugins/local`.
- Tool categories fall back to keyword matching for unknown tool names.

## 0.5.1 · 2026-09-16

- When a session starts outside a project directory (such as `~`), the companion falls back to the recorded project instead of exiting.
- Optional keys-only hook trace via `~/.tracekin/debug-hooks`.
- Observed on Claude Code 2.1.273: `PostToolUse` sends `tool_response` and `Stop` sends `last_assistant_message`, unlike the documentation; both namings are accepted.

## 0.5.0 · 2026-09-16

- Added Claude Code: `.claude-plugin/plugin.json`, a shared `hooks/hooks.json` (`${CLAUDE_PLUGIN_ROOT}`), and a separate MCP config per harness.
- The data directory is now `~/.tracekin` for every surface; the first write path adopts `~/.codex/tracekin` automatically.
- Events carry a per-harness `source`.

## 0.4.6 · 2026-09-16

- Control commands tolerate backticks, quotes, bold markers, punctuation and letter case, and are accepted as the first line of a longer message. Previously `` `tracekin` off `` was treated as an ordinary prompt and uploaded.

## 0.4.5 · 2026-09-16

- An event the receiver rejects (400 / 413 / 415 / 422) no longer blocks the queue; it is skipped. 401 / 403 is shown as "authorization refused" and keeps retrying with backoff.
- The send timeout grew from 2 s to 10 s, and the database write lock is no longer held during the request.
- The dashboard shows the last delivery error and the number of skipped events.

## 0.4.4 · 2026-09-16

- Fixed hooks and MCP reading different data directories: Codex injects `PLUGIN_DATA` into hook commands only, and the old version chose the directory from it, so status stayed at `binding_project` forever. Every surface now uses the same directory, status reports `data_dir`, and the companion left in the old directory is stopped after the upgrade.

## 0.4.3 · 2026-09-16

- Enabled-by-default became the single policy: a new database is created enabled; a legacy database that is pending, missing the field or merely false is migrated to enabled on the next SessionStart; an explicit `denied` is kept.
- `tracekin off` writes only a session override, never the global consent.
- `tracekin status` and the MCP status became pure reads that also answer on read-only files and legacy databases with missing tables.

## 0.4.2 and earlier · 2026-09-15 to 09-16

- 0.4.2: sync on by default after install (first version); 0.4.1: consent and project-binding improvements, read-only status fix; 0.4.0 / 0.3.x: per-session control commands, project scope and receiver edge-case fixes; 0.1.0: first Codex plugin marketplace release.
