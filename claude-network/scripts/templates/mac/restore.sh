#!/bin/sh
# claude-network 一键还原：sudo sh "<本文件路径>"
# 回到备份时（@@STAMP@@）的样子：解除断网开关 → 退出客户端 → 放回整个客户端配置目录
# → 还原各网络服务的 DNS / IPv6 / 系统代理 → 重开客户端 → 确认代理端口真的通了（不通就等端口空出来再开一次）。
# 断网时也能用；中间哪一步出错都继续往下做。
set -u
@@COMMON@@
BACKUP=@@BACKUP@@
LOG="$BACKUP/restore-$(/bin/date '+%Y%m%d-%H%M%S').log"
STAMP=$(/bin/date '+%Y%m%d-%H%M%S')

if ! is_root; then
  echo "需要管理员权限。请在「终端」里执行：sudo sh \"$0\""
  exit 77
fi
log "==== 一键还原开始，备份：$BACKUP ===="

# 1. 断网开关
killswitch_off

# 2. 退出客户端
quit_client
/usr/bin/pkill -x clash-verge >/dev/null 2>&1 || true

# 3. 放回客户端配置目录（当前目录改名保留，不删除）
if [ -d "$BACKUP/verge" ]; then
  if [ -d "$VERGE_DIR" ]; then
    /bin/mv "$VERGE_DIR" "$VERGE_DIR.before-restore-$STAMP" && log "当前配置目录已改名为 $VERGE_DIR.before-restore-$STAMP" || log "改名当前配置目录失败，继续"
  fi
  /usr/bin/ditto "$BACKUP/verge" "$VERGE_DIR" && log "已放回备份的配置目录" || log "放回配置目录失败（继续）"
  /usr/sbin/chown -R "$USER_NAME" "$VERGE_DIR" 2>/dev/null || true
else
  log "备份里没有 verge 目录，跳过"
fi

# 4. 网络服务：系统代理先全部关掉，再按备份时的值还原 DNS / IPv6 / 非本机的代理设置
system_proxy_off
@@NETWORK_RESTORE@@
/usr/bin/dscacheutil -flushcache >/dev/null 2>&1 || true
/usr/bin/killall -HUP mDNSResponder >/dev/null 2>&1 || true

# 5. 重开客户端，确认代理端口真的通了
clean_stale_verge_tmp
start_client
if ! wait_ready 90; then
  log "端口没起来：等端口空出来再开一次"
  quit_client
  /usr/bin/pkill -x clash-verge >/dev/null 2>&1 || true
  _n=0
  while /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && [ "$_n" -lt 30 ]; do /bin/sleep 2; _n=$((_n + 1)); done
  clean_stale_verge_tmp
  start_client
  wait_ready 90 || log "第二次仍未就绪"
fi

if port_open; then log "代理端口 $PORT 已监听"; else log "代理端口 $PORT 未监听"; fi
if claude_ok; then
  log "还原完成：claude.ai 可访问"
else
  log "还原完成：claude.ai 暂时不通（备份时的配置本身可能就不通，或客户端还在启动）"
fi
log "日志：$LOG"
exit 0
