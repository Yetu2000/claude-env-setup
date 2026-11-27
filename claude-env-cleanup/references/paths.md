# Claude 本机路径对照（Windows / macOS）

记号：`C` = 配置目录，`G` = 全局配置所在目录。

## 配置目录与全局配置

| 项 | 规则 |
|---|---|
| `C` | `CLAUDE_CONFIG_DIR`；未设置时为 `~/.claude`（Windows 是 `%USERPROFILE%\.claude`） |
| `G` | `CLAUDE_CONFIG_DIR`；未设置时为主目录 |
| 全局配置 | `G/.claude{后缀}.json`，后缀为 `""`（正式环境）、`-custom-oauth`（设了 `CLAUDE_CODE_CUSTOM_OAUTH_URL`）、`-staging-oauth`、`-local-oauth` |
| 旧版全局配置 | `C/.config.json`：存在且非空时**优先于**上面那个文件生效 |
| 全局配置备份 | `C/backups/.claude.json.backup.<时间戳>`（Claude Code 自己按期清理） |
| 全局配置里的内容 | `userID`（即上报的 `device_id`，`randomBytes(32)` 生成）、`machineID`（同样是随机数，与硬件无关）、`oauthAccount`（邮箱、姓名、组织、套餐）、GrowthBook 开关与实验分组缓存、`mcpServers`、`projects` 等 |

## 凭据

| 平台 | 位置 |
|---|---|
| macOS | 钥匙串，服务名 `Claude Code{后缀}-credentials{哈希}`（OAuth）、`Claude Code{后缀}{哈希}`（API Key）；账户名 `claude-code-user`。设了 `CLAUDE_CONFIG_DIR` 时 `{哈希}` = `-` + sha256(配置目录) 的前 8 位 |
| macOS | 设备密钥：钥匙串 `Claude Code-device-keys`，以及文件 `~/.claude/.device-keys.json` |
| Windows | 默认是明文文件 `C\.credentials.json`；开关 `tengu_windows_credman` 打开或设了 `CLAUDE_CODE_FORCE_WINDOWS_CREDMAN=1` 时改存凭据管理器（目标名 ⚠️ 推测为 `Claude Code-credentials`，脚本按 `Claude Code` 前缀匹配） |
| 两者 | `~/.claude/.device-keys.json` **固定在主目录**，不跟 `CLAUDE_CONFIG_DIR` 走 |
| 两者 | `C/hfi-auth.json`、`C/.session_ingress_token`（附加令牌） |

## 配置目录内的条目（2.1.325 源码枚举）

| 批次类别 | 条目 | 基础 | 深度 |
|---|---|---|---|
| 01-缓存日志 | `cache` `paste-cache` `image-cache` `mcp-discovery-cache` `statsig` `stats-cache.json` `telemetry` `traces` `startup-perf` `debug` `shell-snapshots` `dump-prompts` `feedback-bundles` `mcp-needs-auth-cache.json` `gh-pr-status-cache.json` `.last-update-result.json` `logs` `api-dumps` `usage*.jsonl` | ✔ | ✔ |
| 02-登录凭据 | `.credentials.json` `.device-keys.json` `hfi-auth.json` `.session_ingress_token` | ✔ | ✔ |
| 03-会话与项目 | `projects`（含 `projects/*/memory` 自动记忆）`sessions` `history.jsonl` `file-history` `todos` `tasks` `plans` `session-env` `shares` `uploads` `usage-data` `ide` `file-transfers` `downloads` | ✔ | ✔ |
| 04-记忆 | `memory` `agent-memory` | ✔ | ✔ |
| 05-MCP与插件 | `plugins` `mcp-servers` `cowork_plugins` `mcp-skill-archives` | 保留 | ✔ |
| 06-自定义配置 | `skills` `commands` `agents` `hooks` `rules` `themes` `keybindings.json` `workflows` `teams` `output-styles` `scheduled-tasks` `scripts` `CLAUDE.md` `loop.md` | 保留 | ✔ |
| 07-设置 | `settings*.json` `remote-settings.json` `policy-limits.json` | 原地删除中转站/密钥键 | ✔（之后只用遥测键重建 settings.json） |
| 08-后台会话与状态 | `jobs` `daemon` `daemon*.json` `daemon.log*` `state` `local` `ccr` `chrome` `remote-control` `feedback`，以及 `.last-cleanup` 等标记文件 | 保留 | ✔ |
| 09-全局配置 | `.claude*.json` `.config.json` `backups` | 原地换新 ID、删除账号与项目记录 | ✔ |
| 99-其他 | 源码清单里没有的条目 | 保留 | ✔ |

