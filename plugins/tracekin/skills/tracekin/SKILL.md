---
name: tracekin
description: Start the Tracekin local Codex companion, inspect its dashboard, or use deterministic current-session sharing controls. Do not collect or transmit data until the user completes the one-time opt-in inside the local panel.
---

# Tracekin companion

Tracekin has two independent surfaces: the native Codex **Pets** experience, and a loopback-only settings panel. The pet does not require sharing and its status/animation never acts as proof of work.

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

1. On first use, the panel identifies the current project and offers one explicit choice: **同意并开始共享** or **暂不共享**. This is the only global authorization action; do not show a daily form or ask the user to submit a sample.
2. Advanced settings allow changing concrete project directories and the user’s own HTTPS ingestion endpoint (a bearer token is optional and stays in the local database). Saving or changing a project, destination, or token revokes the previous authorization and clears pending local events.
3. After that one-time opt-in, new Codex sessions share by default within the configured project directories. Full-task mode controls payload detail; it never expands the project boundary. The dashboard shows pending, acknowledged, and paused-session counts plus the locally retained delivery receipts.
4. Before a sensitive task, the user can send the exact first message `tracekin off`. `UserPromptSubmit` handles it before capture, hashes the session ID, clears pending events for that session, and suppresses subsequent prompt/tool/stop events only for that session. `tracekin on` resumes that session and `tracekin status` reads its effective state. These control prompts are never uploaded. New sessions continue to use the global default.
5. In full mode, the hook sends the task fields available on `UserPromptSubmit`, `PostToolUse`, and `Stop` (prompt, assistant response, tool input, and tool response) together with event type, timestamp, coarse tool category, and per-device HMAC IDs. It never opens `transcript_path`; users should understand that full mode can contain private code or credentials returned by tools.
6. Revoking the global authorization stops new capture, clears the pending queue and session overrides, and closes the local transaction before a new send can start. A request already in flight may complete; remote deletion/revocation is an ingestion-service contract, not claimed by this MVP.

When the user sends `tracekin off` or `tracekin on`, acknowledge the deterministic hook state change concisely. For `tracekin status`, call the read-only `tracekin_status` MCP tool and report its result. Do not start demo mode unless the user explicitly asks for a local-only demo.

If the user asks whether the events are valuable training data, answer that this MVP is an **activity/consent transport proof**, not a training-sample or quality proof. Add a separately consented, human-reviewed contribution lane before introducing labels or a token reward.
