# Tracekin Codex client MVP

Tracekin is a **local-first Codex plugin**, not a Chrome content detector. It keeps a custom animated pet in Codex and adds a separate loopback panel where the user chooses a concrete project and whether to share narrowly redacted activity metadata.

## Run from this checkout

```bash
cd /Users/bing/Projects/brainstrom/tracekin
python3 scripts/serve.py --demo --home /tmp/tracekin-demo --project /Users/bing/Projects/brainstrom
```

Open the printed loopback URL. The demo receiver is disposable and local-only. For production testing omit `--demo`, set an HTTPS ingestion URL in the panel, and review/trust the bundled hooks with `/hooks` before enabling them. A new or changed project, endpoint, or token always resets the sharing checkbox.

## Native pet

The panel defaults to **沿用当前 Codex 宠物**: turn on that switch to keep using the pet already selected in Codex without creating another one. The switch only controls Tracekin’s binding choice; it does not alter the native pet. Turn it off when you want the panel to save a name and visual description, generate a prompt, and open Codex **Settings → Pets → Create pet**. This is deliberately separate from data sharing: pet animation and task status are not proof of work.

## MCP + Hooks 架构

Tracekin 可以作为 Codex 插件直接安装，并内置一个只读 MCP 服务（查询状态和数据契约）。MCP 负责让 Codex 认识 Tracekin；真正的无感采集由生命周期 Hooks 完成，因为 MCP 工具只有在模型主动调用时才会运行。两者一起安装，用户只在客户端面板点击一次全量共享开关。

## 无感体验

首次打开面板只出现一次「允许此项目 · 后台运行」或「仅本地」。选择后不再填写日报、不再生成候选样本；宠物照常跟随 Codex，Hook 在后台处理允许项目的活动元数据。项目或接收地址变化时才重新请求授权。

## What this MVP proves

- default-off, project-scoped consent inside a Codex-side companion;
- hooks fail closed and do not read stdin/transcripts while off;
- payload redaction, HMAC pseudonymous IDs, deduplication, bounded queue and visible receipts;
- disabling sharing clears pending local events and serializes against a new send.

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

在 Codex CLI 中添加这个本地 marketplace（压缩包内已包含 `.agents/plugins/marketplace.json`；路径替换为解压目录）：

```bash
codex plugin marketplace add /path/to/tracekin-remote-test-bundle
codex plugin add tracekin@tracekin-remote-test
codex plugin list
```

然后重启 Codex Desktop。在 Plugins Directory 中确认 Tracekin 已启用；打开 Hooks 审核页，逐条审阅并信任 Tracekin 的 bundled hooks，完成一次 hooks 信任确认。

### 3. 配置另一台电脑的本地面板

开启一个**新会话**，让 `SessionStart` hook 自动启动 Tracekin 本地面板。如果没有自动打开，先重启 Codex 并检查插件是否启用、hooks 是否已信任；手动启动只用于排查，生产测试使用与 hooks 相同的数据目录和非 demo 模式。

在该电脑的面板中填写：

- 允许的项目目录：另一台电脑上真实存在的测试项目绝对路径（本机 `/Users/bing/...` 路径在另一台电脑不适用）；
- 数据发送到哪里：`https://tracekin-ingest-guhpxpyula-as.a.run.app/ingest`；
- 接收服务令牌：通过独立安全渠道复制本机 `artifacts/tracekin-gcp-receiver/receiver-token` 的内容；压缩包本身不含令牌；
- 点击“保存共享范围”，再开启“共享所有任务数据 / 后台运行”。

保存项目、地址或令牌后，Tracekin 会自动关闭共享并清空待发送队列；保存后要重新打开共享开关。

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

### 6. 关闭共享的反向检查

关闭面板共享开关，再发送一条新提示。预期“接收服务已确认”不再增加，待发送保持 0；重新开启后才恢复发送。

### 7. 排错顺序

- 面板显示“仅本机演示 · 零外发”：你打开的是 demo，关闭它并使用 `SessionStart` 启动的生产面板；
- 没有任何事件：重启 Codex，确认新会话已启用 Tracekin，并在 Hooks 页完成信任；
- 项目不匹配：把项目目录改成另一台电脑上的绝对路径，保存后重新打开共享；
- 401：重新复制令牌，确认地址只有 `https://.../ingest`，没有多余空格、查询串或片段；
- 本机面板没有远程电脑数据：这是预期行为；本地面板只显示当前电脑的本地发送队列和回执。
