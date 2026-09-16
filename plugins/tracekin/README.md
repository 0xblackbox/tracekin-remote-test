# Tracekin 插件技术参考

本文是 [仓库 README](../../README.md) 的展开版：运行细节、默认策略、投递、各 harness 差异和排错。版本记录见 [CHANGELOG](../../CHANGELOG.md)，跨电脑验收见 [REMOTE-TEST](../../REMOTE-TEST.md)。

## 四个表面

| 表面 | 文件 | 职责 |
|------|------|------|
| Hooks | `hooks/hooks.json`（Codex、Claude Code）、`cursor/hooks.json`（Cursor） | `SessionStart` 绑定项目并拉起 companion；`UserPromptSubmit` / `PostToolUse` / `Stop` 采集，控制指令在采集前处理 |
| MCP | `codex/mcp.json`、`.mcp.json`、`cursor/mcp.json` → `scripts/mcp_server.py` | `tracekin_status`、`tracekin_data_contract` 只读；`tracekin_allow` 修复 / 重新启用；`tracekin_deny` 全局紧急撤销 |
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

## 运行细节

### SessionStart 绑定

`tracekin.py start` 读取 stdin 里的 `cwd`（Codex、Claude Code）或 `workspace_roots[0]`（Cursor），退回 `CURSOR_PROJECT_DIR` / `CLAUDE_PROJECT_DIR` / 进程目录。目录会被追加进 `projects` 并设为 `active_project`；根目录和用户主目录会被拒绝，但 companion 仍会以已记录的项目继续运行。SessionStart 的输出在 Codex / Claude Code 下是诊断 JSON（`tracekin`、`project`、`data_dir`、`error`），在 Cursor 下是 `{}`。

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

| | Codex | Claude Code | Cursor |
|------|------|------|------|
| 会话 / 轮次 ID | `session_id` / `turn_id` | `session_id` / `prompt_id` | `conversation_id` / `generation_id` |
| 工具输出字段 | `tool_response` | `tool_response`（2.1.273 实测）或 `tool_output`（文档） | `tool_output` |
| Stop 的助手回复 | 有 | 有（实测） | 无，`task_data` 为空 |
| 项目来源 | `cwd` | `cwd` | `workspace_roots[0]`，用户级 hook 的工作目录是 `~/.cursor` |
| hook 变量 | `${PLUGIN_ROOT}`，也导出 `CLAUDE_PLUGIN_ROOT` | `${CLAUDE_PLUGIN_ROOT}` | 无，包装脚本自行定位 |
| hook 输出 | 忽略 | JSON 或 `{}` | `beforeSubmitPrompt` 必须 `{"continue": true}` |
| 安装 | marketplace | marketplace / `--plugin-dir` | `install-cursor` 或 `~/.cursor/plugins/local` |

`tracekin.py hook --harness auto` 会按字段自动识别方言，也可用 `--harness codex|claude-code|cursor` 强制。

## 排错

| 现象 | 原因与处理 |
|------|------|
| `sharing_state: awaiting_session_start` | 没有数据库：hooks 未信任、未重启、或未从项目目录新建会话 |
| `binding_project` 且 `migration_pending: true` | 0.4.3 及更早版本 hook 与 MCP 读写不同目录；升级后新建会话，对比 SessionStart 输出与状态里的 `data_dir` |
| `denied` | 曾调用 `tracekin_deny`，或 0.4.1 的"清除本地记录"写入了 denied；调用 `tracekin_allow` |
| `tracekin off` 被上传 | 0.4.5 及更早要求逐字匹配；升级 |
| 面板"发送失败" | 看"最近错误"：`HTTP 401 unauthorized` 接收服务要求令牌而正式版不发送，需在服务端移除 `TRACEKIN_TOKEN`；`HTTP 413` 单条过大已跳过；`URLError` / `TimeoutError` 网络不通 |
| 面板打不开 | `runtime.json` 记录的进程已不在；新建会话或手动 `tracekin.py start` |
| 本机面板没有别的电脑的数据 | 预期行为，面板只显示本机队列与回执 |
| Codex 显示 `sharing_enabled=false` | 先 `codex plugin list --json` 确认加载的版本，插件缓存里的旧版本会沿用旧默认值 |

hook 字段日志：`touch ~/.tracekin/debug-hooks` 后，每次 hook 调用往 `~/.tracekin/hook-debug.log` 追加一行，只含事件类型、识别的 harness、字段名和处理结果，不含任何内容；删除标记文件即停止。

## 开发

- 单元测试：`python3 scripts/test_tracekin.py`（标准库，无依赖；会真实拉起并回收 companion 进程）
- 集成冒烟：`python3 scripts/integration_smoke.py`（demo 模式，本机接收器）
- 语法：`python3 -m py_compile scripts/*.py`
- 清单：`claude plugin validate --strict .`（在插件目录）与仓库根 `claude plugin validate .`
- 版本：改 `PLUGIN_VERSION` 与 `.codex-plugin` / `.claude-plugin` / `.cursor-plugin` 三份 `plugin.json` 及根 `.claude-plugin/marketplace.json`，测试会校验一致

本插件只证明"活动与授权的传输链路"，不声称活动元数据是训练语料、任务质量或任何奖励。
