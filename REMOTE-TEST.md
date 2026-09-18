# Cross-machine acceptance walkthrough

English | [简体中文](REMOTE-TEST.zh-CN.md)

Install the same plugin on another machine and verify three things: the hooks fire on `UserPromptSubmit` / `PostToolUse` / `Stop` in a new session; the MCP server returns the read-only status and the data contract; events reach Tracekin Cloud. Each machine has its own data directory and dashboard, so the remote machine's events are visible on the remote dashboard; its "acknowledged by the receiver" count is the source of truth.

## 1. Install

Check that `python3 --version` works, then install for your harness (commands in the [README](README.md#quick-start)). Codex needs a restart of Codex Desktop, a check in Plugins Directory that the plugin is enabled, and a review-and-trust pass over the bundled hooks on the Hooks page; Claude Code trusts marketplace plugins on install; Cursor needs a restart after `install-cursor`; Gemini CLI needs a restart after `gemini extensions install <repo url>`; OpenCode needs a restart after `install-opencode`.

Upgrade first if an older version is installed: Codex uses `marketplace upgrade` → `remove` → `add`, Claude Code uses `claude plugin update`, Cursor and OpenCode use `git pull` in the checkout, Gemini CLI uses `gemini extensions update tracekin`. The version should read `0.8.3+build.<timestamp>`.

## 2. Start a new session

Start a **new session from the target project directory**; `SessionStart` binds the project, migrates old data and starts the companion. Do not continue in an old session. Find the dashboard address:

```bash
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.tracekin/runtime.json")))["url"])'
```

The dashboard should show the current project and the fixed Tracekin Cloud endpoint. There is no path, endpoint or token to enter and no consent button to press.

## 3. Hooks smoke test

In the same session send, one after the other:

```text
Tracekin remote smoke test: hooks
```

```text
Run `printf TRACEKIN_REMOTE_TOOL_OK` and return the output verbatim.
```

Expected: the dashboard's pending count returns to 0, "acknowledged by the receiver" grows by at least 3 (the prompt, the tool call and the end of the turn), and the event cards are not synthetic.

## 4. MCP check

In the same session send:

```text
Call the Tracekin MCP tools to read the current status and the data contract. Read-only; do not send new data.
```

Expected: `schema: tracekin.activity.v1`, `sharing_state: enabled`, `current_project_authorized: true`, `data_dir: ~/.tracekin`, `proof_status: activity_only_not_training_proof`. You can then call `tracekin_deny` and `tracekin_allow` in turn to verify the global revoke and restore.

## 5. Session pause

Start a new session, send `tracekin off` as the first message (backticks are fine), then send an ordinary prompt. Expected: that session's acknowledged count stops growing and "paused sessions" reads 1; `tracekin on` resumes it. Other new sessions keep sharing by default.

## 6. Check without the model

```bash
python3 <plugin directory>/scripts/tracekin.py status
```

The plugin directory is `~/.codex/plugins/cache/<marketplace>/tracekin/<version>/` for Codex, `~/.claude/plugins/cache/...` for Claude Code, `~/.gemini/extensions/tracekin/plugins/tracekin/` for Gemini CLI, and the checkout itself for Cursor and OpenCode. This is a pure read, equivalent to the MCP `tracekin_status` tool.

## 7. Troubleshooting

See the [troubleshooting table in plugins/tracekin/README.md](plugins/tracekin/README.md#troubleshooting). To see which fields a hook actually received: `touch ~/.tracekin/debug-hooks`, then read `~/.tracekin/hook-debug.log`; it records field names only, never content.
