---
name: claude-network
description: 在 Windows 或 macOS 上把 Clash Verge Rev 配成「国内直连、其余全走一个固定海外出口、断了也不漏」，专门处理 Claude 的海外出口、DNS 泄漏与 IPv6 旁路，并装断网开关、写断网急救单、一键还原和 11 项实测验收。用于：Claude 出口 IP 不固定或在港澳大陆、DNS 泄漏、IPv6 泄漏、想让终端里的 Claude Code / curl / git 和浏览器走同一个出口、代理断了不想漏回本地 IP；也用于在「干净机房节点直接当出口（A）」和「买静态住宅 IP 做链式代理（B）」之间做选择。不用于规避平台风控或违反 Anthropic 服务条款。
---

# claude-network：Claude 海外出口 / DNS / IPv6 / 断网开关

你直接在用户这台电脑上动手，但**所有会断网的操作都交给脚本一次跑完**，所有要管理员权限的操作都交给用户在终端里自己跑。
随附脚本在 `scripts/`（下文 `cnet` 指 `python3 scripts/cnet.py`，Windows 用 `python scripts\cnet.py`；路径相对本 skill 目录）。
不要临时拼命令改代理、DNS、防火墙、pf；脚本里已经处理了备份、自检和失败自动还原。

先读 [references/exit-guide.md](references/exit-guide.md)（A/B 怎么选、住宅 IP 怎么买），遇到客户端细节读 [references/clash-verge.md](references/clash-verge.md)，规则/DNS/IPv6/断网开关的原理读 [references/design.md](references/design.md)，验收方法读 [references/acceptance.md](references/acceptance.md)。

## 0. 你自己也靠这个代理联网

你和模型的通信走的也是这台电脑的代理。代理一停，你就收不到下一步，活会停在半路。所以：

- 会让代理中断的操作：退出/重启客户端、改完配置重开、开关虚拟网卡、装卸服务模式、装断网开关、切换海外出口节点、三个演练、还原。**它们不准拆成几步由你一条条执行**，只能用随附脚本一次跑完。
- 不要 sudo/管理员的（`cnet apply`）：它自己用 nohup（macOS）或一次性计划任务（Windows）放到后台跑，AI 断线也会跑完、失败自动还原，结束时网一定是通的。跑完用 `cnet status --plan <目录>` 读日志汇报，**不凭记忆说**。
- 要 sudo/管理员的（harden / killswitch / drills / restore）：只生成脚本并打印一条命令，交给用户在「终端」App（Windows：以管理员身份运行的 PowerShell）里跑。**不要问用户要密码**。发命令之前，`cnet` 已经做完不需要权限的预检（语法、占位符、macOS 上命令路径是否存在）；预检没过就别发。
- 让用户做事之前（装服务模式、在终端跑命令），先说清楚他要做什么、做完回你什么，再等他。
- 断网开关装上以后，你每改一次配置、重启一次客户端，自己都会断一下。所以顺序固定：先把配置全部改完 → 验收 1~6 通过 → 再装断网开关 → 最后演练。

## 1. 只读体检（不改任何东西）

```bash
cnet doctor          # 客户端、服务模式、开关、扩展冲突、其他代理、Tailscale、防火墙/pf、当前出口与 DNS 出口
cnet nodes           # 真节点按延迟排序，标出只能当跳板的（香港/澳门/大陆），剔除「剩余流量/到期/官网」条目
```

- `✗` 必须先解决：没装 Clash Verge Rev（本 skill 只自动化它）、不是 2.x、没选订阅、macOS 的 `/etc/pf.conf` 缺 `anchor "com.apple/*"`。
- `?` 先问用户：别的代理/VPN 程序在跑；Tailscale 接管了系统 DNS（首选 DNS 100.100.100.100，查询会绕过代理）；服务模式没装（开虚拟网卡必须先装）；全局 Merge / Script 或当前订阅的增强文件不是默认内容（本 skill 会整份替换全局 Script.js，旧内容在备份里）；Windows 防火墙被关（断网开关装不了）；当前 DNS 出口在中国大陆（就是要修的 DNS 泄漏，告诉用户即可）。
- 用户用的是 Mihomo Party / Surge / sing-box / ClashX / v2rayN 等：**没做**，原因是本 skill 只对 Clash Verge Rev 的文件布局和行为做过核对；建议用户换成 Clash Verge Rev（Windows、macOS 都有官方版），或者只按 [references/design.md](references/design.md) 自己照抄规则。

