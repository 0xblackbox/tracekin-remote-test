# Tracekin：另一台电脑完整 Hooks/MCP 测试

请先阅读 `plugins/tracekin/README.md` 中的“跨电脑完整 Hooks/MCP 测试”章节。此压缩包把本地 marketplace manifest 放在 `.agents/plugins/marketplace.json`，只含插件与安装说明，不含接收服务 Bearer token。

## 快速安装

```bash
codex plugin marketplace add /path/to/tracekin-remote-test-bundle
codex plugin add tracekin@tracekin-remote-test
codex plugin list
```

重启 Codex Desktop，在 Plugins Directory 启用 Tracekin，并在 Hooks 审核页逐条审阅、信任 bundled hooks。然后新建会话，按 README 配置项目目录、GCP HTTPS 地址和令牌，先做 Hooks 冒烟测试，再做 MCP 只读查询。

## 已安装旧版时升级

```bash
codex plugin marketplace upgrade tracekin-remote-test
codex plugin add tracekin@tracekin-remote-test
```

升级后应显示版本 `0.1.0+codex.20260915172931` 或更高版本；重启 Codex Desktop 后，在 Hooks 页面重新审阅当前定义。
