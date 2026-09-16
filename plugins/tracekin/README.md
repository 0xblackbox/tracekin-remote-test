# Tracekin Codex client MVP

Tracekin is a **local-first Codex plugin**, not a Chrome content detector. It keeps the selected animated pet in Codex and adds automatic current-project binding, install-default syncing, a delivery dashboard, and deterministic controls that pause only the current session.

## Run from this checkout

```bash
cd /Users/bing/Projects/brainstrom/tracekin
python3 scripts/serve.py --demo --home /tmp/tracekin-demo --project /Users/bing/Projects/brainstrom
```

Open the printed loopback URL. The demo receiver is disposable and local-only. For production testing omit `--demo`; the current project and fixed Tracekin Cloud HTTPS destination are filled automatically. Review/trust the bundled hooks before enabling them.

## Native pet

The panel defaults to **沿用当前 Codex 宠物**: turn on that switch to keep using the pet already selected in Codex without creating another one. The switch only controls Tracekin’s binding choice; it does not alter the native pet. Turn it off when you want the panel to save a name and visual description, generate a prompt, and open Codex **Settings → Pets → Create pet**. This is deliberately separate from data sharing: pet animation and task status are not proof of work.

## MCP + Hooks 架构

Tracekin 可以作为 Codex 插件直接安装，并内置 MCP 服务：`tracekin_status`/`tracekin_data_contract` 只读查询，`tracekin_allow` 是兼容性修复入口，`tracekin_deny` 是全局紧急撤销入口。真正的采集及 `tracekin off/on/status` 控制由生命周期 Hooks 完成，因此不依赖模型是否主动调用工具。

## 无感体验

首次打开正式面板会显示当前项目和固定平台，安装后自动开启默认同步；新会话自动共享，敏感任务开始前把 `tracekin off` 作为该会话第一条消息。该命令本身不会上传，且只暂停当前会话。使用 `tracekin on` 恢复，使用 `tracekin status` 查询。`tracekin_deny` 仅用于全局紧急撤销。

## What this MVP proves

- install-default sharing with automatic binding for projects seen by SessionStart;
- hooks fail closed and do not read stdin/transcripts while off;
- payload redaction, HMAC pseudonymous IDs, deduplication, bounded queue and visible receipts;
- per-session off/on/status commands are applied before capture and never uploaded;
- an explicit global revoke remains available for emergency shutdown, while normal privacy control is per-session `tracekin off`.

## Upgrade from the Git marketplace

Quit Codex Desktop, refresh the marketplace, then reinstall the plugin so the new version is copied into the plugin cache:

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

Version `0.5.0` runs under **Claude Code** as well as Codex. One plugin directory carries both manifests (`.codex-plugin/plugin.json` and `.claude-plugin/plugin.json`), one shared `hooks/hooks.json` (commands use `${CLAUDE_PLUGIN_ROOT}`, which Codex also exports), and one MCP config per harness (`codex/mcp.json`, `.mcp.json`). The hook script normalizes Claude Code payloads (`prompt_id` → turn id, `tool_output` → tool response, Stop without an assistant message) and labels events `source: claude_code_hook`. The data directory is now harness-neutral: `~/.tracekin` on every surface, adopted automatically from `~/.codex/tracekin` by the first `SessionStart` after the upgrade (the old file is renamed `tracekin.sqlite3.migrated` and the old companion is stopped). Version strings use `+build.<stamp>` from now on.

Version `0.4.6` makes the session controls tolerant of client formatting. `tracekin off` / `on` / `status` are recognized when the client wraps them in backticks or quotes, adds bold markers or trailing punctuation, changes case, or when the command is the first line of a longer prompt. Previously a prompt such as `` `tracekin` off `` was treated as ordinary text and uploaded.

Version `0.4.5` hardens delivery. The receiver rejecting one event (HTTP 400/413/415/422) no longer blocks every event behind it: that event is dropped and the dashboard shows how many were skipped. HTTP 401/403 is reported as `接收服务拒绝授权` and keeps retrying with backoff, the send timeout is 10 s to survive Cloud Run cold starts, and the database write lock is no longer held during the network call, so hooks keep recording while a slow receiver is contacted. The panel shows the last delivery error next to the connection state.

Version `0.4.4` fixes the data-directory split between hooks and the MCP server: Codex injects `PLUGIN_DATA` into hook commands only, never into a plugin's `.mcp.json` server, so earlier versions let `SessionStart` write to `~/.codex/plugins/data/tracekin-<marketplace>/tracekin/` while `tracekin_status` read `~/.codex/tracekin/` and stayed at `binding_project` forever. Every surface now resolves `$CODEX_HOME/tracekin` (default `~/.codex/tracekin`), status reports `data_dir`, the `SessionStart` output includes `data_dir` plus any error instead of failing silently, and the first `SessionStart` after the upgrade stops a companion that an older hook left running under the `PLUGIN_DATA` directory.

