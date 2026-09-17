# Tracekin companion（技能说明中文版）

> 本文件供人阅读。各 harness 实际加载的是同目录的 `SKILL.md`（英文，带 YAML frontmatter）；两份内容保持一致。

Tracekin 默认把当前项目的活动事件同步到 Tracekin Cloud，用户可以用 `tracekin off` 暂停单个会话。它有四个表面：生命周期 **hooks**（采集与会话指令）、只读 **MCP** 服务（状态、数据契约、allow / deny）、本地 companion 提供的回环 **面板**，以及仅 Codex 有的原生 **宠物** 体验。插件在 Codex 和 Claude Code 里以插件安装，在 Gemini CLI 里以扩展安装（仓库根目录），在 Cursor 里用 `tracekin.py install-cursor`，在 OpenCode 里用 `tracekin.py install-opencode`。所有 harness 共用 `~/.tracekin` 数据目录。

## 会话指令

hooks 在采集之前确定性地处理这些指令；模型不负责实现，只需要确认。

| 用户发送 | 发生什么 | 如何回应 |
|------|------|------|
| `tracekin off` | 暂停本会话：丢弃其待发送事件，后续的提示、工具、结束事件都不再上报。其他会话与全局设置不受影响。 | 简短确认本会话已暂停，`tracekin on` 可恢复。可选用 `tracekin_status` 的 `paused_sessions` 佐证。 |
| `tracekin on` | 恢复本会话。 | 简短确认。 |
| `tracekin status` | 不改变任何状态。 | 调用只读的 `tracekin_status` MCP 工具并汇报结果。 |

反引号、引号、加粗、结尾标点和大小写都会被忽略，指令也可以是长消息的第一行。控制指令本身以及它开启的那一整轮都不上传，所以在这一轮里调用 `tracekin_status` 并回复是安全的。

## 状态工具

`tracekin_status` 是纯读操作：不建表、不迁移、不 prune。汇报这些字段：

- `sharing_state`：`awaiting_session_start`（还没有数据库，从项目目录新建会话）、`binding_project`（共享已开启但当前项目未绑定）、`enabled`（当前项目已授权）、`denied`（显式全局撤销，只有 `tracekin_allow` 能恢复）。
- `hint`：对应状态的下一步。
- `counts`（待发送 / 已发送）、`session_controls.paused_sessions`、`data_dir`、`version`。
- `migration_pending: true` 表示旧版本的数据库会在下一次 `SessionStart` 迁移。

`tracekin_data_contract` 描述一条事件包含什么，不读取任何任务内容。`tracekin_allow` 是对当前项目的幂等修复 / 重新启用。`tracekin_deny` 是全局紧急撤销：停止新采集并清空待发送队列；已在途的请求可能完成，远端删除属于接收服务的契约，本插件不做承诺。只有用户明确要求全局停止时才用 `tracekin_deny`，日常控制是 `tracekin off`。

## 会上传什么

安装后 `SessionStart` 绑定当前项目并启用固定的 Tracekin Cloud 地址；不要展示地址或令牌表单，也不要让用户提交样本。开始另一个项目时，其首次 `SessionStart` 会把它绑定进来。默认的 full-task 模式下，hooks 每条事件会发送提示词、工具输入与输出、助手回复（视 harness 是否提供），以及事件类型、时间戳、粗粒度工具分类和每台设备独立的 HMAC ID。会话记录文件从不打开。要明确告诉用户：全量模式可能包含工具返回的私有代码或凭据，敏感任务前发 `tracekin off` 才是设计好的控制方式。

如果用户问这些事件是否是有价值的训练数据，回答：本插件只是"活动与授权的传输链路证明"，不是训练样本或质量证明；在引入标注或奖励之前，需要另设一条单独授权、人工审核的贡献通道。

## 面板与演示

`SessionStart` hook 每个数据目录只拉起一个回环 companion。面板地址是随机的，从 `~/.tracekin/runtime.json` 读取：

```bash
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.tracekin/runtime.json")))["url"])'
```

companion 没在运行时，从项目目录新建会话会重新拉起它，也可以在插件目录手动启动：

```bash
python3 "<插件目录>/scripts/serve.py" --project "$PWD"
```

只在用户明确要求演示时，用不触网的本地演示：

```bash
python3 "<插件目录>/scripts/serve.py" --demo --home /tmp/tracekin-demo --project "$PWD"
```

演示模式运行一个回环接收器，生成的事件带 `synthetic: true`。永远不要让正式安装指向演示地址。

## Codex 宠物流程（仅 Codex）

面板里"沿用当前 Codex 宠物"的开关默认打开：Codex 里已选中的宠物保持不变，开关只记录 Tracekin 的绑定选择。想新建宠物就关掉开关，编辑名字和描述，复制生成的提示词，再打开 `codex://settings` → **Pets** → **Create pet** 发送；桌面端内置的 `hatch-pet` 技能会创建原生动画宠物，Settings → Pets → **Refresh** 即可选中。宠物不依赖共享，它的状态或动画也永远不是工作量证明。
