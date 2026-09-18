# Tracekin plugin technical reference

English | [简体中文](README.zh-CN.md)

This is the long-form companion to the [repository README](../../README.md): runtime details, the default policy, delivery, per-harness differences and troubleshooting. Release history is in the [CHANGELOG](../../CHANGELOG.md); the cross-machine acceptance walkthrough is in [REMOTE-TEST](../../REMOTE-TEST.md).

## Four surfaces

| Surface | Files | Responsibility |
|------|------|------|
| Hooks | `hooks/hooks.json` (Codex, Claude Code), `cursor/hooks.json` (Cursor), the repository-root `hooks/hooks.json` (Gemini CLI extension), `opencode/tracekin.js` (OpenCode plugin) | `SessionStart` binds the project and starts the companion; `UserPromptSubmit` / `PostToolUse` / `Stop` capture events, with control commands handled before capture |
| MCP | `codex/mcp.json`, `.mcp.json`, `cursor/mcp.json`, the root `gemini-extension.json` → `scripts/mcp_server.py` | `tracekin_status` and `tracekin_data_contract` are read-only; `tracekin_allow` repairs / re-enables; `tracekin_deny` is the global emergency revoke |
| Companion | `scripts/serve.py` | Delivery worker, the `127.0.0.1` dashboard, and the loopback receiver in demo mode |
| Skill | `skills/tracekin/SKILL.md` | Tells the model how to respond to `tracekin off / on / status` and status queries |

The Codex-only "native pet" switch on the dashboard only affects Tracekin's binding choice; it never changes the Codex pet and has nothing to do with data sharing.

## Install and upgrade

### Codex

```bash
codex plugin marketplace add https://github.com/0xblackbox/tracekin-remote-test.git
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

Upgrading means reinstalling so the plugin cache is refreshed, then restarting Codex Desktop and reviewing the hooks again on the Hooks page:

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
```

### Claude Code

```bash
claude plugin marketplace add 0xblackbox/tracekin-remote-test
claude plugin install tracekin@tracekin-remote-test
```

A local checkout works with `claude plugin marketplace add /path/to/checkout`, or load it for a single session with `claude --plugin-dir /path/to/checkout/plugins/tracekin`. The model sees the status tool as `mcp__plugin_tracekin_tracekin__tracekin_status`.

### Cursor

Cursor has no marketplace for this plugin. Both options share one checkout:

**A. User-level hooks (covered by the test suite)**

```bash
git clone https://github.com/0xblackbox/tracekin-remote-test.git ~/tracekin-remote-test
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-cursor
```

The installer writes two argument-free wrapper scripts under `~/.cursor/tracekin/`, merges them into `~/.cursor/hooks.json` (`sessionStart`, `beforeSubmitPrompt`, `postToolUse`, `stop`) and `~/.cursor/mcp.json`, and keeps every other hook and server. Re-running it is idempotent; `uninstall-cursor` removes only its own entries. The installer pins the Python interpreter it was run with.

**B. Plugin directory (built from the Cursor plugin reference, not yet exercised in a live Cursor)**

```bash
mkdir -p ~/.cursor/plugins/local
ln -s ~/tracekin-remote-test/plugins/tracekin ~/.cursor/plugins/local/tracekin
```

Then run "Developer: Reload Window". If Cursor does not pick it up, use A.

### Gemini CLI

The repository root is a Gemini CLI extension (`gemini-extension.json` plus `hooks/hooks.json`; commands use `${extensionPath}` to reach the wrapper scripts inside the repository):

```bash
gemini extensions install https://github.com/0xblackbox/tracekin-remote-test
```

It takes effect after restarting the CLI. Upgrade with `gemini extensions update tracekin`; for local development use `gemini extensions link /path/to/checkout`. The alternative writes the user settings directly:

```bash
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-gemini
```

