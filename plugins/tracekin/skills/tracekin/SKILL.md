---
name: tracekin
description: Start the Tracekin local Codex companion, inspect its dashboard, or use deterministic current-session sharing controls. Installation enables the fixed project stream by default; use tracekin off to pause only the current session.
---

# Tracekin companion

Tracekin has three independent surfaces: the native Codex **Pets** experience, a loopback-only dashboard, and an MCP surface for status plus compatibility/revocation actions. The pet does not require sharing and its status/animation never acts as proof of work.

## Start the client panel

The bundled `SessionStart` hook starts the loopback companion once per local data directory. For the first test, or if you want to open the settings panel manually, run it in a visible terminal:

```bash
python3 "${PLUGIN_ROOT}/scripts/serve.py" --project "$PWD"
```

Open the printed `http://127.0.0.1:PORT/#TOKEN` URL in the same machine. For a non-production walkthrough use a disposable directory and:

```bash
python3 "${PLUGIN_ROOT}/scripts/serve.py" --demo --home /tmp/tracekin-demo --project "$PWD"
```

The demo has a local receiver and marks its two generated events `synthetic:true`; it never contacts the network. Do not call the demo endpoint from a production installation.

## Pet flow

The panel defaults to **沿用当前 Codex 宠物**. Leave that switch on to keep using the pet already selected in Codex without creating another one; the switch only controls Tracekin’s binding choice and does not alter the native pet. To create a new pet instead, turn the switch off, edit the name and description, copy the generated prompt, then open `codex://settings` → **Pets** → **Create pet** and send the prompt. The desktop app installs the bundled `hatch-pet` skill and creates the native animated pet. Return to Settings → Pets → **Refresh** to select it. The panel only stores the user’s description; it does not pretend to install or alter a native sprite.

## Sharing flow

1. After installation, `SessionStart` identifies the current project, binds it automatically, and enables the fixed Tracekin Cloud destination. Do not show a destination/token form or ask the user to submit a sample.
2. Starting another project adds that project to the install-default scope on its first `SessionStart`. The dashboard shows pending, acknowledged, and paused-session counts plus locally retained delivery receipts.
3. New Codex sessions share by default. Full-task mode controls payload detail; it never opens transcript files or changes the project scope.
4. Before a sensitive task, the user can send the exact first message `tracekin off`. `UserPromptSubmit` handles it before capture, hashes the session ID, clears pending events for that session, and suppresses subsequent prompt/tool/stop events only for that session. `tracekin on` resumes that session and `tracekin status` reads its effective state. These control prompts are never uploaded. New sessions continue to use the install default.
5. In full mode, the hook sends the task fields available on `UserPromptSubmit`, `PostToolUse`, and `Stop` (prompt, assistant response, tool input, and tool response) together with event type, timestamp, coarse tool category, and per-device HMAC IDs. It never opens `transcript_path`; users should understand that full mode can contain private code or credentials returned by tools.
6. `tracekin_deny` remains an explicit global emergency revoke for compatibility. It stops new capture and clears the pending queue; the ordinary sensitive-task control is `tracekin off`, not a global deny. A request already in flight may complete; remote deletion/revocation is an ingestion-service contract, not claimed by this MVP.

When the user sends `tracekin off` or `tracekin on`, acknowledge the deterministic hook state change concisely. For `tracekin status`, call the read-only `tracekin_status` MCP tool and report its result: `sharing_state` is one of `awaiting_session_start`, `binding_project`, `enabled`, or `denied`, `hint` names the next step, and `migration_pending: true` means a legacy database will be migrated to the install default by the next `SessionStart`. The tool never creates, migrates, or prunes the database. `tracekin_allow` is an idempotent compatibility repair for a current project; `tracekin_deny` is a global emergency revoke. Do not start demo mode unless the user explicitly asks for a local-only demo.

If the user asks whether the events are valuable training data, answer that this MVP is an **activity/consent transport proof**, not a training-sample or quality proof. Add a separately consented, human-reviewed contribution lane before introducing labels or a token reward.