## 2. 一次问完，用户同意了才动手

把下面三件事放在**同一条消息**里问：

1. **出口选 A 还是 B**（用 [references/exit-guide.md](references/exit-guide.md) 的话讲清楚）：
   - **A：直接用一个海外节点当出口。** 前提是这个节点是干净的机房节点，而且**出口 IP 固定**。香港、澳门、大陆节点不能当出口（Claude 在这些地区不提供服务）。很多梯子同一个节点名背后是多台机器或会换 IP，这种不行——验收第 4 项会发现「出口 IP 不唯一」。
   - **B：买静态住宅 IP（家宽 / ISP 代理，SOCKS5：IP、端口、账号、密码四样），做链式代理。** 国内直连不上住宅代理，所以让它经一个高速节点（「跳板」，香港的也行）连出去；「海外出口」里只放这个住宅代理。机场节点 IP 不固定、被标记为机房/代理、或者 Claude 经常要验证时，就该走 B。要说清楚：住宅 IP 要用户自己去供应商网站买，要「静态 / Static Residential / ISP」，不要「动态 / Rotating」，要支持 SOCKS5 和账号密码认证。
2. **用哪个节点**：把 `cnet nodes` 的结果按延迟列给用户，标出哪些只能当跳板、哪些不通。A 选一个能通的非港澳大陆节点；B 选一个能通的节点当默认跳板（其余能通的节点也留在「跳板」组里，方便以后换）。
3. **打算改哪些东西**（照实列）：
   - 先完整备份客户端配置目录到 `~/clash-guard-backup/日期时间/`，里面有一键还原脚本；桌面写「断网急救.txt」。
   - 替换客户端的三个文件（订阅文件不动）：全局扩展 `profiles/Script.js`（规则、DNS、代理组）、`config.yaml`（IPv6 关、规则模式、TUN 参数）、`verge.yaml`（开虚拟网卡、开开机自启、关系统代理、关「DNS 覆写」）。
   - DNS / IPv6 加固（要管理员）：macOS 把各网络服务的 DNS 改成 223.5.5.5 / 119.29.29.29 并关 IPv6；Windows 关物理网卡的 IPv6、关「智能多宿主名称解析」。
   - 装断网开关（要管理员）：macOS 用 pf 锚点 `com.apple/clash-guard` + launchd `local.clash-guard`，文件在 `/Library/Application Support/clash-guard/`；Windows 用防火墙规则组 `clash-guard` + 默认出站阻止 + 计划任务 `clash-guard-killswitch`，文件在 `C:\ProgramData\clash-guard\`。
   - 三个故障演练（要管理员），每个结束自动恢复。

B 方案的住宅代理四项由用户在对话里给你（先提醒：这是住宅代理的连接信息，不是网站登录密码；对话内容会被 AI 服务商处理）。只经**标准输入**传给 `cnet plan`，不要放进命令参数、不要回显、不要写进任何日志。

## 3. 备份 → 急救单（备份没做完，不改任何东西）

```bash
cnet backup          # 完整复制客户端配置目录并逐个校验；写 state.json（DNS/IPv6/系统代理原值）和 restore 脚本；最后才写 BACKUP_OK
cnet emergency       # 按这台机器的实际路径和网络服务名在桌面写「断网急救.txt」，并打印全文
```

把 `emergency` 打印的**全文**发给用户，请他用手机拍下来。它包含：① 先打开客户端等一分钟；② 解除断网开关（off）和一键还原（restore，多个备份时用最早那个）的实际命令；③ 这两个文件不存在时的兜底命令；④ 用实际网络服务名关系统代理；⑤ 网通后回到这个对话说「网断过，先查现状再继续」。以后每次新做备份都重新跑一次 `emergency`，保证桌面文件和最终路径一致。

## 4. 生成配置并切换（会断网几十秒，后台自愈）

```bash
cnet plan --mode A --exit "<节点名>"
printf '%s' '{"server":"<IP>","port":<端口>,"username":"<账号>","password":"<密码>"}' | cnet plan --mode B --jump "<跳板节点名>" --residential-stdin
```

Windows 上用 PowerShell 管道时先设 `$OutputEncoding = New-Object Text.UTF8Encoding $false`（否则 5.1 会把非 ASCII 字符变成问号），或用 Python 子进程写标准输入；同样不要进命令行参数。`plan` 只写计划目录 `~/claude-network/plans/<时间>/`，并用客户端自带内核 `mihomo -t` 校验；没通过就停。把输出的改动摘要给用户看一眼，然后：

```bash
cnet apply --plan <计划目录>     # 后台：退出客户端 → 换 3 个文件 → 重开 → 等端口 → 测速确认节点通 → 选节点 → 2 分钟内普通用户连不上 claude.ai 就自动还原
cnet status --plan <计划目录>    # 过一两分钟读结果：OK / ROLLED_BACK / ABORTED
```

- 切节点之前脚本会先用客户端测速接口确认节点真的通（海外出口里没有直连，选了死节点等于海外全断）。
- 重载后同一个域名的 fake-ip 会变，系统缓存的旧地址短暂连不上是正常的；等 claude.ai 恢复了再往下做，不要去修它。
- 结果是 ROLLED_BACK：照日志里的原因告诉用户（常见：节点不通、住宅代理账号/白名单/到期），改完重新 `plan` → `apply`。

## 5. DNS / IPv6 加固（管理员）→ 验收 1~6

```bash
cnet harden          # 生成脚本并打印命令，交给用户在终端里跑；60 秒内 claude.ai 不通会自动改回
cnet verify --items 1,2,3,4,5,6
```

`verify` 全部以普通用户身份发请求（macOS 不要加 sudo：root 不受断网开关限制，测不准），每条 curl 清掉代理变量并加 `--noproxy '*'`，同时录下核心的连接日志，用它证明每个请求命中哪条规则、走哪个出口。`pass` 有直接证据；`fail` 明确不满足；`unknown` 是证据不足，**不能当成通过**。第 4 项报「出口 IP 不唯一」或出口在港澳大陆、ip-api 标记 hosting/proxy 且 Claude 频繁要求验证时，回到第 2 步建议用户改用 B。

1~6 不全过，不要装断网开关。

## 6. 断网开关（管理员）→ 验收 7、8 → 演练 9~11（管理员）

```bash
cnet killswitch      # 生成安装脚本：先确认虚拟网卡真的起来、claude.ai 通才装；装完 60 秒内不通自动解除
cnet verify --items 7,8
cnet drills --plan <计划目录>   # 三个演练写在一个脚本里，每个结束先自动恢复，确认 claude.ai 通了再做下一个
cnet verify          # 汇总 1~8，并读出演练 9~11 的结果
```

## 7. 收尾汇报

按 1~11 项列出实测结果（照报告原文，不美化），并给出：

- 一键还原：macOS `sudo sh "<最早备份目录>/restore.sh"`；Windows 右键 `<最早备份目录>\restore.cmd` →「以管理员身份运行」。
- 解除断网开关：macOS `sudo sh "/Library/Application Support/clash-guard/off.sh"`；Windows 右键 `C:\ProgramData\clash-guard\off.cmd` →「以管理员身份运行」。
- 再跑一次 `cnet doctor`，确认此刻客户端开着、系统代理关着、DNS 设置正常、桌面「断网急救.txt」里的路径和名字与上面一致。

## 用户回来说「网断过」

先 `cnet doctor`，再读最近计划目录的 `switch.log` / `~/claude-network/*.log` 和备份目录里的 `restore-*.log`，弄清停在哪一步、现在是什么状态，再决定下一步。不要新开流程从头来，不要临时拼命令修网络。

## 边界

- 名字固定，不要改：`~/clash-guard-backup/`、`/Library/Application Support/clash-guard/`、`com.apple/clash-guard`、`local.clash-guard`、`C:\ProgramData\clash-guard\`、`clash-guard-killswitch`、代理组「海外出口」「跳板」。用户手上的急救命令按这些名字写。
- macOS 的断网开关不改 `/etc/pf.conf`；代理核心以 root 运行，所以规则只拦非 root 程序。Windows 版是防火墙规则 + 计划任务（每 5 分钟补回），不是 WFP 持久过滤器。
- 不承诺「零泄漏」「一定能用 Claude」。本 skill 只管本机网络；使用 Claude 须遵守 Anthropic 服务条款与支持地区要求。
