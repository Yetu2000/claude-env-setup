# claude-network

从 clash-guard 拆出来的独立 skill：把一台 **Windows 或 macOS** 电脑上的 Clash Verge Rev 配成

> **国内直连、其余全走一个固定的海外出口、断了也不漏。**

专门处理 Claude 的三个老问题：出口 IP 不固定 / 在港澳大陆、DNS 泄漏、IPv6 绕过代理。终端里的 Claude Code、curl、git 和浏览器走同一个出口。

## 两种出口

- **A：干净的机房节点直接当出口**，只要出口 IP 固定。香港、澳门、大陆节点不行。
- **B：买静态住宅 IP（家宽 / ISP，SOCKS5）做链式代理**。很多梯子节点的出口 IP 不固定或名声差，这种就要走 B：机场节点只当「跳板」，最终出口是住宅 IP。

怎么选、住宅 IP 怎么买，见 [references/exit-guide.md](references/exit-guide.md)。

## 它会做什么

1. 只读体检（客户端、服务模式、DNS 覆写/IPv6 开关、其他代理、Tailscale、防火墙、当前出口和 DNS 出口）。
2. 一次问完：A 还是 B、用哪个节点、要改哪些东西。
3. 完整备份客户端配置目录到 `~/clash-guard-backup/<时间>/`，生成一键还原脚本；桌面写「断网急救.txt」。
4. 只改客户端的全局扩展和开关（订阅文件不动），后台切换，2 分钟内连不上 claude.ai 自动还原。
5. DNS / IPv6 加固、断网开关、三个故障演练——都是脚本，由你本人在终端里用管理员权限运行。
6. 11 项实测验收，用代理核心的连接日志证明每个请求走了哪条规则、哪个出口。

## 安装

把整个 `claude-network/` 目录复制到你的 agent 的 skills 目录（例如 Claude Code 的 `~/.claude/skills/claude-network/`），然后对 AI 说：

> 用 claude-network skill，帮我把这台电脑的代理配成国内直连、其余全走海外、断了也不漏。

需要：Python 3.8+（macOS 自带的命令行工具里有 `python3`；Windows 没有的话 `winget install Python.Python.3.12`）、Clash Verge Rev 2.x 且已导入订阅。

## 命令一览（AI 用）

| 命令 | 作用 | 会改系统吗 |
|---|---|---|
| `doctor` / `nodes` / `verify` / `status` / `precheck` | 体检、列节点测速、验收、读结果、脚本预检 | 不会 |
| `backup` / `emergency` / `plan` | 备份、写急救单、生成新配置到计划目录 | 只写备份/计划目录和桌面急救单 |
| `apply` | 后台切换到新配置（失败自动还原） | 会，短暂断网 |
| `harden` / `killswitch` / `drills` | 生成需要管理员的脚本并打印命令 | 只生成，由你运行 |

## 测试

```bash
python -m unittest discover -s tests -v   # 离线回归；有 node / 客户端内核 / dash 时会额外执行脚本、mihomo -t 与 sh -n
python tests/live_readonly_ps.py          # Windows：对正在运行的客户端做只读冒烟（只发 GET 与测速）
```

## 验证状态（如实说明）

- 已验证（Windows 11 + Clash Verge Rev 2.5.6 / verge-mihomo v1.19.31，服务模式）：doctor、nodes、plan（真实内核 `mihomo -t` 通过 A/B）、backup、emergency、verify 1~6 的取证流程（命名管道日志流与连接快照）、所有 PowerShell 脚本语法与公共函数（只读调用）；生成的 Script.js 在 Node 里执行结果与 Python 模拟一致；macOS 脚本通过 `dash -n`。
- **尚未在真机执行**：apply 切换、harden、断网开关安装、演练 9~11、restore；macOS 全部写入类操作。请先在一次性虚拟机或备用机上跑一遍完整流程。
- 不承诺「零泄漏」或「一定能用 Claude」。使用 Claude 须遵守 Anthropic 服务条款与支持地区要求。
