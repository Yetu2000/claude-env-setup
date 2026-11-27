# claude-env-cleanup

一个 AI agent skill：在 **Windows 和 macOS** 上安全审计、备份、清理或重置 Claude Code / Claude Desktop 的本机状态。

- 单文件脚本，只用 Python 标准库
- 只读审计可以交给 AI 运行，写操作只能由用户在终端里输入 `CONFIRM` 后执行
- 不永久删除：移走的东西进入 `~/ClaudeCleanup/<时间戳>/`，可以 `--rollback` 整批还原

## 安装

把整个目录复制到 agent 的 skills 目录，例如 `~/.claude/skills/claude-env-cleanup`、`~/.codex/skills/claude-env-cleanup`。

## 直接使用

两种模式：

- **基础清理**：缓存日志、登录环境（凭据、钥匙串/凭据管理器、账号字段、settings 里的中转站与密钥）、userID 轮换、会话与项目、记忆
- **深度清理**：在基础之上，把 MCP、插件、skills、hooks、设置、后台会话等配置目录全部内容和全局配置都挪走，只用原来的遥测键重建 settings.json

挪走的东西按类别存进 `~/ClaudeCleanup/<时间戳>/moved/01-缓存日志/…`。

```bash
python3 scripts/claude_env_cleanup.py --audit          # 只读审计（加 --json 输出给 AI）
python3 scripts/claude_env_cleanup.py --mode basic     # 基础清理（需要真实终端，输入 CONFIRM）
python3 scripts/claude_env_cleanup.py --mode deep      # 深度清理
python3 scripts/claude_env_cleanup.py --list-batches
python3 scripts/claude_env_cleanup.py --rollback "<批次目录>" [--dry-run]
```

## 边界

本工具用于隐私整理与排障。它不改遥测设置、时区、机器标识或网络设置，不碰企业托管设置，也不用于规避封禁、支付校验或任何平台风控。
