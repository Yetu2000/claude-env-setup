---
name: claude-env-setup
description: Claude ENV Setup（Claude 环境设置指南）总控 skill。面向被封号后准备重置环境、或首次配置稳定使用环境的用户，按固定顺序编排 3 个子 skill：1) claude-env-cleanup（清理本机 Claude 残留与登录态）→ 2) claude-network（配置 Clash Verge Rev 固定海外出口、防 DNS/IPv6 泄漏与断网开关）→ 3) claude-browser-env（对齐系统时区/地区/语言与浏览器/指纹浏览器环境），并在最后输出一份端到端的总验收报告。支持 Windows 和 macOS。
---

# Claude ENV Setup — Claude 环境设置指南（总控流程）

本 skill 是 **Claude ENV Setup** 的总入口，用来指导 AI 按严格顺序完成三阶段环境配置，并在末尾输出一份**总验收报告**。

## 整体原则（开始前必读）

1. **严格按顺序执行，不要跳步或颠倒**：
   - **第 1 步：清理本机环境**（`claude-env-cleanup/SKILL.md`）
   - **第 2 步：配置网络与出口**（`claude-network/SKILL.md`）
   - **第 3 步：对齐时区、语言与浏览器**（`claude-browser-env/SKILL.md`）
   - **第 4 步：总验收与收尾汇报**
   - 原因：第 3 步需要读取第 2 步配好的海外出口 IP 与城市来决定时区和语言；第 1 步先清掉旧账号、旧中转站和旧凭据，避免后续登录串号或误走旧代理。
2. **分工边界**：
   - **AI 负责**：只读审计、解释结果、向用户一次性确认选项、生成脚本/计划、复核验收结果。
   - **用户负责**：在真实终端里运行所有写操作与需要管理员/`sudo` 权限的脚本、亲自输入 `CONFIRM` / `RESTORE`、按清单手动配置浏览器或指纹浏览器。
   - **不要自己拼破坏性命令**（如 `rm`、`Remove-Item`、`reg delete`、`pfctl`、`tzutil`、`systemsetup`），全部走各子 skill 随附的脚本。
3. **关键注意事项**：
   - **不要在 Claude Code 自身会话里执行第 1 步写操作**：正在运行 `~/.claude` 的 Claude Code 不能清理自己；检测到处于 Claude Code 中时，只做只读审计，请用户换到普通终端或其它 agent 执行。
   - **深度清理与 skills 目录冲突提醒**：如果用户把本仓库安装在了 `~/.claude/skills/` 下，执行第 1 步 `--mode deep` 会把 `~/.claude/skills/` 一并挪走。请提前提醒用户把本仓库放在独立目录（或先复制到其它 agent 的 skills 目录），或在深度清理后重新放回。
   - **第 2 步会短暂断网**：AI 自身也依赖本机网络，所有会中断代理的操作必须交给 `claude-network` 的后台脚本或用户终端执行，严禁拆成单条命令手工操作。
4. **合规与边界**：
   - 本指南用于清理本机残留、排查网络/DNS/IPv6/WebRTC 泄漏、保持环境一致。
   - 不承诺“绝对不封号”或“恢复已封账号”（封禁记录在服务端，恢复只能走官方申诉）。使用 Claude 请遵守 Anthropic 服务条款与支持地区要求。

---

## 第 0 步：开场摸底（先问清用户现状）

开始前，先快速确认以下信息（合并在一条消息里问，不要来回挤牙膏）：

1. **操作系统**：Windows 还是 macOS？
2. **当前情况**：
   - 之前用过 Claude / 账号被封过，需要彻底重置本机环境 → **从第 1 步开始（建议基础清理或深度清理）**；
   - 全新机器从未装过 Claude → 可先跑一次第 1 步 `--audit` 确认干净，再直接进第 2 步；
   - 只需要修网络或浏览器/时区 → 确认前置条件满足后进入对应步骤。
3. **前置工具确认**：
   - 是否已安装 **Python 3.9+**（Windows `py -3 --version`，macOS `python3 --version`）？
   - 是否已安装 **Clash Verge Rev 2.x** 且导入了可用订阅？（第 2 步仅自动化 Clash Verge Rev）
   - 打算用 **本地浏览器（Chrome/Edge/Firefox 等）** 还是 **指纹浏览器（AdsPower / 比特等）** 登录 Claude？

> 如果用户明确表示不想折腾网络、住宅 IP 和浏览器环境，只需在本地调用 Claude API，可在合适时机提醒：也可以考虑第三方 API 中转方案（见仓库 README 末尾说明）。

---

## 第 1 步：清理本机环境（`claude-env-cleanup`）