It merges argument-free wrapper scripts into the `hooks` section of `~/.gemini/settings.json` (`SessionStart`, `BeforeAgent`, `AfterTool`, `AfterAgent`, timeouts in milliseconds) and into `mcpServers`, keeping every other entry; `uninstall-gemini` removes only its own. If `settings.json` cannot be parsed as JSON (for example because it contains comments), the installer stops instead of overwriting it.

### OpenCode

OpenCode has no stdin-JSON hook commands; its plugins are ESM modules run by Bun. `opencode/tracekin.js` translates OpenCode's events into the same payloads the other harnesses send and hands them to `tracekin.py`, with no npm dependencies:

| OpenCode event | Tracekin event |
|------|------|
| `event: session.created` (subagent sessions with a `parentID` are skipped) | SessionStart, project from `info.directory` |
| `chat.message` (before the model sees the prompt) | UserPromptSubmit, prompt from the non-synthetic text `parts` |
| `tool.execute.after` | PostToolUse, `tool` / `callID` / `args` / `output` |
| `event: session.idle` | Stop, assistant reply from the latest assistant text part of that session |

Subagents run in child sessions with their own `sessionID`. The plugin learns the parent of every session from `session.created` / `session.updated` (and asks the SDK once for a session it first sees mid-life) and reports a subagent's tool calls under the **root** session and its current turn, so `tracekin off` typed in the session you are in also covers everything its subagents do. The task text the model writes for a subagent is not a user prompt and is never uploaded or parsed as a command, and a subagent going idle does not produce a Stop event.

Install:

```bash
git clone https://github.com/0xblackbox/tracekin-remote-test.git ~/tracekin-remote-test
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-opencode
```

The installer writes a one-line loader at `~/.config/opencode/plugins/tracekin.js` (it re-exports the plugin from the checkout, so `git pull` upgrades it) and merges the `tracekin` MCP server into the `mcp` section of `~/.config/opencode/opencode.json`, keeping every other key; `uninstall-opencode` removes only its own. OpenCode allows JSONC there, but the installer only handles plain JSON: on a parse failure it stops and says so, and you can add the loader and the MCP entry by hand. `TRACEKIN_PYTHON` selects the Python interpreter the plugin invokes.

## Runtime details

### SessionStart binding

`tracekin.py start` reads `cwd` (Codex, Claude Code) or `workspace_roots[0]` (Cursor) from stdin, falling back to `CURSOR_PROJECT_DIR` / `CLAUDE_PROJECT_DIR` / the process directory. The directory is appended to `projects` and becomes `active_project`; the root and home directories are refused, but the companion still runs with the project already recorded. SessionStart's output is diagnostic JSON (`tracekin`, `project`, `data_dir`, `error`) under Codex and Claude Code, and `{}` under Cursor and Gemini CLI.

### Data directory

Every surface uses `~/.tracekin` (`TRACEKIN_HOME` overrides it explicitly, for tests and troubleshooting only). Harness-provided `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA` / `CODEX_HOME` never select the directory, because they are injected into hook commands only and not into the MCP server. The first write path after an upgrade adopts the old `~/.codex/tracekin` as a whole (including the pending queue), renames the old file to `tracekin.sqlite3.migrated`, and stops the companion recorded in the old directory.

### Default policy and migration

| Situation | Result |
|------|------|
| Fresh install | `consent_granted=true`, `consent_decision=allowed`, `sharing_enabled=true`, `share_all=true`, `endpoint` fixed to Tracekin Cloud |
| Legacy database with `consent_decision=pending`, without that field, or with only `consent_granted=false` | Migrated to the enabled default on the next write path |
| Legacy database with an explicit `consent_decision=denied` | Kept; SessionStart only binds the project and never overrides it; only `tracekin_allow` restores it |
| `tracekin off` / `on` | Writes `session_overrides` only, never the global fields |
| `tracekin status` / MCP status | Pure read: opened with `mode=ro`, no table creation, migration or pruning; a missing table reports a count of 0 and is listed in `missing_tables` |

