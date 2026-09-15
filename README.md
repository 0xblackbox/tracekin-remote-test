# Tracekin：另一台电脑完整 Hooks/MCP 测试

请先阅读 `plugins/tracekin/README.md`。版本 `0.2.1+codex.20260916051837` 加入一次授权后的默认共享 Dashboard，以及 `tracekin off/on/status` 当前会话控制。此仓库不包含接收服务 Bearer token。

## 快速安装

```bash
codex plugin marketplace add https://github.com/0xblackbox/tracekin-remote-test.git
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

这些命令在 macOS 终端执行；本地压缩包测试时，第一条也可以替换为解压目录路径。重启 Codex Desktop，在 Plugins Directory 启用 Tracekin，并在 Hooks 审核页逐条审阅、信任 bundled hooks。然后新建会话，按 README 配置项目目录、GCP HTTPS 地址和令牌，先做 Hooks 冒烟测试，再做 MCP 只读查询。

## 已安装旧版时升级

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin remove tracekin@tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
codex plugin list --json
```

升级后应显示版本 `0.2.1+codex.20260916051837`；重启 Codex Desktop 后，在 Hooks 页面重新审阅当前定义。
