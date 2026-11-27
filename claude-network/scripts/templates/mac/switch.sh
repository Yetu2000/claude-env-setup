#!/bin/sh
# claude-network：切换到新配置（不需要 sudo，用 nohup 放后台跑；AI 断线也会自己跑完、自己恢复）。
# 流程：确认备份完整 → 退出客户端 → 替换 3 个文件 → 重开 → 等端口 → 测速确认节点通 → 选节点
#       → 2 分钟内普通用户连不上 claude.ai 就自动还原这 3 个文件并重开。脚本结束时网一定是通的。
set -u
@@COMMON@@
LOG=@@LOG@@
BACKUP=@@BACKUP@@
STAGE=@@STAGE@@
RESULT=@@RESULT@@
MODE=@@MODE@@
OVERSEAS_URL=@@OVERSEAS_URL@@
JUMPGROUP_URL=@@JUMPGROUP_URL@@
EXIT_URL=@@EXIT_URL@@
EXIT_JSON=@@EXIT_JSON@@
JUMP_URL=@@JUMP_URL@@
JUMP_JSON=@@JUMP_JSON@@
RES_URL=@@RES_URL@@
RES_JSON=@@RES_JSON@@
FILES='verge.yaml config.yaml profiles/Script.js'

finish() {
  printf '%s\n' "$1" >"$RESULT"
  log "结果：$1"
  exit "${2:-0}"
}

copy_set() {
  # copy_set 来源目录 [strict]：把 3 个文件放回客户端目录；strict 时来源里没有的文件在目标里删掉（还原成「原来没有」）
  for _f in $FILES; do
    if [ -f "$1/$_f" ]; then
      /bin/cp -p "$1/$_f" "$VERGE_DIR/$_f" || log "复制 $_f 失败"
    elif [ "${2:-}" = strict ]; then
      /bin/rm -f "$VERGE_DIR/$_f" && log "备份里没有 $_f，已删除新写入的那份"
    fi
  done
  /bin/chmod 600 "$VERGE_DIR/profiles/Script.js" 2>/dev/null || true
}

rollback() {
  log "==== 失败，自动还原到备份时的 3 个文件：$1 ===="
  quit_client
  copy_set "$BACKUP/verge" strict
  start_client
  wait_ready 120 || true
  if wait_claude 120; then
    finish "ROLLED_BACK: $1（已还原，claude.ai 通）" 3
  fi
  finish "ROLLED_BACK_BUT_OFFLINE: $1（已还原但 claude.ai 仍不通，请按桌面「断网急救.txt」处理）" 4
}

log "==== claude-network switch 开始（方案 $MODE）===="
printf 'RUNNING\n' >"$RESULT"
if [ ! -f "$BACKUP/BACKUP_OK" ]; then
  finish "ABORTED: 备份未完成（缺 $BACKUP/BACKUP_OK），没有改任何东西" 2
fi
for _f in $FILES; do
  [ -f "$STAGE/$_f" ] || finish "ABORTED: 预备文件缺失 $STAGE/$_f，没有改任何东西" 2
done

# 1. 先退出：客户端运行时会把内存里的配置写回磁盘，开着改会被冲掉
quit_client
# 2. 替换
copy_set "$STAGE"
log "已写入新的 verge.yaml / config.yaml / profiles/Script.js"
# 3. 重开并等端口
clean_stale_verge_tmp
start_client
if ! wait_ready 120; then
  rollback "代理端口 / 控制器 120 秒内没有就绪"
fi
/bin/sleep 3

# 4. 切节点之前先测速：海外出口里没有直连，选了死节点等于海外全断
if [ "$MODE" = "A" ]; then
  delay_ok "$EXIT_URL" || rollback "海外出口节点测速不通"
  select_node "$OVERSEAS_URL" "$EXIT_JSON" || rollback "切换海外出口失败"
else
  delay_ok "$JUMP_URL" || rollback "跳板节点测速不通"
  select_node "$JUMPGROUP_URL" "$JUMP_JSON" || rollback "切换跳板失败"
  select_node "$OVERSEAS_URL" "$RES_JSON" || rollback "切换海外出口失败"
  delay_ok "$RES_URL" || rollback "住宅出口（经跳板）测速不通：检查住宅代理的 IP、端口、账号、密码、白名单与有效期"
fi

# 5. 普通用户经虚拟网卡访问 claude.ai（重载后 fake-ip 会变，系统缓存的旧地址短暂连不上是正常的）
if ! wait_claude 120; then
  rollback "普通用户 2 分钟内连不上 claude.ai"
fi
_trace=$(user_curl_body 'https://claude.ai/cdn-cgi/trace' 10 | /usr/bin/grep -E '^(ip|loc)=' | /usr/bin/tr '\n' ' ')
log "claude.ai 出口：$_trace"
finish "OK: 新配置已生效，$_trace" 0