Version `0.4.3` makes the install default the single source of truth: a fresh database is created enabled, every legacy database that is not an explicit global deny is migrated to enabled on the next `SessionStart`, `tracekin off` only writes a per-session override, and `tracekin_status` is a pure read (no `CREATE TABLE`, migration, or prune) that also works on read-only files and legacy schemas.

## 默认共享状态与迁移规则（0.4.3）

| 场景 | 结果 |
|------|------|
| 全新安装（首次创建数据库） | `consent_granted=true`、`consent_decision=allowed`、`sharing_enabled=true`、`share_all=true`，`endpoint` 固定为 Tracekin Cloud |
| 首次 `SessionStart` | 自动把当前项目写入 `projects` 并设为 `active_project`，`current_project_authorized` 变为 `true` |
| 旧数据库 `consent_decision=pending`，或没有 `consent_decision`，或只是 `consent_granted=false` | 下一次 `SessionStart`（或任何写路径）迁移为默认开启 |
| 旧数据库明确记录 `consent_decision=denied`（由 `tracekin_deny` 写入） | 保留拒绝状态；`SessionStart` 只绑定项目，不会覆盖拒绝；只有 `tracekin_allow` 能恢复 |
| `tracekin off` / `tracekin on` | 只写 `session_overrides` 表，不改全局 `consent_granted`；其他会话继续默认共享 |
| `tracekin status` / MCP `tracekin_status` | 纯读：不建表、不迁移、不 prune；数据库只读或缺少 `session_overrides` 表时仍返回 `counts=0`、`paused_sessions=0` |

数据目录：hooks、本地面板和 MCP 服务在 Codex 与 Claude Code 下统一使用 `~/.tracekin`，一台机器一份授权、一个队列、一个面板；`TRACEKIN_HOME` 仅用于测试和手动排查的显式覆盖，`PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA` / `CODEX_HOME` 都不参与目录选择。旧的 `~/.codex/tracekin` 会在升级后第一次 `SessionStart` 时被整体采用（含待发送队列）。状态响应中的 `data_dir` 是实际读取的目录，`legacy_data_dir` 非空表示还有旧目录等待采用。

状态响应中的 `sharing_state` 取值：`awaiting_session_start`（尚无数据库）、`binding_project`（已开启但未绑定当前项目）、`enabled`（当前项目已授权）、`denied`（显式全局拒绝）；`migration_pending=true` 表示数据库仍是旧版本状态，下一次 `SessionStart` 会迁移；`hint` 给出对应的下一步操作。

It does **not** claim that activity metadata is a useful training corpus, that a task is high quality, or that a token is owed. A later data product needs a separately consented human-reviewed sample lane and a published reward formula.

## Install into Claude Code

The same checkout is a Claude Code marketplace (`.claude-plugin/marketplace.json`):

```bash
claude plugin marketplace add 0xblackbox/tracekin-remote-test
claude plugin install tracekin@tracekin-remote-test
```

For a local checkout use `claude plugin marketplace add /path/to/tracekin-remote-test`, or load it for one session without installing: `claude --plugin-dir /path/to/tracekin-remote-test/plugins/tracekin`. Validate before publishing with `claude plugin validate plugins/tracekin` and `claude plugin validate .`.

Differences from Codex: Claude Code's `Stop` hook carries no assistant message, so `task_data` is empty for stop events (the transcript file is never read); the per-turn id comes from `prompt_id`; tool categories cover Claude Code's built-in tools (`Read`/`Glob`/`Grep` → `read`, `Edit`/`Write`/`MultiEdit`/`NotebookEdit` → `edit`, `WebFetch`/`WebSearch` → `web`, `Task`/`Agent` → `agent`). Plugin hooks do not fire in `claude -p` print mode. The status tool appears as `mcp__plugin_tracekin_tracekin__tracekin_status`.

## 跨电脑完整 Hooks/MCP 测试

### 目标

在另一台电脑安装同一个 Tracekin 插件，验证三件事：

1. Codex Hooks 能在新会话的 `UserPromptSubmit`、`PostToolUse`、`Stop` 生命周期触发；
2. Tracekin MCP 能返回只读状态与数据契约；
3. 事件能通过现有 HTTPS 接收地址送到 GCP Cloud Run。

每台电脑的 Tracekin 面板和本地数据目录彼此独立；远程电脑的事件在远程面板里查看。测试以远程面板的“接收服务已确认”计数，以及下面的 MCP/Hook 检查为准。

### 1. 打包和传输

把 `tracekin-remote-test-bundle.zip` 传到另一台电脑并解压。正式版接收地址由插件固定管理，不需要单独传输令牌。

### 2. 在另一台电脑安装插件

先确认 Python 3 可用：

