# Claude Code 自带的按期清理

Claude Code 启动时会运行一次保留期清理（遥测事件名 `tengu_retention_sweep`），按文件修改时间删掉早于 `cleanupPeriodDays` 天的数据。默认 30 天，最小值为 1。

| 范围 | 备注 |
|---|---|
| `projects/**` 里的会话转录（`.jsonl`、`.cast` 及其附属文件） | 由 Claude Desktop / Cowork 创建的转录默认豁免，豁免期上限由 `desktopSessionCleanupPeriodDays` 控制（默认 0，表示不设上限） |
| `file-history`、`session-env`、`tasks`、`uploads`、`plans`、`shares` | 按会话目录删除 |
| `debug/*`（保留 `latest`）、`shell-snapshots/*.sh`、`dump-prompts/*.jsonl` | |
| `telemetry/*.json`、`telemetry/runs`、`traces/*.json`、`startup-perf/*` | |
| `feedback-bundles/*.zip`、`feedback/drafts`（固定 30 天）、`remote-control/fork-seeds` | |
| `usage-data/facets`、`usage-data/session-meta`、`usage-data/*.html` | `/insights` 报告 |
| `backups/*` | 全局配置的历史备份 |
| `jobs/settled`、`daemon/dispatch*`、`daemon/auth`、`daemon.log(.1)`、`daemon/roster.json` | 后台会话残留 |
| `mcp-discovery-cache`、`state/served-calls`、`state/unattended-serving-runs` | |
| env-paths 缓存目录里的 `mcp-logs-*`；临时目录里的 `cc-transcript-*.txt`、`cc-daemon-*` | |
| `history.jsonl` | 只在 HIPAA 模式下按期修剪 |

以下情况会跳过清理：任一设置文件读不出来或校验失败；服务端托管的设置还没下发。清理有节流，标记文件是 `C/.last-cleanup`。

## 什么时候用这个方案代替脚本

- 用户只是想"旧会话别一直留着"，并不想立刻清理：改 `cleanupPeriodDays` 就够了，不移动任何文件。
- 不想再保存会话转录：源码的提示是用 `--no-session-persistence` 启动参数（SDK 里对应 `persistSession: false`）。`cleanupPeriodDays` 不能设成 0。

修改方法：先备份 `settings.json`，只改 `cleanupPeriodDays` 这一个键，改完确认文件仍是合法 JSON。
