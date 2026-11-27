"""claude-network 辅助库：只用 Python 标准库，Windows 与 macOS 通用。

模块分工：
  util        通用工具（路径、原子写、引号转义、日志）
  platform_   平台事实采集（只读）
  verge       Clash Verge Rev 配置目录、文件与行级 YAML 修补
  controller  通过 Unix socket / 命名管道访问 mihomo 控制器
  nodes       节点筛选、地区推断、延迟测试
  render      生成全局 Script.js（规则、DNS、代理组）
  gen         从 templates/ 生成需要用户或后台执行的脚本
  verify      验收 1~8 项与 DNS 泄漏测试
"""

VERSION = "0.1.0"
