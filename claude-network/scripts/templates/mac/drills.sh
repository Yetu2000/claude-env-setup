#!/bin/sh
# claude-network：故障演练 9~11（sudo sh "<本文件路径>"）。三个演练写在同一个脚本里，
# 用 sudo -u 普通用户 跑 curl（root 不受断网开关限制，测不准）。每个演练结束先自动恢复、
# 确认 claude.ai 通了再做下一个；不管中途成败，脚本结束时都回到正常状态。
#   9  海外出口切到连不上的节点：claude.ai、api.ipify.org 必须失败（不能换节点、不能变成本地 IP），baidu.com 照常
#   10 关掉虚拟网卡：普通用户 curl 外网必须被拒（exit 7 或 28）
#   11 退出客户端等 3 秒：普通用户 curl 外网必须被拒；然后重开，claude.ai 恢复
set -u
@@COMMON@@
LOG=@@LOG@@
RESULT=@@RESULT@@
OVERSEAS_URL=@@OVERSEAS_URL@@
DRILL_JSON=@@DRILL_JSON@@
NORMAL_JSON=@@NORMAL_JSON@@
pass9=FAIL; pass10=FAIL; pass11=FAIL

if ! is_root; then
  echo "需要管理员权限。请在「终端」里执行：sudo sh \"$0\""
  exit 77
fi
log "==== 故障演练 9~11 ===="

curl_code() {
  as_user /usr/bin/env -i HOME="$USER_HOME" PATH=/usr/bin:/bin /usr/bin/curl --noproxy '*' -sS -o /dev/null --max-time 8 "$1" >/dev/null 2>&1
  echo $?
}

current_exit() {
  api GET "/proxies/$OVERSEAS_URL" '' 5 | /usr/bin/sed -n 's/.*"now":"\([^"]*\)".*/\1/p'
}

back_to_normal() {
  # 不论处于什么状态，都拉回：客户端在跑、虚拟网卡开着、海外出口选回原节点、claude.ai 通
  if ! client_running; then start_client; fi
  wait_ready 120 || true
  api PATCH /configs '{"tun":{"enable":true}}' >/dev/null 2>&1 || true
  select_node "$OVERSEAS_URL" "$NORMAL_JSON" >/dev/null 2>&1 || true
  wait_claude 120
}

if ! wait_claude 30; then
  log "演练前 claude.ai 就不通：不做演练"
  exit 2
fi
log "当前海外出口：$(current_exit)（演练后恢复为 $NORMAL_JSON）"
if [ -z "$(tun_iface || true)" ]; then log "虚拟网卡不在：先修好再演练"; exit 3; fi
if [ ! -f "$LD_PLIST" ]; then log "注意：断网开关没装，10、11 两项预期会失败"; fi

# ---------- 演练 9 ----------
log "-- 演练 9：海外出口切到连不上的节点 --"
if select_node "$OVERSEAS_URL" "$DRILL_JSON"; then
  /bin/sleep 2
  c1=$(curl_code 'https://claude.ai/cdn-cgi/trace')
  ip2=$(user_curl_body 'https://api.ipify.org' 8 || true)
  c3=$(curl_code 'https://www.baidu.com')
  log "claude.ai exit=$c1；api.ipify.org 返回=[${ip2}]；baidu.com exit=$c3"
  if [ "$c1" -ne 0 ] && [ -z "$ip2" ] && [ "$c3" -eq 0 ]; then pass9=PASS; fi
else
  log "切换到演练节点失败"
fi
back_to_normal || log "演练 9 后恢复失败"

# ---------- 演练 10 ----------
log "-- 演练 10：关掉虚拟网卡 --"
api PATCH /configs '{"tun":{"enable":false}}' >/dev/null 2>&1
_n=0
while tun_iface >/dev/null && [ "$_n" -lt 10 ]; do /bin/sleep 1; _n=$((_n + 1)); done
if tun_iface >/dev/null; then
  log "虚拟网卡没有关掉，演练 10 无法进行"
else
  c1=$(curl_code 'https://api.ipify.org')
  c2=$(curl_code 'https://www.baidu.com')
  c3=$(curl_code 'http://1.1.1.1')
  log "虚拟网卡已关：api.ipify.org exit=$c1；baidu.com exit=$c2；1.1.1.1 exit=$c3（期望 7 或 28）"
  ok=1
  for c in "$c1" "$c2" "$c3"; do
    case "$c" in 7|28) ;; *) ok=0 ;; esac
  done
  [ "$ok" -eq 1 ] && pass10=PASS
fi
back_to_normal || log "演练 10 后恢复失败"

# ---------- 演练 11 ----------
log "-- 演练 11：退出客户端 --"
quit_client
/usr/bin/pkill -x verge-mihomo >/dev/null 2>&1 || true
/usr/bin/pkill -x verge-mihomo-alpha >/dev/null 2>&1 || true
/bin/sleep 3
if tun_iface >/dev/null; then
  log "退出后虚拟网卡仍在（核心可能被服务拉起）：本项判为未通过，需人工复查"
else
  c1=$(curl_code 'https://api.ipify.org')
  c2=$(curl_code 'https://www.baidu.com')
  log "客户端已退出：api.ipify.org exit=$c1；baidu.com exit=$c2（期望 7 或 28）"
  case "$c1" in 7|28) case "$c2" in 7|28) pass11=PASS ;; esac ;; esac
fi
system_proxy_off
back_to_normal || log "演练 11 后恢复失败"

final="正常"
claude_ok || final="claude.ai 不通"
log "==== 结果：演练9=$pass9 演练10=$pass10 演练11=$pass11；结束时状态：$final ===="
printf 'drill9=%s\ndrill10=%s\ndrill11=%s\nfinal=%s\n' "$pass9" "$pass10" "$pass11" "$final" >"$RESULT"
/usr/sbin/chown "$USER_NAME" "$RESULT" "$LOG" 2>/dev/null || true
exit 0
