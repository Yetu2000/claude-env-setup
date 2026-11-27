# Clash Verge Rev（2.x）：文件、接口与坑

依据：clash-verge-rev 2.x 的实际文件布局。

## 文件

| 文件 | 作用 | 本 skill 怎么用 |
|---|---|---|
| `verge.yaml` | 客户端开关 | 改 `enable_tun_mode: true`、`enable_auto_launch: true`、`enable_system_proxy: false`、`enable_dns_settings: false`、`profile_dns_settings.*.enabled: false` |
| `config.yaml` | 客户端的 clash 基础配置；生成运行配置时 `ipv6`、`mode`、`log-level`、`tun` 等键会**盖掉**订阅与扩展里的同名键 | 改 `ipv6: false`、`mode: rule`、`log-level: info`、`allow-lan: false`、`tun.stack/auto-route/auto-detect-interface/strict-route/dns-hijack`（`tun.enable` 由 verge.yaml 控制） |
| `profiles.yaml` | 订阅索引；`uid: Merge` / `uid: Script` 是全局扩展 | 只读：确认当前订阅、找订阅自带的增强文件 |
| `profiles/Script.js` | 全局扩展脚本，`main(config, profileName)` | **整份替换**成本 skill 生成的脚本（规则、DNS、代理组、住宅节点） |
| `profiles/Merge.yaml` | 全局 Merge（deep merge，序列整段替换） | 不动；含 proxies/rules/dns 等键时 doctor 会提醒 |
| `clash-verge.yaml` | 最近一次生成的运行配置 | 只读：核对 dns.ipv6 等 |

目录：Windows `%APPDATA%\io.github.clash-verge-rev.clash-verge-rev`，macOS `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev`。

## 为什么放在全局 Script.js

- 订阅文件不动，订阅更新冲不掉。
- 全局 Merge 写 `proxies:` 会把订阅节点整段替换掉（实测 `mihomo -t` 报引用节点 not found），住宅节点只能靠脚本前插。
- 规则、DNS、代理组由脚本整段赋值，结果确定。订阅自带的 rule-providers 也一并去掉（规则已不再引用它们）。
- 订阅**自己**的增强（profile 级 merge/script/rules/proxies/groups）在全局扩展之后执行，可能再改一遍；doctor 会把非默认的列出来。

## 控制器

- Windows：命名管道 `\\.\pipe\verge-mihomo-*`。服务模式下实际管道名和 config.yaml 里写的不一样（`sidecar-release-<hash>` vs `production-<hash>`），按前缀枚举、优先 production。Windows PowerShell 5.1 里 `[IO.Directory]::GetFiles('\\.\pipe\')` 枚举不到，要用 `Get-ChildItem \\.\pipe\`。
- macOS：Unix socket `/tmp/verge/verge-mihomo*.sock`，`curl --unix-socket` 即可。
- 用到的接口：`GET /version /configs /proxies /rules /providers/rules /connections /logs`、`GET /proxies/<名>/delay`（测速）、`PUT /proxies/<组>`（选节点）、`PATCH /configs {"tun":{"enable":false}}`（演练 10）、`PUT /configs?force=true`（端口起不来时重载一次）。

## 坑

1. **开虚拟网卡要先装服务模式**：设置 → 系统设置 → 服务模式 → 安装，会弹系统密码框，只能用户自己点。doctor 报「服务模式未安装」时先让用户装，装好回复你。
2. **「DNS 覆写」和「IPv6」两个开关会盖掉脚本写的 DNS**：verge.yaml 的 `enable_dns_settings`（以及每个订阅单独的 `profile_dns_settings`）、config.yaml 的 `ipv6`，plan 都会关掉。
3. **改文件前先退出客户端**：它运行时会把内存里的配置写回磁盘，开着改会被冲掉。switch 脚本固定「退出 → 替换 → 重开」。
4. **刚重启时代理端口可能几十秒起不来**：脚本等待；过半时间还不行就 `PUT /configs?force=true` 让核心重载一次。
5. **卸载服务模式后 /tmp/verge 会残留 sock 和 pid**（macOS），客户端再打开时核心起不来：服务没加载时脚本会清掉。
6. **订阅的扩展文件可能只有注释**，读出来是空的，别当成字典用（`yamlmini.is_effectively_empty_yaml`）。
7. **客户端被强行退出时，系统代理可能还指着它的端口**：还原、演练和急救单都会顺手把系统代理关掉。
8. **开机自启**：verge.yaml 的开关只是意图，真正的登记在 Windows 的 Run 注册表 / 启动文件夹 / 计划任务「Clash Verge (Admin)」，macOS 的 LaunchAgents 或「登录项」。verify 第 1 项会去这些地方找；找不到就请用户在客户端设置里把「开机自启」关掉再打开一次（这个操作不断网）。
9. **Windows 上客户端没有命令行退出**：脚本直接结束进程（不会写回内存配置，正好）；服务模式下的内核由服务托管，客户端重开时会用新配置重启它。管理员脚本里重开客户端用 `explorer.exe <路径>` 转交，避免客户端以管理员身份运行。
10. **Windows 提权要用同一个账号**：管理员脚本里关系统代理、还原代理设置、用 explorer 重开客户端，作用的都是「当前提权账号」的用户配置。如果用户是普通账号、提权时输入的是另一个管理员账号的密码，这几步会作用到那个管理员账号上。遇到这种情况先告诉用户，让他用自己的账号（需在管理员组）运行。
