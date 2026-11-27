---
name: claude-browser-env
description: 在 Windows 和 macOS 上为 Claude 准备浏览器环境，并让时区和语言与 Clash 出口对齐。查看默认浏览器、已装的普通浏览器和指纹浏览器（AdsPower、比特等）、每个浏览器资料的语言设置、WebRTC 防护和 claude.ai 使用痕迹；按出口 IP 所在城市推荐系统时区、国家或地区、语言列表和浏览器语言，可执行并整批回滚；给出本地浏览器（清理站点数据、装 WebRTC 防泄漏扩展、改语言）和指纹浏览器（AdsPower 逐项配置）的操作清单。属于 Claude 环境配置的第三步，排在 claude-env-cleanup（清理本机）和 claude-network（Clash 网络）之后。
---

# Claude 浏览器、时区与语言（Windows / macOS）

审计和修改都通过随附脚本 `scripts/claude_browser_env.py`（只用 Python 标准库）完成。**不要自己拼 `tzutil`、`Set-WinUserLanguageList`、`systemsetup`、`defaults write`。** 脚本先把原值写进 `~/ClaudeBrowserEnv/<时间戳>/manifest.json`，可以 `--rollback` 整批还原。

分工：**AI 只运行只读的 `--audit` / `--plan`，负责解释结果和向用户提问；写操作由用户自己在普通终端里运行，并亲自输入 CONFIRM。** 浏览器里的设置（清理站点数据、装扩展、改语言、配置指纹浏览器）由用户按清单手动操作，脚本不改浏览器文件。

前提：第二步 claude-network 已经配好 Clash，出口在海外。审计显示直连出口在中国大陆时，先回第二步，不要往下做。

## 1. 只读审计

```bash
python3 scripts/claude_browser_env.py --audit --json     # macOS
py -3 scripts\claude_browser_env.py --audit --json       # Windows
# Clash 没开系统代理、但想测它的端口：加 --proxy http://127.0.0.1:7897
```

建议先让用户关掉浏览器再审计。浏览器开着时 Cookie 库被占用，`cookie_status` 会显示"被占用"，这时只能看访问记录。

不要逐行念 JSON，向用户说明这几点：

- `default_browser`：默认浏览器。**Claude Code 登录时会用默认浏览器打开授权页。** 如果用户打算在别的浏览器（包括指纹浏览器）里登录，就让他把终端里打印的登录链接复制到那个浏览器里打开，不要直接点。默认浏览器显示成 IE 或"未知"时，提醒他在系统设置里明确选一个。
- `browsers[].profiles[]`：每个浏览器资料的语言（`languages`、`language_ok`）、WebRTC 状态（`webrtc` 为 protected / partial / missing）、`claude.used_claude`（有没有 claude.ai / anthropic.com 的 Cookie 和访问记录，只报条数和最近日期）。
- `fingerprint_browsers`：检测到的指纹浏览器。
- `system`：当前时区、国家或地区（Windows GeoID / macOS AppleLocale）、语言列表、`auto_timezone`（自动设置时区）。
- `exits`：直连（走 TUN）和系统代理两条路径的出口 IP、国家、城市、时区。
- `recommendation`：目标时区、语言建议、`ops`（待调整项）、`browser_languages`（浏览器语言顺序建议）、`notes`。

## 2. 问用户：Claude 登录在哪个浏览器

用提问工具问一次，选项来自审计结果：检测到的指纹浏览器、各个本地浏览器（标出默认浏览器和有 Claude 痕迹的资料）。

### 2A. 用自己的浏览器（Chrome / Edge / Firefox / Safari 等）

再问两件事：

1. 以前有没有在这个浏览器里注册或登录过 Claude 账号？（`used_claude` 为真时，把检测到的条数和日期告诉他，请他确认）
2. 这些账号有没有被封过？

