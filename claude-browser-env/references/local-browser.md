# 本地浏览器（Chrome / Edge / Firefox / Safari）

适用于不用指纹浏览器、直接在自己电脑的浏览器里登录 Claude 的情况。所有操作都在**要登录 Claude 的那个浏览器资料**里做。

## 1. 以前登录过 Claude，或者有账号被封过

推荐做法是**新建一个资料专门给 Claude 用**，日常浏览留在原来的资料里，互不影响：

- **Chrome**：右上角头像 → 添加 →「在没有账号的情况下继续」，起名 Claude。
- **Edge**：右上角头像 → 其他配置文件 → 添加配置文件 → 添加，不登录微软账号。
- **Firefox**：地址栏打开 `about:profiles` → 创建新配置文件。
- **Safari**（macOS 14 及以上）：Safari → 设置 → 描述文件 → 新建描述文件。

不想新建资料，就清理这个资料里 Claude 相关网站的数据（claude.ai、claude.com、anthropic.com）：

- **Chrome**：地址栏打开 `chrome://settings/content/all`，搜索 `claude` 和 `anthropic`，逐个点删除图标；再在 `chrome://settings/clearBrowserData` 里清除「缓存的图片和文件」。
- **Edge**：设置 → Cookie 和网站权限 → 管理和删除 Cookie 和站点数据 → 查看所有 Cookie 和站点数据，搜索 `claude`、`anthropic` 删除；再在「隐私、搜索和服务」里清除缓存。
- **Firefox**：设置 → 隐私与安全 → Cookie 和网站数据 → 管理数据，搜索 `claude`、`anthropic`，移除所选。
- **Safari**：设置 → 隐私 → 管理网站数据，搜索 `claude`、`anthropic`，移除。

说明：

- 清完重新运行一次审计（先关浏览器），对应资料的 `used_claude` 应该变成假。访问记录也会计入，想让它归零需要同时清除这些网站的历史记录。
- 封禁记录在服务端。清理本机浏览器数据不会让被封的账号恢复，要恢复只能通过官方申诉。

## 2. WebRTC 防泄漏

- **Chrome / Brave / 其他 Chromium 浏览器**：安装 WebRTC Protect：<https://chromewebstore.google.com/detail/webrtc-protect-protect-ip/bkmmlbllpjdpgcgdohbaghfaecnddhni>，装好后确认扩展处于开启状态。
- **Edge**：先在 `edge://extensions` 左下角打开「允许来自其他应用商店的扩展」，再用上面的链接安装。
- **Firefox**：地址栏打开 `about:config`，把下面两项设为 `true`：
  - `media.peerconnection.ice.default_address_only`
  - `media.peerconnection.ice.no_host`
  不用视频会议的话，也可以把 `media.peerconnection.enabled` 设为 `false`，彻底关闭 WebRTC。
- **Safari**：没有对应扩展。到下面的实测网站检查，能看到真实 IP 就换浏览器。

实测：打开 <https://browserleaks.com/webrtc>，「Public IP Address」只应显示代理出口 IP；「Local IP Address」不应出现内网地址（显示为 `-` 或 `xxx.local` 都正常）。看到本地宽带的公网 IP 就是泄漏。

## 3. 浏览器语言

顺序按审计结果里的 `browser_languages`（例如 `zh-CN, zh, en-US, en`），与系统语言列表保持一致：

- **Chrome**：`chrome://settings/languages` →「首选语言」里添加缺的语言，用每项右侧的「⋮」调整顺序。
- **Edge**：`edge://settings/languages` →「首选语言」添加并排序。
- **Firefox**：设置 → 常规 → 语言 →「网页语言」旁的「选择」，添加并排序。
- **Safari**：跟随系统语言列表，不用单独设置。

浏览器界面显示中文没关系，要对上的是「首选语言」列表（网页读到的 `navigator.languages` 和请求头 `Accept-Language`）。

## 4. 时区

浏览器用的是系统时区，按 SKILL.md 第 3 步改完系统时区后，**完全退出浏览器再打开**（关掉所有窗口，Windows 上还要确认任务栏托盘里没有后台进程）。

## 5. 最终核对

打开 <https://www.browserscan.net>：

- IP 所在地区与时区、系统时区一致；
- 浏览器语言与上面的顺序一致；
- 没有 DNS 泄漏和 WebRTC 泄漏。

Claude Code 登录时会打开默认浏览器。如果默认浏览器不是这里配置好的浏览器或资料，就把终端里打印的登录链接复制到配置好的那个资料里打开。
