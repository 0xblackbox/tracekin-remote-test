# Changelog

[English](CHANGELOG.md) | 简体中文

版本格式 `X.Y.Z+build.<时间戳>`（0.4.x 为 `+codex.<时间戳>`），时间戳只用于让插件缓存识别新版本。

## 0.8.0 · 2026-09-17

- 新增 OpenCode：`opencode/tracekin.js`（无 npm 依赖的 ESM 插件）订阅 `session.created` / `chat.message` / `tool.execute.after` / `session.idle`，翻译成与其他 harness 相同的 payload 交给 `tracekin.py`；助手回复取自最近的 assistant 文本 part；子代理会话不重复拉起 companion。
- `tracekin.py install-opencode` 在 `~/.config/opencode/plugins/` 写加载器并把 MCP 合并进 `opencode.json`；`uninstall-opencode` 只删自己的。
- 测试用 Node 驱动真实插件文件走完整链路，机器上没有 Node 时跳过。
- 工具分类新增 OpenCode 内置工具名。

## 0.7.0 · 2026-09-17

- 新增 Gemini CLI：`SessionStart` / `BeforeAgent` / `AfterTool` / `AfterAgent` 归一化到共享事件模型，`AfterAgent` 的 `prompt_response` 作为助手回复，`tool_response` 对象原样保留。
- 没有轮次 ID 的 harness 由数据库按会话计数（新表 `session_turns`）：新提示词开启下一轮，工具与结束事件归入当前轮；旧库缺表时退回逐事件唯一 ID。
- 两种安装：仓库根即 Gemini 扩展（`gemini-extension.json` + `hooks/hooks.json`，`gemini extensions install <repo>`）；`tracekin.py install-gemini` 合并进 `~/.gemini/settings.json`（幂等、只删自己）。
- 安装器遇到无法解析的现有配置文件（例如带注释的 JSON）会中止而不是覆盖，Cursor 安装器同样适用。
- 工具分类新增 Gemini 内置工具名。

## 0.6.1 · 2026-09-17

- companion 启动时不再对 `127.0.0.1` 做反向 DNS 查询：在反向解析慢的环境（GitHub macOS runner、部分 VPN）下该查询会让 companion 卡住几十秒、迟迟写不出 `runtime.json`。
- companion 的输出记录到 `~/.tracekin/companion.log`，便于排查"面板起不来"。
- 测试不再让临时 companion 在事件入队期间运行，避免向真实接收服务投递测试数据。
- 文档重组：项目主页 README、按主题组织的技术参考、CHANGELOG、独立的跨电脑验收流程；新增 GitHub Actions 测试工作流与 MIT 许可证。

## 0.6.0 · 2026-09-16

- 新增 Cursor：`sessionStart` / `beforeSubmitPrompt` / `postToolUse` / `stop` 归一化到共享事件模型，`conversation_id` / `generation_id` 作会话与轮次 ID，项目取 `workspace_roots`，`beforeSubmitPrompt` 回 `{"continue": true}`。
- 两种安装：`tracekin.py install-cursor` 合并进 `~/.cursor/hooks.json` 与 `mcp.json`（幂等、只删自己）；`.cursor-plugin/plugin.json` 供 `~/.cursor/plugins/local` 使用。
- 工具分类对未知工具名按关键词兜底。

## 0.5.1 · 2026-09-16

- 从非项目目录（如 `~`）启动会话时 companion 退回已记录的项目，不再退出。
- 可选的字段名排查日志 `~/.tracekin/debug-hooks`。
- Claude Code 2.1.273 实测：`PostToolUse` 发送 `tool_response`，`Stop` 发送 `last_assistant_message`，与文档不同，两种命名均兼容。

## 0.5.0 · 2026-09-16

- 新增 Claude Code：`.claude-plugin/plugin.json`、共用 `hooks/hooks.json`（`${CLAUDE_PLUGIN_ROOT}`）、各 harness 独立 MCP 配置。
- 数据目录统一为 `~/.tracekin`，首次写路径自动采用 `~/.codex/tracekin`。
- 事件 `source` 按 harness 标记。

## 0.4.6 · 2026-09-16

- 控制指令容忍反引号、引号、加粗、标点、大小写，并接受作为长消息第一行；此前 `` `tracekin` off `` 会被当普通提示词上传。

## 0.4.5 · 2026-09-16

- 接收服务拒绝的单条事件（400 / 413 / 415 / 422）不再堵死队列，直接跳过；401 / 403 显示为"拒绝授权"并继续退避重试。
- 发送超时 2 秒改为 10 秒；发送期间不再持有数据库写锁。
- 面板显示最近投递错误和已跳过数量。

## 0.4.4 · 2026-09-16

- 修复 hooks 与 MCP 读写不同数据目录：Codex 只给 hook 注入 `PLUGIN_DATA`，旧版据此选目录导致状态永远 `binding_project`。所有表面改用同一目录，状态返回 `data_dir`，升级后停掉旧目录里的 companion。

## 0.4.3 · 2026-09-16

- 默认开启成为唯一策略：新库创建即开启；旧库 pending / 缺字段 / 仅 false 在下一次 SessionStart 迁移为开启；显式 `denied` 保留。
- `tracekin off` 只写会话覆盖，不改全局授权。
- `tracekin status` 与 MCP 状态改为纯读，只读文件和缺表的旧库也能返回。

## 0.4.2 及更早 · 2026-09-15 至 09-16

- 0.4.2：安装即默认同步（首版）；0.4.1：授权与项目绑定优化、只读状态查询修复；0.4.0 / 0.3.x：单会话控制指令、项目范围与接收器边界修复；0.1.0：首个 Codex 插件市场发布。
