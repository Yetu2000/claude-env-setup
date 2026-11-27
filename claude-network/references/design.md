# 设计：规则、DNS、IPv6、断网开关

## 规则顺序（从上到下，先命中先执行）

| # | 规则 | 去向 | 为什么 |
|---|---|---|---|
| 1 | `DOMAIN-SUFFIX` claude.ai、claude.com、anthropic.com、claudeusercontent.com、claudemcpclient.com | 海外出口 | 排最前，任何名单都不能把 Claude 分到直连 |
| 1b | （B 方案）住宅代理自己的 IP/32（或域名） | 跳板 | 不然访问住宅地址会被兜底送进住宅出口，绕成死循环 |
| 2 | 局域网与保留地址：10/8、172.16/12、192.168/16、127/8、169.254/16、**100.64/10**、224/4、::1、fc00::/7、fe80::/10、ff00::/8；`.lan/.local/.localdomain/.home.arpa` | DIRECT | IP 规则全部 `no-resolve` |
| 3 | B 站（含 `DOMAIN-KEYWORD,mirrorakam`：B 站海外 CDN 按后缀匹配不上）、微信（域名 + 进程名） | DIRECT | |
| 4 | `GEOSITE,geolocation-!cn` | 海外出口 | **必须排在国内名单前面**：direct.txt 里混着几百条海外公司域名（googletagmanager、doubleclick 这类），不先拦走，Claude 网页调它们时就用国内 IP 出去了 |
| 5 | `RULE-SET,direct-domain`（Loyalsoldier direct.txt）、`RULE-SET,china-ip,no-resolve`（cncidr.txt） | DIRECT | 每天自动更新（interval 86400），下载经海外出口（B 方案经跳板）。IP 规则不为匹配去本地解析域名 |
| 6 | `MATCH` | 海外出口 | 兜底不允许 DIRECT |

指向 DIRECT 的只有 2、3、5 这几类。verify 第 2 项会逐条核对。

**UDP 兜底（每条海外规则后面都跟一条同条件的 REJECT，最后是 `MATCH,REJECT`）**：mihomo 匹配到的出口不支持 UDP 时（住宅 SOCKS5 关了 UDP、演练节点、选中的节点本身不支持），会跳过这条规则继续往下找，最后落到 `DIRECT`（见 mihomo `tunnel/tunnel.go` 的 `match()`）——海外的 QUIC、WebRTC 就会从本地 IP 直接出去。加了 REJECT 后 TCP 永远停在前一条，只有被跳过的 UDP 落到 REJECT；浏览器的 QUIC 被拒后自动回落到 TCP。

**组的失效方向**：「海外出口」与「跳板」组末尾都有「演练-断线(勿选)」。引用的节点改名或消失时组里只剩它 → 断网；不会变成空组（mihomo 会用 COMPATIBLE 顶上，等于直连）。

## DNS

- `enhanced-mode: fake-ip`，`ipv6: false`（不返回 AAAA），`respect-rules: true`（DNS 连接本身也按规则走）。
- `nameserver`：`https://1.1.1.1/dns-query`、`https://8.8.8.8/dns-query`。它们命中兜底 → 经海外出口解析海外域名。
- `nameserver-policy`：`rule-set:direct-domain`、B 站/微信域名、Windows 连通性检测域名 → 223.5.5.5 / 119.29.29.29。
- `proxy-server-nameserver`、`direct-nameserver`、`default-nameserver`：223.5.5.5 / 119.29.29.29。节点自己的域名和走直连的国内域名用国内 DNS 解析，所以国内网站拿到国内线路，海外节点挂了也照常能开。
- 为什么 IP 规则必须 `no-resolve`：只要规则链里有一条不带 `no-resolve` 的 IP 规则（比如常见的 `GEOIP,CN`），核心就会为了匹配它**先在本地把域名解析一遍**——海外域名的查询就从本地 DNS 漏出去了。
- fake-ip：重载配置后同一个域名的 fake-ip 会变，系统缓存的旧地址短暂连不上是正常的。

### 系统层面的 DNS 旁路（harden）

