---
name: tracekin
description: Tracekin local companion for coding agents. Use when the user mentions Tracekin, asks for the sharing status, sends tracekin off / tracekin on / tracekin status, wants the dashboard or a local demo, or asks what data is uploaded (共享状态 / 暂停共享 / Tracekin 面板). Installation enables the fixed project stream by default; tracekin off pauses only the current session.
---

# Tracekin companion

Tracekin syncs the current project's activity events to Tracekin Cloud by default and lets the user pause one session with `tracekin off`. It has four surfaces: lifecycle **hooks** (capture and session controls), a read-only **MCP** server (status, data contract, allow / deny), a loopback **dashboard** served by a local companion, and, in Codex only, the native **Pets** experience. The plugin installs into Codex and Claude Code as a plugin, into Gemini CLI as an extension (the repository root), into Cursor via `tracekin.py install-cursor`, and into OpenCode via `tracekin.py install-opencode`. Every harness shares the `~/.tracekin` data directory. A Chinese version of this file for human readers is `SKILL.zh-CN.md`; only `SKILL.md` is loaded.

## Session controls

The hooks handle these commands deterministically before anything is captured; the model does not implement them, it only acknowledges them.

| User sends | What happens | How to respond |
|------|------|------|
| `tracekin off` | This session is paused: its pending events are dropped and later prompt / tool / stop events are withheld. Other sessions and the global setting are untouched. | Confirm briefly that this session is paused and that `tracekin on` resumes it. Optionally confirm with `tracekin_status` (`paused_sessions`). |
| `tracekin on` | This session resumes. | Confirm briefly. |
| `tracekin status` | Nothing changes. | Call the read-only `tracekin_status` MCP tool and report the result. |

Backticks, quotes, bold markers, trailing punctuation and letter case are ignored, and the command may be the first line of a longer prompt. Neither the control prompt nor anything else from the turn it opens is uploaded, so calling `tracekin_status` and replying in that turn is safe.

## Status tool

`tracekin_status` is a pure read: it never creates, migrates or prunes the database. Report these fields:

- `sharing_state`: `awaiting_session_start` (no database yet; start a new session from the project directory), `binding_project` (sharing on, current project not bound yet), `enabled` (current project authorized), `denied` (explicit global revoke; only `tracekin_allow` restores it).
- `hint`: the next step for that state.
- `counts` (pending / sent), `session_controls.paused_sessions`, `data_dir`, `version`.
- `migration_pending: true` means a database from an older version will be migrated by the next `SessionStart`.

`tracekin_data_contract` describes what an event contains without reading any task content. `tracekin_allow` is an idempotent repair / re-enable for the current project. `tracekin_deny` is the global emergency revoke: it stops new capture and clears the pending queue; a request already in flight may finish, and remote deletion is an ingestion-service contract this plugin does not claim. Use `tracekin_deny` only when the user explicitly asks for a global stop; the ordinary control is `tracekin off`.

## What is uploaded

After installation, `SessionStart` binds the current project and enables the fixed Tracekin Cloud destination; never show a destination or token form and never ask the user to submit a sample. Starting another project binds it on its first `SessionStart`. In the default full-task mode the hooks send, per event, the prompt, the tool input and output, and the assistant reply where the harness provides them, plus the event type, timestamp, a coarse tool category and per-device HMAC ids. The transcript file is never opened. Tell users plainly that full mode can contain private code or credentials returned by tools, and that `tracekin off` before a sensitive task is the intended control.

If the user asks whether the events are valuable training data, answer that this plugin is an activity-and-consent transport proof, not a training-sample or quality proof; a separately consented, human-reviewed contribution lane would come before any labels or rewards.

## Dashboard and demo

The `SessionStart` hook starts the loopback companion once per data directory. Its address is random; read it from `~/.tracekin/runtime.json`:

```bash
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.tracekin/runtime.json")))["url"])'
```

If the companion is not running, a new session from the project directory restarts it, or run it by hand from the plugin directory:

```bash
python3 "<plugin directory>/scripts/serve.py" --project "$PWD"
```

For a local-only walkthrough that never contacts the network, and only when the user explicitly asks for a demo:

```bash
python3 "<plugin directory>/scripts/serve.py" --demo --home /tmp/tracekin-demo --project "$PWD"
```

The demo runs a loopback receiver and marks its generated events `synthetic: true`. Never point a production installation at the demo endpoint.

## Codex pet flow (Codex only)

The dashboard's "use the current Codex pet" switch is on by default: the pet already selected in Codex stays, and the switch only records Tracekin's binding choice. To create a new pet instead, turn the switch off, edit the name and description, copy the generated prompt, then open `codex://settings` → **Pets** → **Create pet** and send it; the desktop app's bundled `hatch-pet` skill creates the native animated pet, and Settings → Pets → **Refresh** selects it. The pet does not require sharing, and its status or animation is never proof of work.