- **登录过或被封过**：提醒他在登录前清理这个浏览器里 claude.ai、claude.com、anthropic.com 的站点数据（Cookie、本地存储、缓存），更干净的做法是**新建一个浏览器资料（Profile）专门给 Claude 用**。具体步骤见 [`references/local-browser.md`](references/local-browser.md) 第 1 节。同时如实说明：封禁记录在服务端，清理本机浏览器数据不会让被封的账号恢复；要恢复只能走官方申诉。
- **都没有**：不用清理，建议仍然单独建一个资料，避免和日常浏览混在一起。

然后按 `local-browser.md` 第 2～4 节处理 WebRTC 和语言（见下面第 4、5 步）。

### 2B. 用指纹浏览器

按 [`references/fingerprint-browser.md`](references/fingerprint-browser.md) 逐项核对，核心几条要讲清楚：

- 操作系统按本机真实系统选（Mac 选 macOS、Windows 选 Windows），User-Agent 范围选"全部"，不要跨系统伪装。
- WebRTC：单独绑了海外 Socks5 代理时选"代理 UDP"或"禁用"；选 noproxy、走本机 Clash 分流时**必须选"禁用"**。
- 时区、地理位置、语言都"基于 IP"，界面语言"基于语言"。
- 画布、WebGL 图像、AudioContext、ClientRects、SpeechVoices 的噪音全部关掉（选"真实"），只留"媒体设备"噪音；硬件参数全部"真实"。
- 端口扫描保护启用，随机指纹保持关闭。
- 启动后先看自检页，再用 BrowserScan 检查指纹真实度、时区、语言、DNS 和 WebRTC。
- 以后只在这个环境窗口里打开 claude.ai 和相关邮箱，不要在日常浏览器里直接登录；邮件里的验证链接右键复制，粘到这个窗口里打开。

指纹浏览器自己会按出口 IP 设置浏览器时区和语言，但**电脑本身的时区仍然要对齐**（Claude Code、Claude Desktop 和系统里的其他程序用的是系统时区），继续做第 3 步。

## 3. 时区与语言：按出口对齐

规则（脚本已经实现，向用户解释时用这个说法）：

- **时区跟出口城市走**：出口在新加坡就用新加坡时区（Asia/Singapore），出口在洛杉矶就用洛杉矶时区（America/Los_Angeles），而不是简单按国家：美国有好几个时区，时区取自 ipinfo 返回的城市时区。用户买的 IP 城市和探测结果不一致时，以他买的为准，用 `--timezone` 指定。
- **语言可以保留中文**：系统显示语言和第一语言可以继续用中文，把出口地区的英语（新加坡 en-SG、美国 en-US、英国 en-GB……）加为第二语言。新加坡、马来西亚、港澳台华人多，中文排第一很自然；其他地区想更贴近本地用户，可以加 `--english-first` 让英语排第一（Windows 会同时锁定当前显示语言，界面不会变成英文；macOS 重新登录后界面会变成英文，要提醒用户）。
- **"时间和语言"设置里的国家或地区也一起改**：Windows 改 GeoID（Set-WinHomeLocation），macOS 改 AppleLocale 的地区部分（例如 zh_CN → zh_SG），日期和数字格式会随之变化。不想改就加 `--skip-region`。
- **自动设置时区要关**：`auto_timezone` 为真时系统会按定位把时区改回去，让用户先在系统设置里关掉（脚本不改这一项）。

步骤：

1. AI 运行 `--plan`（只读）给用户看将要改什么；需要时带上 `--country`、`--timezone`、`--english-first`、`--skip-region`、`--skip-languages`。
2. 用户在**真实终端**里运行同样参数的 `--apply`，确认清单后输入 `CONFIRM`。macOS 改时区会要求输入开机密码（sudo）。不要用管道代答，也不要替用户输入；脚本发现 stdin 不是终端、或由 Claude Code 启动时会拒绝执行。
3. 改完让用户完全退出并重新打开浏览器（浏览器时区才会刷新），注销重新登录一次（语言和地区才完全生效）。
4. 脚本报告"部分完成"时如实告诉用户，并给出它打印的回滚命令。