### Control command recognition

Before comparison, backticks, quotes, bold markers, brackets and similar decorations are stripped along with trailing punctuation, case is ignored, and if the whole message does not match, the first line is tried. A message recognized as a command is withheld entirely; pausing too eagerly beats leaking a prompt. Questions such as `how does tracekin off work?` are not misread as commands.

The turn a command opens is withheld as a whole. `tracekin status` and `tracekin on` leave the session sharing, so the turn is recorded in the `control_turns` table (hashed session and turn ids) and every later event of that turn, such as the status tool call and the model's confirmation, returns `control_turn` instead of being queued. Harnesses without a turn id give the control prompt its own counted turn. The hook write path creates the auxiliary tables on demand, so a database from an older version is protected by its first hook call rather than its next `SessionStart`.

### Delivery

The companion's worker picks the oldest pending event every 0.5 s, holds no database write lock during the request, and times out after 10 s. Outcomes:

| Receiver response | Handling |
|------|------|
| 200 with the id in `accepted` | Marked sent |
| 400 / 413 / 415 / 422 | The receiver rejected this event; it is dropped and skipped, and the dashboard counts it as skipped |
| 401 / 403 | Kept and retried with backoff; the dashboard reports that the receiver refused authorization |
| Any other status, network error or timeout | Kept and retried with backoff, up to 30 s |

At most 1000 events are kept locally for 7 days; production mode sends no token and the endpoint is fixed.

## Status structure

`tracekin.py status` and the MCP `tracekin_status` tool return the same JSON:

| Field | Meaning |
|------|------|
| `config` | The configuration without the salt and token: consent, sharing, project list, active project, endpoint, pet preferences |
| `data_dir` / `legacy_data_dir` | The directory actually read; the latter is set while an old directory is waiting to be adopted |
| `counts` / `events` | Pending and acknowledged counts, and the 12 most recent events |
| `current_project_authorized` | `active_project` is in `projects` and sharing is on |
| `sharing_state` / `hint` | `awaiting_session_start` · `binding_project` · `enabled` · `denied`, plus the next step |
| `session_controls.paused_sessions` | Number of paused sessions |
| `initialized` / `missing_tables` / `migration_pending` | Schema and migration diagnostics |
| `version` / `schema` / `default_policy` / `proof_status` | Metadata |

## Event contract

`schema: tracekin.activity.v1`, one event per POST: `{"schema": ..., "events": [event]}` with the `Content-Type` and `Idempotency-Key` headers (the latter equals the event id).

| Field | Description |
|------|------|
| `id` `project_id` `session_id` `turn_id` | HMAC-SHA256 keyed with the device's random salt |
| `event` | `UserPromptSubmit` / `PostToolUse` / `Stop` |
| `observed_at` `source` `synthetic` `privacy_mode` | Timestamp, `<harness>_hook`, whether it is a demo event, `full_task` or `activity_only` |
| `tool_category` | PostToolUse only: `shell` `edit` `read` `web` `agent` `other` |
| `task_data` | Plain text in `full_task` mode: `prompt`, `tool_input`, `tool_response`, `last_assistant_message` (each only when present) |

## Per-harness differences

