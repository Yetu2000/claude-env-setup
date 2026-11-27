---
name: claude-env-cleanup
description: 在 Windows 和 macOS 上审计、备份并清理 Claude Code 与 Claude Desktop 的本机环境。基础清理：登录环境（凭据、钥匙串/凭据管理器、账号字段、settings 里的中转站与密钥）、轮换 userID/machineID、会话与项目、记忆、缓存日志；深度清理：再挪走 MCP、插件、skills、hooks、设置、后台会话等配置目录全部内容和全局配置。所有东西按类别挪进新文件夹，可整批回滚。
---

# Claude 本机环境清理（Windows / macOS）

所有动作都通过随附脚本 `scripts/claude_env_cleanup.py`完成。**不要自己拼 `rm` / `Remove-Item` / `reg delete`。** 脚本不永久删除任何东西：挪走的内容按类别存进 `~/ClaudeCleanup/<时间戳>/`，被原地修改的文件留有原件，可以 `--rollback` 整批还原。

分工：**AI 只运行只读审计并解释结果；写操作由用户自己在普通终端里运行脚本，并亲自输入 CONFIRM。**

## 两种模式

| | 基础清理（`--mode basic`） | 深度清理（`--mode deep`） |
|---|---|---|
| 缓存日志 | 挪走 | 挪走 |
| 登录环境 | 挪走 `.credentials.json`、`.device-keys.json` 等；删除钥匙串 / 凭据管理器条目；从 `.claude.json` 删除 `oauthAccount`、`primaryApiKey` 和账号缓存；从 `settings.json` 删除中转站、密钥和代理键（`ANTHROPIC_*`、`apiKeyHelper`、`HTTP(S)_PROXY` 等），其他设置保留 | 一样，外加 settings 整个挪走 |
| userID / machineID | 原地换新，**同时改写所有后缀版本、旧版 `.config.json` 和 `backups/` 里的历史备份**（否则 Claude 会从备份恢复旧值） | 全局配置整个挪走，下次启动重新生成 |
| 会话与项目 | 挪走 `projects`（含项目自动记忆）、`sessions`、`history.jsonl`、`file-history`、`todos`、`plans` 等；从 `.claude.json` 删除 `projects` 记录（打开过的项目路径、信任与权限、项目级 MCP） | 同左 |
| 记忆 | 挪走 `memory`、`agent-memory` | 同左 |
| MCP、插件、skills、hooks、命令、子代理、CLAUDE.md、主题 | 保留 | 挪走 |
| 后台会话（`jobs`、`daemon`、`state`）、未分类条目 | 保留 | 挪走 |
| 遥测键 | 原样保留 | 用原值重建一个只有遥测键的 `settings.json` |
| 两种模式都会问 | Claude Desktop：0 保留 / 1 数据（含登录态、Code 会话、Cowork、Desktop 的 MCP 配置）/ 2 数据加应用 | 同左；另外可选：深链与浏览器原生宿主注册、原生安装的程序、项目里的个人配置（`.claude/settings.local.json`、`CLAUDE.local.md`） |

批次目录结构：`moved/01-缓存日志`、`02-登录凭据`、`03-会话与项目`、`04-记忆`、`05-MCP与插件`、`06-自定义配置`、`07-设置`、`08-后台会话与状态`、`09-全局配置`、`10-ClaudeDesktop`、`11-系统注册`、`12-CLI程序`、`13-项目内个人配置`、`99-其他`。每个类别下保留原来的相对路径，另有 `edited-originals/`、`registry/`、`manifest.json`、`README.txt`。

## 运行顺序

1. **只读审计**（任何执行者都可以运行，包括 Claude Code 自己）：

   ```bash
   python3 scripts/claude_env_cleanup.py --audit --json     # macOS
   py -3 scripts\claude_env_cleanup.py --audit --json       # Windows
   ```

   不要逐行念 JSON，向用户说明这几点：
   - 生效的配置目录和全局配置文件（`legacy_active` 为真表示旧版 `.config.json` 优先生效）；
   - 基础清理和深度清理各会挪走什么、有多大；
   - 记录了多少个项目、有哪些 MCP、settings 里有哪些中转站键（只报键名，不要复述密钥值）；
   - 凭据实际存在哪里；
   - `shell_relay_vars`：shell 配置或用户环境变量里的中转站和密钥。脚本不改这些，要提醒用户自己删掉，否则清理后仍会走中转站；
   - `notes` 里的提示（npm 安装、后台会话、项目里提交到 Git 的共享文件等）。

2. **如果当前执行者是 Claude Code**（`under_claude_code` 为真）：只做到审计为止，让用户换到普通终端或其他 agent 去运行。正在使用 `~/.claude` 的 Claude Code 不能清理它自己。

3. 用户选好模式后，告诉他在**真实终端**里运行：

   ```bash
   python3 scripts/claude_env_cleanup.py --mode basic   # 或 --mode deep；不带参数则在终端里选
   ```

   不要用管道代答，也不要替用户输入。脚本发现 stdin 不是终端、或者是由 Claude Code 启动的，会直接拒绝执行。

4. 脚本会要求用户自己退出所有 Claude Code / Desktop，并运行一次 `claude daemon stop`（脚本不会结束进程）。之后展示完整清单，用户输入 `CONFIRM` 才会写盘。

5. 用户回来后，再运行一次 `--audit` 核对结果。如果脚本报告"部分完成"，就如实告诉用户是部分完成，并给出它打印的回滚命令。

## 回滚与收尾

```bash
python3 scripts/claude_env_cleanup.py --list-batches
python3 scripts/claude_env_cleanup.py --rollback "<批次目录>" --dry-run   # AI 可以运行
python3 scripts/claude_env_cleanup.py --rollback "<批次目录>"             # 用户在终端运行，输入 RESTORE
```

回滚会把挪走的内容放回原处（原处已有新文件时跳过并说明）、还原被修改的文件、导入注册表备份。已删除的钥匙串 / 凭据管理器条目无法还原，只能重新登录。批次目录里有凭据和会话内容，用户确认不再需要后由他自己删除，AI 不要代删。

## 不做的事

- 不永久删除，不结束进程，不改遥测设置（`DISABLE_TELEMETRY`、`DO_NOT_TRACK`、`DISABLE_ERROR_REPORTING`、`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` 逐值保持原样，执行后会复检）。
- 不碰项目源码，也不碰项目里提交到 Git、与团队共享的 `.mcp.json`、`.claude/settings.json`、`CLAUDE.md`（只报告）。
- 不碰企业托管设置（Windows `C:\Program Files\ClaudeCode`、macOS `/Library/Application Support/ClaudeCode`）。
- 不改 shell 配置文件、时区、系统语言、机器标识或网络设置。