细节和手动操作路径见 [`references/timezone-language.md`](references/timezone-language.md)。

## 4. WebRTC 防泄漏

Claude Code 在本地使用时会打开浏览器（登录授权、打开链接），所以登录 Claude 的浏览器都要处理 WebRTC：

1. **指纹浏览器**：按 2B 里的 WebRTC 设置做。
2. **本地 Chrome / Edge / Brave 等 Chromium 浏览器**：在登录 Claude 的那个资料里装 WebRTC Protect 扩展：<https://chromewebstore.google.com/detail/webrtc-protect-protect-ip/bkmmlbllpjdpgcgdohbaghfaecnddhni>（Edge 需要先允许来自其他应用商店的扩展）。
3. **Firefox**：在 `about:config` 里把 `media.peerconnection.ice.default_address_only` 和 `media.peerconnection.ice.no_host` 设为 true，或者装同类扩展。
4. **Safari**：没有对应扩展，以实测为准；实测能看到真实 IP 就换浏览器。

装完重新审计，对应资料的 `webrtc` 应该变成 protected，再到 <https://browserleaks.com/webrtc> 实测：只应看到代理出口 IP，看不到本地宽带 IP。审计结果是 protected 不代表实测一定没有泄漏，以实测为准。

## 5. 浏览器语言要对上

浏览器语言顺序和系统语言一致，用审计结果里的 `browser_languages`（例如 `zh-CN, zh, en-US, en`）。在登录 Claude 的那个资料里改，路径见 `local-browser.md` 第 3 节。改完重新审计，`language_ok` 应该为真。指纹浏览器的语言由它"基于 IP"自动设置，不用手改。

## 6. 收尾核对

- 重新运行 `--audit`：`recommendation.ops` 为空，登录 Claude 的资料 `webrtc` 为 protected、`language_ok` 为真。
- 在要用的浏览器里打开 <https://www.browserscan.net>：IP 时区、系统时区、浏览器语言一致，没有 DNS 泄漏和 WebRTC 泄漏。
- 提醒用户：登录 Claude 只用选定的浏览器或资料；Claude Code 登录时把链接复制过去。

## 回滚

```bash
python3 scripts/claude_browser_env.py --list-batches
python3 scripts/claude_browser_env.py --rollback "<批次目录>" --dry-run   # AI 可以运行
python3 scripts/claude_browser_env.py --rollback "<批次目录>"             # 用户在终端运行，输入 RESTORE
```

回滚按 manifest 恢复时区、国家或地区、语言列表（保留原来的输入法）和显示语言锁定。浏览器里的手动改动不在回滚范围内。

## 不做的事

- 不改浏览器文件：不清 Cookie、不装扩展、不改浏览器语言、不写浏览器托管策略，这些都由用户按清单操作。
- 不读 Cookie 的值、密码和浏览内容，只统计 claude.ai / claude.com / anthropic.com 相关的 Cookie 条数和访问次数。
- 不改代理、DNS、网络设置（那是第二步的事），不改区域格式（Set-Culture）、系统区域设置和机器标识。
- 不承诺任何效果：环境一致只能减少明显矛盾和 WebRTC 泄漏，不能决定平台怎么判断账号，也不能恢复被封的账号。

## 参考

- [`references/fingerprint-browser.md`](references/fingerprint-browser.md)：AdsPower 新建环境逐项配置表、启动核对、BrowserScan 检测，以及其他指纹浏览器的对应项
- [`references/local-browser.md`](references/local-browser.md)：本地浏览器清理站点数据或新建资料、WebRTC、语言设置、实测网站
- [`references/timezone-language.md`](references/timezone-language.md)：时区与语言的对齐规则、脚本具体执行的命令、系统设置里的手动路径