- **macOS**：虚拟网卡只接管默认路由。系统 DNS 若是路由器（局域网地址），查询走局域网路由直接从物理网卡出去，不进虚拟网卡。改成 223.5.5.5 / 119.29.29.29 后查询走默认路由进虚拟网卡、被 `dns-hijack` 接管；虚拟网卡不在时由断网开关拦住。
- **Windows**：TUN 开 `strict-route`（阻止从其他网卡发 DNS）；再关掉「智能多宿主名称解析」（`DisableSmartNameResolution=1`，否则系统会同时问所有网卡的 DNS）和 A/AAAA 并行查询。

## IPv6

三层：核心 `ipv6: false` + DNS 不返回 AAAA；系统层关掉物理网卡的 IPv6（macOS `networksetup -setv6off`，手动配置的 IPv6 不动；Windows 关 `ms_tcpip6` 绑定）；断网开关对物理网卡上的 IPv6 TCP/UDP 一律拦截。备份时记录原值，restore 原样改回。

## 断网开关

### macOS（pf）

锚点 `com.apple/clash-guard`（不改 `/etc/pf.conf`，主规则集里必须有 `anchor "com.apple/*"`），规则：

```
pass quick on lo0 all
pass quick on <utun：带 198.18.x 地址的那个>          # 虚拟网卡不在时省略这条
block return quick out on { enN } inet  proto { tcp udp } to <局域网段> port 53 user != root
block return quick out on { enN } inet6 proto { tcp udp } to <局域网段> port 53 user != root
pass  quick out on { enN } inet  to <局域网段>
pass  quick out on { enN } inet6 to <局域网段>
block return quick out on { enN } inet  proto { tcp udp } all user != root
block return quick out on { enN } inet6 proto { tcp udp } all user != root
```

代理核心以 root 运行（服务模式），所以只拦非 root 程序；`return` 让程序立即报错（curl exit 7）。launchd `local.clash-guard`（KeepAlive）的 watch.sh 每 5 秒检查：pf 被关、锚点被清、虚拟网卡或物理网卡变化，就重新加载。用 `pfctl -E` 的引用令牌，别的程序 `pfctl -d` 关不掉。主规则集里缺 `anchor "com.apple/*"`（开机时没加载 pf.conf）时，watch.sh 会 `pfctl -f /etc/pf.conf` 加载一次（只读取，不改文件）。

系统解析器 mDNSResponder 不是 root，受规则约束。加固之后新接入的网络（iPhone 热点、新 USB 网卡）用的是 DHCP 下发的局域网 DNS，会被「局域网 53 端口拦截」挡住、整机解析失败。所以 watch.sh 在网络变化时把这类服务的 DNS 改成 223.5.5.5 / 119.29.29.29，记在 `dns-auto.txt`，off.sh 解除时改回 DHCP。

已知限制：pf 的 user 条件只对 TCP/UDP 生效，ICMP 不受控；root 进程不受限；只拦 enN（ppp、ipsec、bridge、其他 utun 不在范围）。

### Windows（防火墙）

规则组 `clash-guard` + 三个配置文件默认出站 Block。放行：回环、**本地地址 198.18.0.0/15**（虚拟网卡，不依赖网卡别名，网卡重建也有效）、DHCP、局域网、IPv6 邻居发现、代理程序（服务与内核，按安装时记录的绝对路径；客户端界面本身不放行，它不经代理拉订阅的流量也该被拦）。每 5 分钟的补回若有规则没补成功、或一条代理程序放行都没有，就**不去设**默认出站 Block——宁可暂时不拦，也不能把代理核心自己拦住。局域网 53 端口单独拦截（Windows 防火墙里拦截规则优先于放行规则）。计划任务 `clash-guard-killswitch`（SYSTEM）开机与每 5 分钟逐条比对补回。防火墙按程序和地址判定，不区分管理员与普通用户。

已知限制：不是 WFP 持久过滤器，规则被删后最多约 5 分钟补回；第三方安全软件接管防火墙时无效（安装前检查三个配置文件都启用）。