`jobs`、`daemon`、`daemon.json`、`shell-snapshots`、`session-env` 里的文件会被 Claude Code（后台会话服务）自动执行。清理前必须先退出 Claude，并运行 `claude daemon stop`。

## 配置目录以外

| 内容 | Windows | macOS |
|---|---|---|
| env-paths 缓存（含 `mcp-logs-*`） | `%LOCALAPPDATA%\claude-cli-nodejs\{Cache,Data,Log}`、`%TEMP%\claude-cli-nodejs` | `~/Library/Caches/claude-cli-nodejs`、`~/Library/Logs/claude-cli-nodejs`、`~/Library/Application Support/claude-cli-nodejs`、`$TMPDIR/claude-cli-nodejs` |
| 临时文件 | `%TEMP%\cc-transcript-*.txt`、`%TEMP%\cc-daemon-*` | `$TMPDIR` 下同名 |
| 原生安装 | `%USERPROFILE%\.local\bin\claude.exe`、`%USERPROFILE%\.local\share\claude\versions`、`%USERPROFILE%\.cache\claude\staging` | `~/.local/bin/claude`、`~/.local/share/claude/versions`、`~/.cache/claude/staging`（遵循 `XDG_DATA_HOME`/`XDG_CACHE_HOME`） |
| 深链协议 `claude-cli://` | `HKCU\Software\Classes\claude-cli` | `~/Applications/Claude Code URL Handler.app`（`com.anthropic.claude-code-url-handler`） |
| Claude in Chrome 原生宿主 | `%APPDATA%\Claude Code\ChromeNativeHost\`；`HKCU\Software\<浏览器>\NativeMessagingHosts\com.anthropic.claude_code_browser_extension` | `~/Library/Application Support/<浏览器>/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json` |
| 后台会话服务 | 无（按需启动） | launchd `com.anthropic.claude-daemon` （`claude daemon uninstall` 可移除） |
| 企业托管设置（永不触碰） | `C:\Program Files\ClaudeCode\` | `/Library/Application Support/ClaudeCode/` |

## Claude Desktop

| 内容 | Windows | macOS |
|---|---|---|
| 日志/缓存/崩溃报告（A） | `%APPDATA%\Claude\{logs,Cache,Code Cache,GPUCache,Dawn*,Crashpad}` | `~/Library/Logs/Claude`、`~/Library/Caches/com.anthropic.claudefordesktop*`、`~/Library/Logs/DiagnosticReports/Claude*`、`~/Library/Application Support/CrashReporter/Claude*` |
| 数据（R） | `%APPDATA%\Claude`、`%LOCALAPPDATA%\Claude-3p`、`%APPDATA%\Claude-3p`、商店版 `%LOCALAPPDATA%\Packages\{Claude_pzs8sxrjxfjjc,AnthropicPBC.Claude_fnn82j28hfe8t}\LocalCache\Roaming\Claude` | `~/Library/Application Support/{Claude,Claude-3p,com.anthropic.claudefordesktop}`、`~/Library/{HTTPStorages,WebKit}/com.anthropic.claudefordesktop`、`~/Library/Preferences/com.anthropic.claudefordesktop*.plist`、`~/Library/Saved Application State/com.anthropic.claudefordesktop.savedState` |
| 应用（R） | `%LOCALAPPDATA%\AnthropicClaude`、`%LOCALAPPDATA%\Programs\Claude`；商店版用 `Get-AppxPackage *Claude* \| Remove-AppxPackage` | `/Applications/Claude.app` |
| Electron 加密钥匙 | 在数据目录的 `Local State` 里（DPAPI 加密），随数据目录一起处理 | 钥匙串 `Claude Safe Storage`（本工具不处理） |
