# Tracekin 插件技术参考

[English](README.md) | 简体中文

本文是 [仓库 README](../../README.zh-CN.md) 的展开版：运行细节、默认策略、投递、各 harness 差异和排错。版本记录见 [CHANGELOG](../../CHANGELOG.zh-CN.md)，跨电脑验收见 [REMOTE-TEST](../../REMOTE-TEST.zh-CN.md)。

## 四个表面

| 表面 | 文件 | 职责 |
|------|------|------|
| Hooks | `hooks/hooks.json`（Codex、Claude Code）、`cursor/hooks.json`（Cursor）、仓库根 `hooks/hooks.json`（Gemini CLI 扩展）、`opencode/tracekin.js`（OpenCode 插件） | `SessionStart` 绑定项目并拉起 companion；`UserPromptSubmit` / `PostToolUse` / `Stop` 采集，控制指令在采集前处理 |
| MCP | `codex/mcp.json`、`.mcp.json`、`cursor/mcp.json`、根 `gemini-extension.json` → `scripts/mcp_server.py` | `tracekin_status`、`tracekin_data_contract` 只读；`tracekin_allow` 修复 / 重新启用；`tracekin_deny` 全局紧急撤销 |
| companion | `scripts/serve.py` | 投递 worker、`127.0.0.1` 面板、demo 模式的本地接收器 |
| Skill | `skills/tracekin/SKILL.md` | 告诉模型如何响应 `tracekin off / on / status` 和状态查询 |

Codex 专有的"原生宠物"面板开关只影响 Tracekin 的绑定选择，不改动 Codex 宠物，也与数据共享无关。

## 安装与升级

### Codex

```bash
codex plugin marketplace add https://github.com/0xblackbox/tracekin-remote-test.git
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

升级需要重装以刷新插件缓存，然后重启 Codex Desktop 并在 Hooks 页面重新审阅：

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
```

### Claude Code

```bash
claude plugin marketplace add 0xblackbox/tracekin-remote-test
claude plugin install tracekin@tracekin-remote-test
```

本地 checkout 可用 `claude plugin marketplace add /path/to/checkout`，或只在一次会话里加载：`claude --plugin-dir /path/to/checkout/plugins/tracekin`。状态工具在模型侧的名字是 `mcp__plugin_tracekin_tracekin__tracekin_status`。

### Cursor

Cursor 没有面向这个插件的市场，两种方式共用同一个 checkout：

**A. 用户级 hooks（测试套件覆盖）**

```bash
git clone https://github.com/0xblackbox/tracekin-remote-test.git ~/tracekin-remote-test
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-cursor
```

安装器在 `~/.cursor/tracekin/` 写两个无参包装脚本，合并进 `~/.cursor/hooks.json`（`sessionStart`、`beforeSubmitPrompt`、`postToolUse`、`stop`）和 `~/.cursor/mcp.json`，保留其他 hook 与 MCP；重复执行是幂等的；`uninstall-cursor` 只删自己的条目。安装器会固定它自己被运行时的 Python 解释器。

**B. 插件目录（按 Cursor 插件参考实现，尚未在 Cursor 实机验证）**

```bash
mkdir -p ~/.cursor/plugins/local
ln -s ~/tracekin-remote-test/plugins/tracekin ~/.cursor/plugins/local/tracekin
```

之后执行 "Developer: Reload Window"。识别不到就用 A。

### Gemini CLI

仓库根目录就是一个 Gemini CLI 扩展（`gemini-extension.json` + `hooks/hooks.json`，命令用 `${extensionPath}` 定位仓库内的包装脚本）：

```bash
gemini extensions install https://github.com/0xblackbox/tracekin-remote-test
```

重启 CLI 后生效；升级用 `gemini extensions update tracekin`，本地开发用 `gemini extensions link /path/to/checkout`。备选方案是写入用户设置：

```bash
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-gemini
```

它把无参包装脚本合并进 `~/.gemini/settings.json` 的 `hooks`（`SessionStart`、`BeforeAgent`、`AfterTool`、`AfterAgent`，超时单位毫秒）和 `mcpServers`，保留其他条目；`uninstall-gemini` 只删自己的。若 `settings.json` 无法按 JSON 解析（例如带注释），安装器会中止而不是覆盖它。

### OpenCode

