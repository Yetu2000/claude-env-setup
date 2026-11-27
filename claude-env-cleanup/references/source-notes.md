# 源码要点：本机清理影响什么、不影响什么

## 本机标识

- `userID` 就是请求里 `metadata.user_id.device_id` 的值。它在首次启动时由 `crypto.randomBytes(32)` 生成（64 位十六进制），写进全局配置。**与硬件无关**：复制配置文件，标识就跟着走；删除或轮换它，下一个请求就会用新值。
- `machineID` 同样是本地生成的随机数。它唯一的外传用途是错误上报里的 `host_name_redacted`，取前 12 位。
- 没有发现读取主板、磁盘序列号，也没有拿 MAC 地址当标识的代码。唯一读 MAC 的地方是判断"是否运行在 GCP 虚拟机上"，结果只是一个布尔值。
- GrowthBook 开关值和实验分组缓存在全局配置里（`cachedGrowthBookFeatures` 等），轮换身份时会一并清除。

## 清理不能改变的东西

- 每个请求都会带上账号凭据（OAuth JWT 或 API Key）和会话 ID；第一条消息里还会附上工作目录绝对路径、平台、Shell、OS 版本和 git 状态。
- 第一方事件通道会上报邮箱、账号与组织 UUID、git remote URL、commit、MCP 与插件名等。
- 这些数据已经在服务端。本机清理只处理本机文件，**不改变也无法改变**服务端已有的记录和关联。因此本 skill 只用于隐私整理与排障，不宣称、也不用于规避封禁或风控。

## 验证状态

| 结论 | 状态 |
|---|---|
| 配置目录与全局配置的路径规则、`.config.json` 优先 | 源码（win32 包） |
| 钥匙串服务名规则 | 源码（共用代码） |
| Windows 凭据管理器的启用条件 | 源码；目标名待在虚拟机上核实 |
| 配置目录条目、按期清理范围 | 源码 |
| 深链协议、浏览器原生宿主 | 源码；Windows 的 `HKCU\Software\Classes\claude-cli` 已在真机上看到 |
| macOS launchd plist 路径 | 待用 darwin-arm64 包核实 |
| Claude Desktop 的所有路径 | 来自 suyuan/claude-cleanup 和社区资料，待实机核实 |
