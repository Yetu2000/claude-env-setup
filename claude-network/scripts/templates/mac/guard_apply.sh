#!/bin/sh
# clash-guard 断网开关：生成并加载 pf 锚点规则（root；由 launchd local.clash-guard 的 watch.sh 调用）。
# 规则只挂在锚点 com.apple/clash-guard 下，不修改 /etc/pf.conf。
# 虚拟网卡在：放行它；不在：普通程序在物理网卡上一律被拦（代理核心以 root 运行，不受影响）。
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
BASE='/Library/Application Support/clash-guard'
ANCHOR='com.apple/clash-guard'
TOKEN_FILE="$BASE/token"
LANV4='10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10, 224.0.0.0/4'
LANV6='fe80::/10, fc00::/7, ff00::/8'

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "需要 root：sudo sh \"$0\"" >&2
  exit 77
fi

# 主规则集必须含 anchor "com.apple/*"，否则锚点里的规则不会被求值（本脚本不改 pf.conf）
if ! /sbin/pfctl -sr 2>/dev/null | /usr/bin/grep -Eq '^[[:space:]]*anchor[[:space:]]+"com\.apple/\*"[[:space:]]+all([[:space:]]|$)'; then
  echo 'pf 主规则集（pfctl -sr）缺少 anchor "com.apple/*"；本脚本不改 pf.conf，请确认 /etc/pf.conf 后执行 sudo pfctl -f /etc/pf.conf' >&2
  exit 83
fi

TUN=''
for i in $(/sbin/ifconfig -l); do
  case "$i" in
    utun*)
      if /sbin/ifconfig "$i" 2>/dev/null | /usr/bin/grep -q 'inet 198\.18\.'; then TUN="$i"; break; fi
      ;;
  esac
done
PHYSICAL=$(/sbin/ifconfig -l | /usr/bin/tr ' ' '\n' | /usr/bin/grep -E '^en[0-9]+$' | /usr/bin/tr '\n' ',' | /usr/bin/sed 's/,$//' || true)
if [ -z "$PHYSICAL" ]; then
  echo '未发现物理网卡 enN；跳过加载' >&2
  exit 80
fi

RULES=$(/usr/bin/mktemp /tmp/clash-guard-pf.XXXXXX)
trap '/bin/rm -f "$RULES"' EXIT
{
  echo 'pass quick on lo0 all'
  if [ -n "$TUN" ]; then echo "pass quick on $TUN all"; fi
  echo "block return quick out on { $PHYSICAL } inet proto { tcp udp } from any to { $LANV4 } port 53 user != root"
  echo "block return quick out on { $PHYSICAL } inet6 proto { tcp udp } from any to { $LANV6 } port 53 user != root"
  echo "pass quick out on { $PHYSICAL } inet to { $LANV4 }"
  echo "pass quick out on { $PHYSICAL } inet6 to { $LANV6 }"
  echo "block return quick out on { $PHYSICAL } inet proto { tcp udp } from any to any user != root"
  echo "block return quick out on { $PHYSICAL } inet6 proto { tcp udp } from any to any user != root"
} >"$RULES"

if ! /sbin/pfctl -n -a "$ANCHOR" -f "$RULES" 2>/dev/null; then
  echo 'pf 规则语法检查未通过；未加载' >&2
  exit 81
fi
/sbin/pfctl -a "$ANCHOR" -f "$RULES" 2>/dev/null

need_token=0
[ -s "$TOKEN_FILE" ] || need_token=1
/sbin/pfctl -s info 2>/dev/null | /usr/bin/grep -q 'Status: Enabled' || need_token=1
if [ "$need_token" -eq 1 ]; then
  out=$(/sbin/pfctl -E 2>&1 || true)
  token=$(printf '%s\n' "$out" | /usr/bin/sed -n 's/.*Token[^0-9]*\([0-9][0-9]*\).*/\1/p' | /usr/bin/head -n 1)
  if [ -n "$token" ]; then
    printf '%s\n' "$token" >"$TOKEN_FILE"
    /bin/chmod 600 "$TOKEN_FILE"
  fi
fi
echo "$(/bin/date '+%F %T') 已加载 $ANCHOR（TUN=${TUN:-无，普通程序全拦}，物理网卡=$PHYSICAL）"
