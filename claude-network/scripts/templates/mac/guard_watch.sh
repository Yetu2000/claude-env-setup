#!/bin/sh
# clash-guard 守护循环（launchd local.clash-guard，KeepAlive）：每 5 秒检查 pf 是否启用、锚点规则是否还在、
# 虚拟网卡与物理网卡是否变化；有变化或被别的程序清掉就重新加载。
# 网络变化时还会检查：某个启用的网络服务用的是 DHCP 下发的 DNS（多半是路由器/热点的局域网地址）——
# 断网开关会拦普通程序发往局域网 53 端口的查询，这种网络会整机解析失败；所以把它改成 223.5.5.5 / 119.29.29.29
# （查询走默认路由进虚拟网卡），改过的服务名记在 dns-auto.txt，off.sh 解除时改回 DHCP。
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
BASE='/Library/Application Support/clash-guard'
ANCHOR='com.apple/clash-guard'
LOG="$BASE/watch.log"
AUTO_DNS="$BASE/dns-auto.txt"
last=''
last_err=''

note() { echo "$(/bin/date '+%F %T') $*" >>"$LOG"; }

fix_dhcp_dns() {
  /usr/sbin/networksetup -listallnetworkservices 2>/dev/null | /usr/bin/sed '1d' | while IFS= read -r svc; do
    case "$svc" in ''|\**) continue ;; esac
    if /usr/sbin/networksetup -getdnsservers "$svc" 2>/dev/null | /usr/bin/grep -q "There aren't any DNS Servers"; then
      if /usr/sbin/networksetup -setdnsservers "$svc" 223.5.5.5 119.29.29.29 >/dev/null 2>&1; then
        /usr/bin/grep -qxF "$svc" "$AUTO_DNS" 2>/dev/null || printf '%s\n' "$svc" >>"$AUTO_DNS"
        note "网络服务「$svc」用的是 DHCP 下发的 DNS，已改为 223.5.5.5 119.29.29.29（解除断网开关时改回）"
      fi
    fi
  done
}

while :; do
  # 主规则集缺 anchor "com.apple/*"（例如开机时没加载 pf.conf）：加载一次 /etc/pf.conf（只读取，不修改文件）
  if ! /sbin/pfctl -sr 2>/dev/null | /usr/bin/grep -Eq '^[[:space:]]*anchor[[:space:]]+"com\.apple/\*"'; then
    /sbin/pfctl -f /etc/pf.conf >/dev/null 2>&1 || true
  fi
  tun=''
  for i in $(/sbin/ifconfig -l); do
    case "$i" in
      utun*) /sbin/ifconfig "$i" 2>/dev/null | /usr/bin/grep -q 'inet 198\.18\.' && tun="$i" && break ;;
    esac
  done
  phys=$(/sbin/ifconfig -l | /usr/bin/tr ' ' '\n' | /usr/bin/grep -E '^en[0-9]+$' | /usr/bin/tr '\n' ',')
  now="$tun|$phys"
  need=0
  if [ "$now" != "$last" ]; then
    need=1
    fix_dhcp_dns
  fi
  /sbin/pfctl -a "$ANCHOR" -sr 2>/dev/null | /usr/bin/grep -q . || need=1
  /sbin/pfctl -s info 2>/dev/null | /usr/bin/grep -q 'Status: Enabled' || need=1
  if [ "$need" -eq 1 ]; then
    if out=$(/bin/sh "$BASE/apply.sh" 2>&1); then
      echo "$out" >>"$LOG"
      last="$now"
      last_err=''
    else
      # 同样的失败只记一次，避免日志每 5 秒涨一行
      if [ "$out" != "$last_err" ]; then note "重新加载失败（TUN=${tun:-无}）：$out"; fi
      last_err="$out"
    fi
  fi
  /bin/sleep 5
done