OpenCode 没有 stdin JSON 的 hook 命令，插件是 Bun 运行的 ESM 模块。`opencode/tracekin.js` 把 OpenCode 的事件翻译成其他 harness 同样的 payload 再交给 `tracekin.py`，不依赖任何 npm 包：

| OpenCode 事件 | Tracekin 事件 |
|------|------|
| `event: session.created`（跳过带 `parentID` 的子代理会话） | SessionStart，项目取 `info.directory` |
| `chat.message`（在模型看到提示词之前） | UserPromptSubmit，提示词取 `parts` 中非 synthetic 的文本 |
| `tool.execute.after` | PostToolUse，`tool` / `callID` / `args` / `output` |
| `event: session.idle` | Stop，助手回复来自该会话最近一条 assistant 消息的文本 part |

子代理运行在有独立 `sessionID` 的子会话里。插件从 `session.created` / `session.updated` 学到每个会话的父会话（对中途才见到的会话会向 SDK 查询一次），并把子代理的工具调用记到**根会话**及其当前轮次名下，所以你在当前会话里发的 `tracekin off` 同样覆盖它的所有子代理。模型写给子代理的任务文本不是用户提示词，既不上传也不会被当成指令解析；子代理进入空闲也不产生 Stop 事件。

安装：

```bash
git clone https://github.com/0xblackbox/tracekin-remote-test.git ~/tracekin-remote-test
python3 ~/tracekin-remote-test/plugins/tracekin/scripts/tracekin.py install-opencode
```

安装器在 `~/.config/opencode/plugins/tracekin.js` 写一行加载器（re-export checkout 里的插件，`git pull` 即升级），并把 `tracekin` MCP 合并进 `~/.config/opencode/opencode.json` 的 `mcp`，保留其他键；`uninstall-opencode` 只删自己的。OpenCode 的配置允许 JSONC，但安装器只处理纯 JSON：解析失败会中止并提示，你可以手动把加载器和 MCP 条目加进去。可用 `TRACEKIN_PYTHON` 指定插件调用的 Python 解释器。

## 运行细节

### SessionStart 绑定

`tracekin.py start` 读取 stdin 里的 `cwd`（Codex、Claude Code）或 `workspace_roots[0]`（Cursor），退回 `CURSOR_PROJECT_DIR` / `CLAUDE_PROJECT_DIR` / 进程目录。目录会被追加进 `projects` 并设为 `active_project`；根目录和用户主目录会被拒绝，但 companion 仍会以已记录的项目继续运行。SessionStart 的输出在 Codex / Claude Code 下是诊断 JSON（`tracekin`、`project`、`data_dir`、`error`），在 Cursor 和 Gemini CLI 下是 `{}`。

### 数据目录

所有表面统一使用 `~/.tracekin`（`TRACEKIN_HOME` 可显式覆盖，仅用于测试和排查）。harness 注入的 `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA` / `CODEX_HOME` 都不参与目录选择，因为它们只注入 hook 命令，不注入 MCP 服务。升级后第一次写路径会把旧的 `~/.codex/tracekin` 整体采用（含待发送队列），旧文件改名为 `tracekin.sqlite3.migrated`，旧目录里记录的 companion 会被停掉。

### 默认策略与迁移

| 场景 | 结果 |
|------|------|
| 全新安装 | `consent_granted=true`、`consent_decision=allowed`、`sharing_enabled=true`、`share_all=true`，`endpoint` 固定为 Tracekin Cloud |
| 旧库 `consent_decision=pending`、缺少该字段、或仅 `consent_granted=false` | 下一次写路径迁移为默认开启 |
| 旧库明确记录 `consent_decision=denied` | 保留；SessionStart 只绑定项目不覆盖；只有 `tracekin_allow` 能恢复 |
| `tracekin off` / `on` | 只写 `session_overrides`，不改全局字段 |
| `tracekin status` / MCP 状态 | 纯读：`mode=ro` 打开，不建表、不迁移、不 prune；缺表时对应计数为 0 并列入 `missing_tables` |

### 控制指令识别

指令与提示词比较前会去掉反引号、引号、加粗星号、括号等装饰和结尾标点，忽略大小写，整段不匹配时再看第一行。识别为指令的整条消息都不上传，宁可多暂停也不漏传；`how does tracekin off work?` 之类的提问不会被误判。

