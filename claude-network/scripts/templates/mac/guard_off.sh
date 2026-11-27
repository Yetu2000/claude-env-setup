#!/bin/sh
# clash-guard 断网开关：一条命令解除（可重复执行）：sudo sh "/Library/Application Support/clash-guard/off.sh"
# 顺序：停 launchd 服务 → 清锚点规则 → 释放 pf 引用令牌 → 复核。
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
BASE='/Library/Application Support/clash-guard'
ANCHOR='com.apple/clash-guard'
LABEL='local.clash-guard'
PLIST='/Library/LaunchDaemons/local.clash-guard.plist'
if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "需要 root：sudo sh \"$0\"" >&2
  exit 77
fi
failed=0
/bin/launchctl bootout "system/$LABEL" >/dev/null 2>&1 && echo "已停止 $LABEL" || echo "$LABEL 未加载，跳过"
/bin/rm -f "$PLIST"
/sbin/pfctl -a "$ANCHOR" -F all >/dev/null 2>&1 && echo "已清空锚点 $ANCHOR" || echo "锚点 $ANCHOR 无需清理"
if [ -f "$BASE/dns-auto.txt" ]; then
  while IFS= read -r svc; do
    [ -n "$svc" ] || continue
    /usr/sbin/networksetup -setdnsservers "$svc" empty >/dev/null 2>&1 && echo "网络服务「$svc」的 DNS 已改回 DHCP"
  done <"$BASE/dns-auto.txt"
  /bin/rm -f "$BASE/dns-auto.txt"
fi
if [ -f "$BASE/token" ]; then
  /sbin/pfctl -X "$(/bin/cat "$BASE/token")" >/dev/null 2>&1 || true
  /bin/rm -f "$BASE/token"
fi
if /bin/launchctl print "system/$LABEL" >/dev/null 2>&1; then echo "复核失败：$LABEL 仍在" >&2; failed=1; fi
if /sbin/pfctl -a "$ANCHOR" -sr 2>/dev/null | /usr/bin/grep -q .; then echo "复核失败：锚点仍有规则" >&2; failed=1; fi
if [ "$failed" -ne 0 ]; then
  echo '兜底：sudo launchctl bootout system/local.clash-guard ; sudo pfctl -a com.apple/clash-guard -F all' >&2
  exit 1
fi
echo '断网开关已解除（复核通过）。重新安装请重新运行安装脚本。'
exit 0