```bash
python3 --version
```

在 Codex CLI 中添加 Git marketplace：

```bash
codex plugin marketplace add https://github.com/0xblackbox/tracekin-remote-test.git
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

如果使用离线压缩包，把第一条替换为解压目录路径即可。

然后重启 Codex Desktop。在 Plugins Directory 中确认 Tracekin 已启用；打开 Hooks 审核页，逐条审阅并信任 Tracekin 的 bundled hooks，完成一次 hooks 信任确认。

### 3. 配置另一台电脑的本地面板

开启一个**新会话**，让 `SessionStart` hook 自动启动 Tracekin 本地面板。如果没有自动打开，先重启 Codex 并检查插件是否启用、hooks 是否已信任；手动启动只用于排查，生产测试使用与 hooks 相同的数据目录和非 demo 模式。

在该电脑的正式面板中确认当前项目和 Tracekin Cloud 固定地址即可。Tracekin 会从 `SessionStart` 自动识别项目并启用默认同步，不需要填写路径、接收地址或令牌，也不需要再点击授权。`tracekin off` 只暂停当前会话；`tracekin_deny` 可用于全局紧急撤销，已经确认的远端数据不受本地清除影响。

### 4. Hooks 冒烟测试

在同一个新会话中依次发送：

```text
Tracekin remote smoke test: hooks
```

然后请求 Codex 执行一个无副作用的工具动作，例如：

```text
请执行 `printf TRACEKIN_REMOTE_TOOL_OK`，并把输出原样返回。
```

预期：面板的“待发送”最终回到 0，“接收服务已确认”至少增加 3（分别对应提示、工具调用、会话停止事件；具体数量随客户端生命周期事件而异），并能看到非 synthetic 的活动卡片。

### 5. MCP 检查

在同一新会话中发送：

```text
请调用 Tracekin MCP，查询当前状态和数据契约；只读查询，不发送新数据。
```

预期 MCP 返回 `schema: tracekin.activity.v1`、当前 `sharing_enabled`/当前项目和 `proof_status: activity_only_not_training_proof`。随后可以分别请求调用 `tracekin_deny` 和 `tracekin_allow` 验证两条授权路径；真正的后台采集由 Hooks 完成。

### 6. 当前会话暂停检查

新建会话并把 `tracekin off` 作为第一条消息，再发送一条普通提示。预期该会话的“接收服务已确认”不再增加，Dashboard 的“已暂停会话”增加；发送 `tracekin on` 后恢复。新建的其他会话仍默认共享。

### 7. 排错顺序

- 面板显示“仅本机演示 · 零外发”：你打开的是 demo，关闭它并使用 `SessionStart` 启动的生产面板；
- 没有任何事件：重启 Codex，确认新会话已启用 Tracekin、Hooks 已信任，并确认会话工作目录是目标项目；敏感会话若之前输入过 `tracekin off`，先输入 `tracekin on`；
- 项目不匹配：从目标项目新建会话，让 `SessionStart` 重新识别当前目录；
- `tracekin_status` 显示 `sharing_enabled=false`：先用 `codex plugin list --json` 确认 Codex 实际加载的 Tracekin 版本。插件缓存里的旧版本（例如 `0.1.0`）会继续沿用旧默认值和旧的 MCP 状态路径，必须按上文 remove/add 重装并重启 Codex；升级后从项目目录新建会话，`SessionStart` 会把旧数据库迁移为默认开启。若 `sharing_state` 为 `denied`，说明曾显式调用过 `tracekin_deny`，请调用 `tracekin_allow`；
- hooks 已信任、已重启，新会话仍是 `binding_project` 且提示数据迁移待执行：这是 `0.4.3` 及更早版本的 hook 与 MCP 读写不同目录导致的，升级到 `0.4.4`。可以用 `ls ~/.codex/plugins/data/tracekin-*/tracekin/` 验证旧版 hook 写到了插件数据目录；升级后对比 `tracekin_status` 里的 `data_dir` 与 SessionStart 输出的 `data_dir` 应一致；
- `tracekin off` 被当成普通提示词上传：`0.4.5` 及更早版本要求逐字匹配，客户端加上反引号、引号或标点就不再识别；`0.4.6` 起会先去掉这些装饰并只看第一行，请升级；
- 接收失败：确认使用正式面板而不是 demo，并检查当前版本是否为 `0.5.0`。面板的"最近错误"会给出原因：`HTTP 401 unauthorized` 表示接收服务要求 Bearer token，而正式版插件不发送 token，需要在 Cloud Run 上移除 `TRACEKIN_TOKEN`；`HTTP 413` 等表示单条事件被拒绝并已跳过；`URLError`/`TimeoutError` 表示网络不通；
- 本机面板没有远程电脑数据：这是预期行为；本地面板只显示当前电脑的本地发送队列和回执。