| | Codex | Claude Code | Cursor | Gemini CLI | OpenCode |
|------|------|------|------|------|------|
| Mechanism | stdin-JSON hook commands | Same as Codex | Same as Codex | Same as Codex | A JS plugin subscribes to the event bus and calls the same script |
| Event names | SessionStart / UserPromptSubmit / PostToolUse / Stop | Same as Codex | sessionStart / beforeSubmitPrompt / postToolUse / stop | SessionStart / BeforeAgent / AfterTool / AfterAgent | session.created / chat.message / tool.execute.after / session.idle |
| Session / turn ids | `session_id` / `turn_id` | `session_id` / `prompt_id` | `conversation_id` / `generation_id` | `session_id` / none, counted per session locally | `sessionID` / none, counted per session locally |
| Tool output field | `tool_response` | `tool_response` (observed on 2.1.273) or `tool_output` (docs) | `tool_output` | `tool_response` (an object) | `output.output` |
| Assistant reply on Stop | Yes | Yes (observed) | No, `task_data` is empty | Yes, from `prompt_response` | Yes, from the latest assistant text part |
| Project source | `cwd` | `cwd` | `workspace_roots[0]`; user-level hooks run from `~/.cursor` | `cwd` | `session.directory`, falling back to the plugin's `directory` |
| Hook variables | `${PLUGIN_ROOT}`, also exports `CLAUDE_PLUGIN_ROOT` | `${CLAUDE_PLUGIN_ROOT}` | None; wrapper scripts locate themselves | `${extensionPath}`, timeouts in milliseconds | None; the plugin locates the script from its own path |
| Hook output | Ignored | JSON or `{}` | `beforeSubmitPrompt` must answer `{"continue": true}` | `{}`, and nothing but JSON | Not applicable |
| Install | marketplace | marketplace / `--plugin-dir` | `install-cursor` or `~/.cursor/plugins/local` | `gemini extensions install <repo>` or `install-gemini` | `install-opencode` |

`tracekin.py hook --harness auto` detects the dialect from the payload's fields; `--harness codex|claude-code|cursor|gemini|opencode` forces one. For harnesses without a turn id, `Store.record` assigns turns per session: a new prompt opens turn N+1 and tool and stop events join the current turn; a legacy database without the `session_turns` table falls back to a unique id per event.

## Troubleshooting

| Symptom | Cause and fix |
|------|------|
| `sharing_state: awaiting_session_start` | No database yet: hooks not trusted, no restart, or no new session started from a project directory |
| `binding_project` with `migration_pending: true` | 0.4.3 and earlier let hooks and MCP use different directories; upgrade, start a new session, and compare `data_dir` in the SessionStart output with the one in status |
| `denied` | `tracekin_deny` was called, or 0.4.1's "clear local records" wrote denied; call `tracekin_allow` |
| `tracekin off` was uploaded | 0.4.5 and earlier required an exact match; upgrade |
| Dashboard shows a delivery failure | Read "last error": `HTTP 401 unauthorized` means the receiver wants a token while production sends none, so remove `TRACEKIN_TOKEN` on the server; `HTTP 413` one event was too large and was skipped; `URLError` / `TimeoutError` no network |
| Dashboard does not open | The process recorded in `runtime.json` is gone; start a new session or run `tracekin.py start` by hand |
| Companion does not come up | Read `~/.tracekin/companion.log`: every start is logged there together with the companion's own output and errors |
| The local dashboard shows no data from other machines | Expected; the dashboard only shows this machine's queue and receipts |
| Codex reports `sharing_enabled=false` | Check the loaded version with `codex plugin list --json` first; an old build in the plugin cache keeps its old defaults |

Hook field trace: after `touch ~/.tracekin/debug-hooks`, every hook invocation appends one line to `~/.tracekin/hook-debug.log` containing only the event type, the detected harness, the field names and the record outcome, never any content; delete the marker file to stop.

## Development

- Unit tests: `python3 scripts/test_tracekin.py` (standard library only; it really starts and stops companion processes)
- Integration smoke: `python3 scripts/integration_smoke.py` (demo mode with a loopback receiver)
- Syntax: `python3 -m py_compile scripts/*.py`
- Manifests: `claude plugin validate --strict .` inside the plugin directory, and `claude plugin validate .` at the repository root
- Version: change `PLUGIN_VERSION`, the three `plugin.json` files under `.codex-plugin` / `.claude-plugin` / `.cursor-plugin`, the root `.claude-plugin/marketplace.json` and the root `gemini-extension.json`; the tests check that they agree

This plugin only proves the activity-and-consent transport path. It does not claim that activity metadata is training material, that a task is of any quality, or that any reward is owed.