> 请先完整阅读 [`claude-env-cleanup/SKILL.md`](claude-env-cleanup/SKILL.md)，严格按其中的步骤执行。

### 执行要点

1. **只读审计**：运行 `claude-env-cleanup/scripts/claude_env_cleanup.py --audit --json`，向用户总结：
   - 生效的配置目录、是否存在旧版 `.config.json`；
   - 凭据位置、历史项目数、MCP、以及 `settings.json` 中的中转站/密钥键名（**绝不复述密钥值**）；
   - `shell_relay_vars`（shell 配置文件或系统环境变量里的旧中转站/API Key，提醒用户**手动删除**，否则清理后仍会走旧中转站）。
2. **选择模式并由用户执行**：
   - **基础清理（`--mode basic`）**：保留 MCP/插件/skills/自定义配置，清理缓存、登录凭据、钥匙串、账号字段、中转站键、会话/项目/记忆，并轮换 `userID`/`machineID`（含历史备份）。
   - **深度清理（`--mode deep`）**：挪走几乎全部配置目录与全局配置，仅按原值保留遥测键，相当于从未用过 Claude Code（**被封号后强烈建议选此项**，并一并选择清理 Claude Desktop 数据）。
   - 用户在**真实终端**里关闭所有 Claude Code / Desktop、运行 `claude daemon stop`，再运行清理脚本并输入 `CONFIRM`。
3. **阶段核对（通过才进入第 2 步）**：
   - AI 再次运行 `--audit --json` 核对：旧登录凭据、旧中转站键已清空，无未处理的“部分完成”告警；
   - 记下本步生成的**备份批次目录**（`~/ClaudeCleanup/<时间戳>/`），供最终报告展示。

---

## 第 2 步：配置网络与固定海外出口（`claude-network`）

> 请先完整阅读 [`claude-network/SKILL.md`](claude-network/SKILL.md) 及按需阅读其 `references/` 文档，严格按其中的 1～7 步执行。

### 执行要点

1. **只读体检与节点测速**：运行 `cnet doctor` 和 `cnet nodes`，先排除所有 `✗` 阻断项（如未装 Clash Verge Rev 2.x、未选订阅等），向用户说明 `?` 告警项（如其他代理软件、Tailscale 接管 DNS、服务模式未装）。
2. **一次性确认方案**：
   - **方案 A（干净机房节点直出）**：节点必须非港澳大陆且出口 IP 固定。
   - **方案 B（静态住宅 IP / ISP SOCKS5 链式代理）**：普通机场 IP 不固定或频繁触发验证时推荐选 B，机场节点仅做「跳板」，最终出口为静态住宅 IP。住宅代理四项信息仅通过 **stdin** 传给 `cnet plan`，绝不写入命令行参数或日志。
3. **备份与断网急救单（强制前置）**：
   - 运行 `cnet backup` 和 `cnet emergency`，把急救单**全文**发给用户，请他用手机拍下来后再继续。
4. **生成计划并后台切换**：
   - 运行 `cnet plan`（内置 `mihomo -t` 校验）→ 展示改动摘要 → 运行 `cnet apply --plan <目录>` → 用 `cnet status --plan <目录>` 确认 `OK`。
5. **DNS / IPv6 加固 + 验收 1～6**：
   - 运行 `cnet harden` 生成管理员脚本，交给用户在终端执行；
   - 运行 `cnet verify --items 1,2,3,4,5,6`，确认 **1～6 全部 `pass`**（`unknown` 不算通过；若第 4 项发现出口 IP 不唯一或在港澳大陆，立即停下来建议改用方案 B）。
6. **断网开关 + 验收 7～8 + 演练 9～11**：
   - 仅在 1～6 全过后，运行 `cnet killswitch`（用户管理员执行）→ `cnet verify --items 7,8` → `cnet drills --plan <目录>`（用户管理员执行）→ `cnet verify` 汇总全部 11 项。
7. **阶段核对（通过才进入第 3 步）**：
   - 1～11 项实测结果已产出；
   - 记下**当前固定海外出口 IP、国家、城市**以及**最早备份目录**与**解除断网开关命令**。

---

## 第 3 步：对齐时区、语言与浏览器环境（`claude-browser-env`）

> 请先完整阅读 [`claude-browser-env/SKILL.md`](claude-browser-env/SKILL.md) 及对应的 `references/` 文档，严格按其中的 1～6 步执行。

### 执行要点

1. **只读审计**：提醒用户先关闭浏览器，运行 `claude-browser-env/scripts/claude_browser_env.py --audit --json`。
   - 确认探测到的直连（TUN）出口已是第 2 步配置的海外出口（若显示为中国大陆，立即回到第 2 步排查，不要往下做）。
