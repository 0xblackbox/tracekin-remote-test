# Tracekin：另一台电脑完整 Hooks/MCP 测试

请先阅读 `plugins/tracekin/README.md`。版本 `0.4.5` 安装后默认开启 Tracekin Cloud 同步，首次 `SessionStart` 自动绑定当前项目，旧数据库（pending / 缺少决策 / 仅 false）自动迁移为默认开启，只有显式 `tracekin_deny` 会保持全局关闭；hooks 与 MCP 统一使用 `~/.codex/tracekin` 数据目录，`tracekin_status` 为纯读操作并返回 `data_dir`，提供 Dashboard 和 `tracekin off/on/status` 当前会话控制。

## 快速安装

```bash
codex plugin marketplace add https://github.com/0xblackbox/tracekin-remote-test.git
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

这些命令在 macOS 终端执行；本地压缩包测试时，第一条也可以替换为解压目录路径。重启 Codex Desktop，在 Plugins Directory 启用 Tracekin，并在 Hooks 审核页逐条审阅、信任 bundled hooks。然后从测试项目新建会话，Tracekin 会自动开启同步；敏感任务使用 `tracekin off` 暂停当前会话。

## 已安装旧版时升级

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

升级后应显示版本 `0.4.5+codex.<cachebuster>`；重启 Codex Desktop 后，在 Hooks 页面重新审阅当前定义，并从项目目录新建会话让 `SessionStart` 完成迁移和项目绑定。
