# 时区与语言：对齐规则与脚本做了什么

## 规则

| 项 | 目标 | 说明 |
|---|---|---|
| 系统时区 | 出口 IP 所在城市的时区 | 取 ipinfo.io（备用 ipapi.co）返回的 `timezone`，按城市而不是按国家：洛杉矶 → America/Los_Angeles，纽约 → America/New_York，新加坡 → Asia/Singapore |
| 国家或地区 | 出口国家 | Windows「时间和语言 → 语言和区域 → 国家或地区」；macOS「地区」 |
| 系统语言列表 | 中文 + 出口地区的英语 | 默认中文第一；`--english-first` 时英语第一 |
| 浏览器语言 | 与系统语言列表同序 | 每个带地区的标签后补上基础语言，例如 `zh-CN, zh, en-SG, en` |
| 自动设置时区 | 关闭 | 开着会被系统按定位改回去 |
| 区域格式、显示语言 | 不动 | 中文日期格式和中文界面都可以保留 |

英语标签：美国 en-US、英国 en-GB、加拿大 en-CA、澳大利亚 en-AU、新西兰 en-NZ、爱尔兰 en-IE、新加坡 en-SG、马来西亚 en-MY、中国香港 en-HK、印度 en-IN、菲律宾 en-PH、南非 en-ZA，其他地区用 en-US（系统里已经有任何英语时直接保留）。

新加坡、马来西亚、中国港澳台：中文排第一很自然，不建议 `--english-first`。

出口选择：脚本同时测直连（TUN）和系统代理两条路径，优先用直连的结果（Claude Code、Claude Desktop 和浏览器都会经过它）。两条路径出口不一致时会提示，应回 Clash 让它们走同一个节点。探测到的城市和你买的 IP 不符时，用 `--country` / `--timezone` 指定。

## 脚本执行的命令

### Windows（不需要管理员）

| 项 | 命令 | 回滚 |
|---|---|---|
| 时区 | `tzutil /s "<Windows 时区 ID>"`，再用 `tzutil /g` 核对 | `tzutil /s "<原时区>"` |
| 国家或地区 | `Set-WinHomeLocation -GeoId <GeoID>` | 设回原 GeoID |
| 语言列表 | 重排 `Get-WinUserLanguageList`：已有的语言原样复用（保留输入法），缺的才新建，然后 `Set-WinUserLanguageList -Force` | 按备份顺序还原，原有输入法一并恢复 |
| 显示语言锁定（仅 `--english-first` 且原本没有锁定时） | `Set-WinUILanguageOverride -Language <当前显示语言>` | `Set-WinUILanguageOverride`（取消锁定） |

GeoID 用 `[Globalization.RegionInfo]::new('<国家代码>').GeoId` 核对过，不是 ISO 数字代码：新加坡 215、日本 122、美国 244、英国 242、德国 94、中国香港 104、中国台湾 237。

Windows 时区 ID 来自 CLDR windowsZones 的常用子集，表里每一项都按 1 月和 7 月的 UTC 偏移与系统时区数据核对过。表里没有的时区，用 `tzutil /l` 查到 ID 后加 `--windows-timezone` 指定。

### macOS

| 项 | 命令 | 回滚 |
|---|---|---|
| 时区 | `sudo systemsetup -settimezone <IANA>`（会要求输入开机密码） | 设回原时区 |
| 地区 | `defaults write -g AppleLocale -string <语言>_<国家>`，例如 zh_CN → zh_SG | 写回原值；原本没有则删除该键 |
| 首选语言 | `defaults write -g AppleLanguages -array ...` | 写回原值；原本没有则删除该键 |

`systemsetup` 在部分系统版本上要给终端「完全磁盘访问权限」才能执行；失败时脚本会如实报告，可以改为手动设置。

## 手动设置路径

- **Windows 11**：设置 → 时间和语言 → 日期和时间：关闭「自动设置时区」，选择时区；语言和区域：「国家或地区」选出口国家，「首选语言」里添加英语并调整顺序。
- **Windows 10**：设置 → 时间和语言 → 日期和时间 / 区域 / 语言，项目同上。
- **macOS 13 及以上**：系统设置 → 通用 → 日期与时间：关闭「使用你的位置自动设定时区」，选择最近的城市；通用 → 语言与地区：「地区」选出口国家，「首选语言」添加英语并拖动排序。

改完后：完全退出并重新打开浏览器；注销重新登录一次，语言和地区才完全生效。
