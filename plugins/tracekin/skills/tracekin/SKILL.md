---
name: tracekin
description: Start the Tracekin local Codex companion or help configure its native pet and explicit project-scoped activity sharing. Do not collect or transmit data unless the user has opted in inside the local panel.
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

1. On first use, the panel identifies the current project and offers one explicit choice: **允许此项目 · 后台运行** or **仅本地**. This is the only recurring human action; do not show a daily form or ask the user to submit a sample.
2. Advanced settings allow changing concrete project directories and the user’s own HTTPS ingestion endpoint (a bearer token is optional and stays in the local database). Saving or changing a project, destination, or token automatically turns sharing off and clears pending local events.
3. The one-time choice enables **共享所有任务数据**. The hook then observes `UserPromptSubmit`, `PostToolUse`, and `Stop` across the Codex session instead of requiring a per-task submission.
4. In this explicit full mode, the hook sends the task fields available on those events (prompt, assistant response, tool input, and tool response) together with event type, timestamp, coarse tool category, and per-device HMAC IDs. It never opens `transcript_path`; users should understand that full mode can contain private code or credentials returned by tools.
5. The panel shows pending/sent counts and each redacted payload. Turning sharing off stops new capture, clears the pending queue, and closes the local transaction before a new send can start. A request already in flight may complete; remote deletion/revocation is an ingestion-service contract, not claimed by this MVP.

If the user asks whether the events are valuable training data, answer that this MVP is an **activity/consent transport proof**, not a training-sample or quality proof. Add a separately consented, human-reviewed contribution lane before introducing labels or a token reward.
