# 跨电脑完整验收流程

在另一台电脑安装同一个插件，验证三件事：hooks 在新会话的 `UserPromptSubmit` / `PostToolUse` / `Stop` 触发；MCP 能返回只读状态与数据契约；事件能送达 Tracekin Cloud。每台电脑的数据目录与面板彼此独立，远程电脑的事件在远程面板里看，以"接收服务已确认"计数为准。

## 1. 安装

先确认 `python3 --version` 可用，然后按 harness 安装（命令见 [README](README.md#安装)）。Codex 需要重启 Codex Desktop，在 Plugins Directory 确认已启用，并在 Hooks 页面逐条审阅、信任 bundled hooks；Claude Code 通过 marketplace 安装即已信任；Cursor 运行 `install-cursor` 后重启。

已装旧版时先升级：Codex 用 `marketplace upgrade` → `remove` → `add`，Claude Code 用 `claude plugin update`，Cursor 在 checkout 里 `git pull`。版本应显示 `0.6.1+build.<时间戳>`。

## 2. 新建会话

从目标项目目录**新建一个会话**，`SessionStart` 会自动绑定项目、迁移旧数据、拉起 companion。不要在旧会话里继续。查看面板地址：

```bash
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.tracekin/runtime.json")))["url"])'
```

面板应显示当前项目和 Tracekin Cloud 固定地址，不需要填写路径、地址或令牌，也不需要点授权。

## 3. Hooks 冒烟

在同一会话依次发送：

```text
Tracekin remote smoke test: hooks
```

```text
请执行 `printf TRACEKIN_REMOTE_TOOL_OK`，并把输出原样返回。
```

预期：面板"待发送"回到 0，"接收服务已确认"至少增加 3（提示、工具调用、会话结束），事件卡片非 synthetic。

## 4. MCP 检查

在同一会话发送：

```text
请调用 Tracekin MCP，查询当前状态和数据契约；只读查询，不发送新数据。
```

预期返回 `schema: tracekin.activity.v1`、`sharing_state: enabled`、`current_project_authorized: true`、`data_dir: ~/.tracekin`、`proof_status: activity_only_not_training_proof`。可以再分别调用 `tracekin_deny` 与 `tracekin_allow` 验证全局撤销和恢复。

## 5. 会话暂停

新建会话，第一条发 `tracekin off`（带反引号也可以），再发一条普通提示。预期该会话"接收服务已确认"不再增加，"已暂停会话"为 1；发 `tracekin on` 后恢复。其他新会话仍默认共享。

## 6. 不用模型也能查

```bash
python3 <插件目录>/scripts/tracekin.py status
```

插件目录：Codex 在 `~/.codex/plugins/cache/<marketplace>/tracekin/<version>/`，Claude Code 在 `~/.claude/plugins/cache/...`，Cursor 就是 checkout。这条是纯读命令，等价于 MCP `tracekin_status`。

## 7. 排错

见 [plugins/tracekin/README.md 的排错表](plugins/tracekin/README.md#排错)。需要看 hook 实际送了哪些字段时：`touch ~/.tracekin/debug-hooks`，再看 `~/.tracekin/hook-debug.log`，只记字段名不记内容。
