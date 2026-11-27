#!/bin/sh
# claude-network：堵住 DNS 与 IPv6 的旁路（sudo sh "<本文件路径>"）。
# 为什么要改：虚拟网卡只接管默认路由，系统 DNS 若指向路由器（局域网地址），查询会从物理网卡直接出去，
#   不进虚拟网卡 → 运营商能看到你查了哪些域名（DNS 泄漏）。改成 223.5.5.5 / 119.29.29.29 后，
#   查询走默认路由进虚拟网卡，被核心劫持（dns-hijack）；虚拟网卡不在时由断网开关拦住。
#   IPv6：核心已关 IPv6、不返回 AAAA；再把各网络服务的 IPv6 关掉，杜绝写死 IPv6 地址的程序绕过虚拟网卡。
# 改完 60 秒内普通用户连不上 claude.ai 就自动改回去。备份时的原值写在下面的还原段里。
set -u
@@COMMON@@
LOG=@@LOG@@

if ! is_root; then
  echo "需要管理员权限。请在「终端」里执行：sudo sh \"$0\""
  exit 77
fi
log "==== DNS / IPv6 加固开始 ===="

revert() {
  log "还原 DNS / IPv6 到备份时的值"
@@SERVICES_REVERT@@
  /usr/bin/dscacheutil -flushcache >/dev/null 2>&1 || true
  /usr/bin/killall -HUP mDNSResponder >/dev/null 2>&1 || true
}

if ! claude_ok; then
  log "改之前 claude.ai 就不通：不做任何修改"
  exit 2
fi

errors=0
@@SERVICES_APPLY@@
/usr/bin/dscacheutil -flushcache >/dev/null 2>&1 || true
/usr/bin/killall -HUP mDNSResponder >/dev/null 2>&1 || true

if wait_claude 60; then
  log "加固完成，错误数：$errors"
  exit "$errors"
fi
revert
wait_claude 60 || true
log "加固后 claude.ai 不通，已自动改回原值"
exit 5
