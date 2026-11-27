# claude-browser-env

一个 AI agent skill：在 **Windows 和 macOS** 上为 Claude 准备浏览器环境，并让系统时区、语言与 Clash 出口对齐。Claude 环境配置的第三步，前两步是 claude-env-cleanup（清理本机）和 claude-network（Clash 网络）。

- 单文件脚本，只用 Python 标准库（3.9+）
- 只读审计可以交给 AI 运行；写操作只能由用户在终端里输入 `CONFIRM` 后执行
- 修改前把原值写进 `~/ClaudeBrowserEnv/<时间戳>/manifest.json`，可以 `--rollback` 整批还原
- 浏览器里的操作（清理站点数据、装 WebRTC 扩展、改语言、配置指纹浏览器）以清单形式给出，脚本不改浏览器文件

## 安装

把整个目录复制到 agent 的 skills 目录，例如 `~/.claude/skills/claude-browser-env`。

## 直接使用

```bash
python3 scripts/claude_browser_env.py --audit            # 只读审计（加 --json 输出给 AI）
python3 scripts/claude_browser_env.py --plan             # 只读：列出 --apply 会改什么
python3 scripts/claude_browser_env.py --apply            # 对齐时区、国家或地区、语言列表（真实终端，输入 CONFIRM）
python3 scripts/claude_browser_env.py --apply --country US --timezone America/Los_Angeles --english-first
python3 scripts/claude_browser_env.py --list-batches
python3 scripts/claude_browser_env.py --rollback "<批次目录>" [--dry-run]
```

Windows 上把 `python3` 换成 `py -3`。常用参数：`--proxy http://127.0.0.1:7897`（经 Clash 端口探测出口）、`--skip-region`、`--skip-languages`、`--no-network`。

## 审计内容

- 默认浏览器（Claude Code 登录时会打开它）
- Chrome、Edge、Brave、Chromium、Vivaldi、Opera、360、QQ 浏览器、Firefox、Safari 的每个资料：语言、WebRTC 防护（托管策略 / 扩展 / 偏好）、claude.ai 等网站的 Cookie 条数与访问次数（不读值）
- 指纹浏览器：AdsPower、比特、Hubstudio、MoreLogin、Multilogin、GoLogin、Dolphin Anty、Incogniton、VMLogin、Kameleo、Octo、紫鸟、RoxyBrowser
- 系统时区、国家或地区、语言列表、自动设置时区
- 直连（TUN）与系统代理两条路径的出口 IP、城市、时区

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试用临时目录模拟浏览器数据，用替身代替系统命令，不触碰真实设置，也不联网。

## 边界

只帮助减少环境里的明显矛盾和 WebRTC 泄漏，不承诺平台如何判断账号；清理本机数据不能恢复被封的账号，恢复只能走官方申诉。不改代理、DNS、区域格式和机器标识。