指令开启的那一轮整体不上报。`tracekin status` 和 `tracekin on` 之后会话仍在共享，所以这一轮会记入 `control_turns` 表（会话与轮次 ID 均为哈希），同一轮后续的事件，例如状态工具调用和模型的确认回复，返回 `control_turn` 而不入队。没有轮次 ID 的 harness 会给控制指令单独计一轮。hook 写路径会按需创建这些辅助表，所以旧版本的数据库在第一次 hook 调用时就受到保护，不必等到下一次 `SessionStart`。

### 投递

companion 的 worker 每 0.5 秒取最旧的待发送事件，发送期间不持有数据库写锁，超时 10 秒。结果处理：

| 接收服务响应 | 处理 |
|------|------|
| 200 且 `accepted` 含该 id | 标记已发送 |
| 400 / 413 / 415 / 422 | 该事件被拒绝，直接丢弃并跳过，面板计入"已跳过" |
| 401 / 403 | 保留，按退避重试，面板显示"接收服务拒绝授权" |
| 其他状态、网络错误、超时 | 保留，退避重试，最长 30 秒 |

本地最多保留 1000 条、7 天；正式模式不发送任何令牌，接收地址固定。

## 状态结构

`tracekin.py status` 与 MCP `tracekin_status` 返回同一份 JSON：

| 字段 | 含义 |
|------|------|
| `config` | 去掉 salt 与令牌后的配置：授权、共享、项目列表、当前项目、地址、宠物偏好 |
| `data_dir` / `legacy_data_dir` | 实际读取目录；后者非空表示旧目录待采用 |
| `counts` / `events` | 待发送与已确认计数、最近 12 条事件 |
| `current_project_authorized` | `active_project` 已在 `projects` 内且共享开启 |
| `sharing_state` / `hint` | `awaiting_session_start` · `binding_project` · `enabled` · `denied`，以及对应的下一步 |
| `session_controls.paused_sessions` | 已暂停会话数 |
| `initialized` / `missing_tables` / `migration_pending` | schema 与迁移诊断 |
| `version` / `schema` / `default_policy` / `proof_status` | 元信息 |

## 事件契约

`schema: tracekin.activity.v1`，每次 POST 一条：`{"schema": ..., "events": [event]}`，请求头 `Content-Type` 与 `Idempotency-Key`（等于事件 id）。

| 字段 | 说明 |
|------|------|
| `id` `project_id` `session_id` `turn_id` | HMAC-SHA256，密钥为设备随机 salt |
| `event` | `UserPromptSubmit` / `PostToolUse` / `Stop` |
| `observed_at` `source` `synthetic` `privacy_mode` | 时间戳、`<harness>_hook`、是否 demo、`full_task` 或 `activity_only` |
| `tool_category` | 仅 PostToolUse：`shell` `edit` `read` `web` `agent` `other` |
| `task_data` | `full_task` 下的明文：`prompt`、`tool_input`、`tool_response`、`last_assistant_message`（存在才带） |

## 各 harness 差异

| | Codex | Claude Code | Cursor | Gemini CLI | OpenCode |
|------|------|------|------|------|------|
| 机制 | stdin JSON hook 命令 | 同 Codex | 同 Codex | 同 Codex | JS 插件订阅事件总线，再调用同一脚本 |
| 事件名 | SessionStart / UserPromptSubmit / PostToolUse / Stop | 同 Codex | sessionStart / beforeSubmitPrompt / postToolUse / stop | SessionStart / BeforeAgent / AfterTool / AfterAgent | session.created / chat.message / tool.execute.after / session.idle |
| 会话 / 轮次 ID | `session_id` / `turn_id` | `session_id` / `prompt_id` | `conversation_id` / `generation_id` | `session_id` / 无，本地按会话计数 | `sessionID` / 无，本地按会话计数 |
| 工具输出字段 | `tool_response` | `tool_response`（2.1.273 实测）或 `tool_output`（文档） | `tool_output` | `tool_response`（对象） | `output.output` |
| Stop 的助手回复 | 有 | 有（实测） | 无，`task_data` 为空 | 有，来自 `prompt_response` | 有，来自最近的 assistant 文本 part |
| 项目来源 | `cwd` | `cwd` | `workspace_roots[0]`，用户级 hook 的工作目录是 `~/.cursor` | `cwd` | `session.directory`，退回插件的 `directory` |
| hook 变量 | `${PLUGIN_ROOT}`，也导出 `CLAUDE_PLUGIN_ROOT` | `${CLAUDE_PLUGIN_ROOT}` | 无，包装脚本自行定位 | `${extensionPath}`，超时单位毫秒 | 无，插件按自身路径定位脚本 |
| hook 输出 | 忽略 | JSON 或 `{}` | `beforeSubmitPrompt` 必须 `{"continue": true}` | `{}`，不得输出非 JSON 文本 | 不适用 |
| 安装 | marketplace | marketplace / `--plugin-dir` | `install-cursor` 或 `~/.cursor/plugins/local` | `gemini extensions install <repo>` 或 `install-gemini` | `install-opencode` |

