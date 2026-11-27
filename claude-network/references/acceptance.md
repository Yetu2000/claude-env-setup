# 11 项实测验收

全部用普通用户身份测（macOS 不加 sudo：root 不受断网开关限制）。每条 curl 清掉代理环境变量并加 `--noproxy '*'`，测的是虚拟网卡，不是代理端口。`cnet verify` 同时录下代理核心的连接日志（`/logs` 流 + 每 300ms 一次 `/connections` 快照），用它证明每个请求命中哪条规则、走哪个出口。

| # | 内容 | 怎么测 | 命令 |
|---|---|---|---|
| 1 | 规则模式、虚拟网卡开、开机自启已开启、DNS 的 IPv6 关 | `/configs` 的 mode 与 tun.enable；运行配置 dns.ipv6；verge.yaml 开关 + 系统里真的登记了开机启动项；DNS 覆写关 | `cnet verify --items 1` |
| 2 | 兜底指向海外出口；指向直连的只有规定几类；海外出口组没有 DIRECT | `/rules` 逐条核对；`/proxies/海外出口` 成员与类型；两个规则集已加载（条数 > 0） | `--items 2` |
| 3 | baidu.com、bilibili.com 能开并直连；myip.ipip.net 显示本地国内 IP | curl + 日志里命中 DIRECT | `--items 3` |
| 4 | api.ipify.org、ipinfo.io、claude.ai、statsig.anthropic.com、httpbin.org、googletagmanager.com 全走海外出口，IP 是同一个海外地址，不是港澳大陆 | 四个地方取出口 IP 比较；claude.ai `/cdn-cgi/trace` 的 loc；日志里全部 `海外出口[...]`；附 ip-api.com 的 hosting/proxy 参考 | `--items 4` |
| 5 | DNS 泄漏 | ① 系统解析 3 个随机域名必须拿到 fake-ip（198.18.x）；② 3 次请求 edns.ip-api.com（每次被重定向到一个随机子域名），这 3 个域名必须出现在核心日志里并走海外出口，且解析者不在中国大陆；③ 向 Cloudflare DoH 查随机域名，这条连接走海外出口；④ 调 isp.ac 的 DNS 泄漏测试接口，拿不到 JSON 时如实记为「未验证此子项」 | `--items 5` |
| 6 | 终端直连 IP 也走虚拟网卡 | curl `https://1.1.1.1/cdn-cgi/trace`，日志里能看到 1.1.1.1 走海外出口 | `--items 6` |
| 7 | 装断网开关的脚本 0 错误 | 读 `~/claude-network/killswitch-install.log` 的「错误数」 | `--items 7` |
| 8 | 防火墙规则挂上了、开机服务在运行 | macOS：launchd `local.clash-guard` running + 安装日志里的锚点规则（实时规则要 `sudo pfctl -a com.apple/clash-guard -sr`）；Windows：规则组条数、三个配置文件默认出站 Block、计划任务在 | `--items 8` |
| 9 | 海外出口切到连不上的节点：claude.ai、api.ipify.org 必须失败（不能换节点、不能变成本地国内 IP），baidu.com 照常 | drills 脚本切到「演练-断线(勿选)」，测完切回 | `cnet drills` |
| 10 | 关掉虚拟网卡：curl 任何外网都被拒（exit 7 或 28） | drills 脚本 `PATCH /configs {"tun":{"enable":false}}`，测完开回 | 同上 |
| 11 | 退出客户端等 3 秒：curl 任何外网都被拒；重开后 claude.ai 恢复 | drills 脚本退出客户端并结束内核，测完重开 | 同上 |

报告里的三种结论：`pass`（有直接证据）、`fail`（明确不满足）、`unknown`（证据不足，**不能当通过**）。每次 `verify` 的完整报告保存在 `~/claude-network/verify-<时间>.json`。
