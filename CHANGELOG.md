# Changelog

版本格式 `X.Y.Z+build.<时间戳>`（0.4.x 为 `+codex.<时间戳>`），时间戳只用于让插件缓存识别新版本。

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
