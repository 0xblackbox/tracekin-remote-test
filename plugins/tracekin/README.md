# Tracekin Codex client MVP

Tracekin is a **local-first Codex plugin**, not a Chrome content detector. It keeps the selected animated pet in Codex and adds one-time local sharing authorization, a delivery dashboard, and deterministic controls that pause only the current session.

## Run from this checkout

```bash
cd /Users/bing/Projects/brainstrom/tracekin
python3 scripts/serve.py --demo --home /tmp/tracekin-demo --project /Users/bing/Projects/brainstrom
```

Open the printed loopback URL. The demo receiver is disposable and local-only. For production testing omit `--demo`, set an HTTPS ingestion URL in the panel, and review/trust the bundled hooks before enabling them. A new or changed project, endpoint, or token revokes the previous authorization.

## Native pet

The panel defaults to **沿用当前 Codex 宠物**: turn on that switch to keep using the pet already selected in Codex without creating another one. The switch only controls Tracekin’s binding choice; it does not alter the native pet. Turn it off when you want the panel to save a name and visual description, generate a prompt, and open Codex **Settings → Pets → Create pet**. This is deliberately separate from data sharing: pet animation and task status are not proof of work.

## MCP + Hooks 架构

Tracekin 可以作为 Codex 插件直接安装，并内置一个只读 MCP 服务（查询状态、暂停会话数量和数据契约）。MCP 负责让 Codex 认识 Tracekin；真正的采集及 `tracekin off/on/status` 控制由生命周期 Hooks 完成，因此不依赖模型是否主动调用工具。

## 无感体验

首次打开正式面板只出现一次「同意并开始共享」或「暂不共享」。同意后，新会话在已配置项目目录内默认共享；全量模式只改变发送字段，不会扩大项目边界。敏感任务开始前把 `tracekin off` 作为该会话第一条消息；该命令本身不会上传，且只暂停当前会话。使用 `tracekin on` 恢复，使用 `tracekin status` 查询。项目或接收地址变化时才重新请求授权。

## What this MVP proves

- one-time explicit consent inside the loopback companion, followed by project-scoped default sharing for new sessions;
- hooks fail closed and do not read stdin/transcripts while off;
- payload redaction, HMAC pseudonymous IDs, deduplication, bounded queue and visible receipts;
- per-session off/on/status commands are applied before capture and never uploaded;
- revoking global authorization clears pending local events and session overrides.

## Upgrade from the Git marketplace

Quit Codex Desktop, refresh the marketplace, then reinstall the plugin so the new version is copied into the plugin cache:

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

Version `0.2.1+codex.20260916051837` adds the delivery dashboard and current-session controls.

It does **not** claim that activity metadata is a useful training corpus, that a task is high quality, or that a token is owed. A later data product needs a separately consented human-reviewed sample lane and a published reward formula.

## 跨电脑完整 Hooks/MCP 测试

### 目标

在另一台电脑安装同一个 Tracekin 插件，验证三件事：

1. Codex Hooks 能在新会话的 `UserPromptSubmit`、`PostToolUse`、`Stop` 生命周期触发；
2. Tracekin MCP 能返回只读状态与数据契约；
3. 事件能通过现有 HTTPS 接收地址送到 GCP Cloud Run。

每台电脑的 Tracekin 面板和本地数据目录彼此独立；远程电脑的事件在远程面板里查看。测试以远程面板的“接收服务已确认”计数，以及下面的 MCP/Hook 检查为准。

### 1. 打包和传输

把 `tracekin-remote-test-bundle.zip` 传到另一台电脑并解压。压缩包不包含 Bearer token；token 只通过密码管理器或其他独立安全渠道传输。

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

在该电脑的正式面板中填写：

- 允许的项目目录：另一台电脑上真实存在的测试项目绝对路径（本机 `/Users/bing/...` 路径在另一台电脑不适用）；
- 数据发送到哪里：`https://tracekin-ingest-guhpxpyula-as.a.run.app/ingest`；
- 接收服务令牌：通过独立安全渠道复制本机 `artifacts/tracekin-gcp-receiver/receiver-token` 的内容；压缩包本身不含令牌；
- 点击“保存共享范围”，再点击一次“同意并开始共享”。

保存项目、地址或令牌后，Tracekin 会撤回旧授权并清空待发送队列；保存后需要重新完成一次授权。

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

预期 MCP 返回 `schema: tracekin.activity.v1`、当前 `sharing_enabled`/项目范围和 `proof_status: activity_only_not_training_proof`。MCP 查询本身是只读的；真正的后台采集由 Hooks 完成。

### 6. 当前会话暂停检查

新建会话并把 `tracekin off` 作为第一条消息，再发送一条普通提示。预期该会话的“接收服务已确认”不再增加，Dashboard 的“已暂停会话”增加；发送 `tracekin on` 后恢复。新建的其他会话仍默认共享。

### 7. 排错顺序

- 面板显示“仅本机演示 · 零外发”：你打开的是 demo，关闭它并使用 `SessionStart` 启动的生产面板；
- 没有任何事件：重启 Codex，确认新会话已启用 Tracekin、Hooks 已信任，并在正式面板完成一次授权；
- 项目不匹配：把项目目录改成另一台电脑上的绝对路径，保存后重新授权；
- 401：重新复制令牌，确认地址只有 `https://.../ingest`，没有多余空格、查询串或片段；
- 本机面板没有远程电脑数据：这是预期行为；本地面板只显示当前电脑的本地发送队列和回执。