2. **确认登录浏览器并处理浏览环境**：
   - **本地浏览器（Chrome / Edge / Firefox / Safari）**：若曾登录过 Claude 或账号被封过，强烈建议**新建独立 Profile**（或彻底清理 `claude.ai` / `claude.com` / `anthropic.com` 站点数据与缓存）；安装 **WebRTC Protect** 扩展（Firefox 修改 `about:config`）；按审计推荐的 `browser_languages` 调整浏览器首选语言顺序。
   - **指纹浏览器（AdsPower / 比特等）**：按 `claude-browser-env/references/fingerprint-browser.md` 逐项核对（真实系统、WebRTC 走 Clash 时必须“禁用”、时区/地理/语言“基于 IP”、关闭多余 Canvas/WebGL/Audio 噪音等）。
   - **默认浏览器提醒**：Claude Code 登录会唤起系统默认浏览器；如果专门用于 Claude 的浏览器/Profile 不是默认浏览器，提醒用户**手动复制终端里的登录链接**到目标浏览器打开。
3. **对齐系统时区、国家/地区与语言**：
   - 检查 `auto_timezone`，若开启则请用户在系统设置中关闭「自动设置时区」；
   - AI 运行 `--plan` 展示改动 → 用户在**真实终端**运行同样参数的 `--apply` 并输入 `CONFIRM`（macOS 需输入开机密码）；
   - 改完后请用户**完全退出并重开浏览器**，并按需注销重登系统使语言/地区完全生效。
4. **阶段核对**：
   - AI 再次运行 `--audit --json`：`recommendation.ops` 为空，目标浏览器 Profile 的 `webrtc` 为 `protected`、`language_ok` 为真；
   - 请用户在目标浏览器访问 <https://browserleaks.com/webrtc> 与 <https://www.browserscan.net> 实测确认无泄漏且时区/语言/IP 一致。

---

## 第 4 步：总验收与最终汇报（必须输出）

全部三步完成后，AI **必须**基于各脚本的实际输出（禁止凭记忆编造或美化），向用户输出以下**总验收报告**：

```markdown
## Claude ENV Setup 总验收报告

### 1. 本机环境清理（claude-env-cleanup）
- 清理模式：基础清理 / 深度清理 / 跳过（全新环境）
- 复检结果：旧凭据与旧中转站配置已清空 / 仍有残留项（列明）
- 手动项确认：shell 配置文件与环境变量中的旧 `ANTHROPIC_*` 已清理（待用户确认/已确认）
- 回滚路径：`~/ClaudeCleanup/<时间戳>/`

### 2. 网络与出口（claude-network）
- 出口方案：A（机房直出）/ B（静态住宅 IP 链式代理）
- 固定出口 IP 与地区：`<IP>`（`<国家/城市>`）
- 11 项实测结果：
  - 1~6（规则/分流/出口唯一性/DNS/TUN）：逐项标注 pass / fail / unknown
  - 7~8（断网开关安装与生效）：pass / fail / unknown
  - 9~11（断线/关 TUN/退客户端故障演练）：pass / fail / unknown
- 一键还原命令：`<最早备份目录的 restore 命令>`
- 解除断网开关命令：`<off.sh / off.cmd 命令>`
- 桌面急救单：已生成并核对路径一致

### 3. 时区、语言与浏览器（claude-browser-env）
- 登录方式：本地浏览器（`<浏览器 / Profile>`）/ 指纹浏览器（`<名称>`）
- 系统时区与地区：`<当前时区>` / `<当前地区>`（与出口城市一致，自动时区已关）
- 系统与浏览器语言：`<语言列表>`（`recommendation.ops` 为空）
- WebRTC 防护：`protected`（本地扩展或指纹浏览器禁用）
- 网站实测（用户确认）：
  - `browserleaks.com/webrtc`：仅见海外出口 IP，无本地宽带 IP 泄漏
  - `browserscan.net`：IP 时区 = 系统时区，语言一致，无 DNS/WebRTC 泄漏
- 回滚路径：`~/ClaudeBrowserEnv/<时间戳>/`

### 4. 日常使用提醒
- 始终只使用已配置的专用浏览器 / Profile 或指纹浏览器访问 `claude.ai` 与对应邮箱。
- 使用 Claude Code 登录时，若默认浏览器不是专用浏览器，请手动复制终端里的授权链接过去打开。
- 切换网络或遇到断网时，优先查看桌面「断网急救.txt」，恢复后回到对话先查现状再操作。
```

只有当以上三部分均无 `fail` 且关键手工项已由用户确认时，才可给出**「总验收通过，可以开始正常登录使用 Claude」**的结论；否则明确标出未通过项与下一步修复动作。