`tracekin.py hook --harness auto` 会按字段自动识别方言，也可用 `--harness codex|claude-code|cursor|gemini|opencode` 强制。没有轮次 ID 的 harness 由 `Store.record` 按会话分配：新提示词开启第 N+1 轮，工具与结束事件归入当前轮；旧数据库缺少 `session_turns` 表时退回逐事件唯一 ID。

## 排错

| 现象 | 原因与处理 |
|------|------|
| `sharing_state: awaiting_session_start` | 没有数据库：hooks 未信任、未重启、或未从项目目录新建会话 |
| `binding_project` 且 `migration_pending: true` | 0.4.3 及更早版本 hook 与 MCP 读写不同目录；升级后新建会话，对比 SessionStart 输出与状态里的 `data_dir` |
| `denied` | 曾调用 `tracekin_deny`，或 0.4.1 的"清除本地记录"写入了 denied；调用 `tracekin_allow` |
| `tracekin off` 被上传 | 0.4.5 及更早要求逐字匹配；升级 |
| 面板"发送失败" | 看"最近错误"：`HTTP 401 unauthorized` 接收服务要求令牌而正式版不发送，需在服务端移除 `TRACEKIN_TOKEN`；`HTTP 413` 单条过大已跳过；`URLError` / `TimeoutError` 网络不通 |
| 面板打不开 | `runtime.json` 记录的进程已不在；新建会话或手动 `tracekin.py start` |
| companion 起不来 | 看 `~/.tracekin/companion.log`，里面是每次拉起的记录和 companion 自身的输出与报错 |
| 本机面板没有别的电脑的数据 | 预期行为，面板只显示本机队列与回执 |
| Codex 显示 `sharing_enabled=false` | 先 `codex plugin list --json` 确认加载的版本，插件缓存里的旧版本会沿用旧默认值 |

hook 字段日志：`touch ~/.tracekin/debug-hooks` 后，每次 hook 调用往 `~/.tracekin/hook-debug.log` 追加一行，只含事件类型、识别的 harness、字段名和处理结果，不含任何内容；删除标记文件即停止。

## 开发

代码结构：`scripts/tracekin.py` 是唯一入口（`hook` / `start` / `status` / `install-*`），保留 SQLite 存储、共享策略和 companion 进程管理；`scripts/tracekin_lib/` 放随 harness 变化的部分：`common.py`（版本、契约常量、数据目录）、`adapters.py`（五种 harness 方言、工具分类、控制指令）、`delivery.py`（HTTPS 发送）、`installers.py`（Cursor / Gemini CLI / OpenCode 安装器）。库模块从不反向导入入口脚本；`serve.py`、`mcp_server.py` 和测试都从 `tracekin` 导入公开名字。

- 单元测试：`python3 scripts/test_tracekin.py`（标准库，无依赖；会真实拉起并回收 companion 进程）
- 集成冒烟：`python3 scripts/integration_smoke.py`（demo 模式，本机接收器）
- 语法：`python3 -m py_compile scripts/*.py scripts/tracekin_lib/*.py`
- 清单：`claude plugin validate --strict .`（在插件目录）与仓库根 `claude plugin validate .`
- 版本：改 `scripts/tracekin_lib/common.py` 的 `PLUGIN_VERSION` 与 `.codex-plugin` / `.claude-plugin` / `.cursor-plugin` 三份 `plugin.json`、根 `.claude-plugin/marketplace.json` 和根 `gemini-extension.json`，测试会校验一致

本插件只证明"活动与授权的传输链路"，不声称活动元数据是训练语料、任务质量或任何奖励。
