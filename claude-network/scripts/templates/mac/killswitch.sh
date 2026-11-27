#!/bin/sh
# claude-network：安装断网开关（sudo sh "<本文件路径>"）。
# 装好后：代理崩了、被退出、虚拟网卡没了，普通程序的流量出不了物理网卡，只有局域网能通；
# 开机自动生效（launchd local.clash-guard），规则被别的程序清掉 5 秒内补回。
# 文件放在 /Library/Application Support/clash-guard/，一条命令解除：sudo sh "/Library/Application Support/clash-guard/off.sh"
# 安全阀：装之前确认虚拟网卡真的起来、claude.ai 通；装完 60 秒内普通用户连不上 claude.ai 就自动解除。
set -u
@@COMMON@@
LOG=@@LOG@@
errors=0

fail() { log "错误：$*"; errors=$((errors + 1)); }

if ! is_root; then
  echo "需要管理员权限。请在「终端」里执行：sudo sh \"$0\""
  exit 77
fi
log "==== 安装断网开关 ===="

# ---- 0. 前置检查（任何一项不过都不改任何东西）----
TUN=$(tun_iface) || TUN=''
if [ -z "$TUN" ]; then
  log "没有检测到虚拟网卡（utun 上的 198.18.x 地址）：现在装会让整台电脑立刻断网，拒绝安装"
  exit 10
fi
log "虚拟网卡：$TUN"
if ! /sbin/pfctl -sr 2>/dev/null | /usr/bin/grep -Eq '^[[:space:]]*anchor[[:space:]]+"com\.apple/\*"[[:space:]]+all([[:space:]]|$)'; then
  log 'pf 主规则集缺少 anchor "com.apple/*"：锚点规则不会生效。本 skill 不改 /etc/pf.conf，拒绝安装'
  exit 11
fi
if ! claude_ok; then
  log "安装前 claude.ai 就不通：先把代理修好再装，拒绝安装"
  exit 12
fi
if [ -f "$LD_PLIST" ] || /bin/launchctl print "system/$LD_LABEL" >/dev/null 2>&1; then
  log "已安装过：先解除旧的再重装"
  killswitch_off
fi

# ---- 1. 写文件 ----
/bin/mkdir -p "$GUARD_DIR" || fail "创建 $GUARD_DIR 失败"
/usr/sbin/chown root:wheel "$GUARD_DIR" && /bin/chmod 755 "$GUARD_DIR" || fail "设置 $GUARD_DIR 权限失败"
/bin/cat >"$GUARD_DIR/apply.sh" <<'CNET_GUARD_APPLY_EOF'
@@GUARD_APPLY@@
CNET_GUARD_APPLY_EOF
/bin/cat >"$GUARD_DIR/watch.sh" <<'CNET_GUARD_WATCH_EOF'
@@GUARD_WATCH@@
CNET_GUARD_WATCH_EOF
/bin/cat >"$GUARD_DIR/off.sh" <<'CNET_GUARD_OFF_EOF'
@@GUARD_OFF@@
CNET_GUARD_OFF_EOF
/bin/cat >"$GUARD_DIR/README-解除.txt" <<'CNET_GUARD_README_EOF'
断网开关（clash-guard）
解除：sudo sh "/Library/Application Support/clash-guard/off.sh"
兜底：sudo launchctl bootout system/local.clash-guard
      sudo pfctl -a com.apple/clash-guard -F all
CNET_GUARD_README_EOF
for _f in apply.sh watch.sh off.sh README-解除.txt; do
  /usr/sbin/chown root:wheel "$GUARD_DIR/$_f" || fail "chown $_f"
done
/bin/chmod 755 "$GUARD_DIR/apply.sh" "$GUARD_DIR/watch.sh" "$GUARD_DIR/off.sh" || fail "chmod 脚本"
/bin/sh -n "$GUARD_DIR/apply.sh" || fail "apply.sh 语法"
/bin/sh -n "$GUARD_DIR/watch.sh" || fail "watch.sh 语法"
/bin/sh -n "$GUARD_DIR/off.sh" || fail "off.sh 语法"
if [ "$errors" -ne 0 ]; then
  log "写文件阶段出错，未启用任何规则"
  exit 20
fi

# ---- 2. 先加载一次规则（apply.sh 内部先做 pfctl -n 语法检查）----
if ! /bin/sh "$GUARD_DIR/apply.sh" >>"$LOG" 2>&1; then
  fail "apply.sh 加载规则失败"
  killswitch_off
  exit 21
fi

# ---- 3. launchd：开机生效、被清掉自动补回 ----
/bin/cat >"$LD_PLIST" <<'CNET_GUARD_PLIST_EOF'
@@GUARD_PLIST@@
CNET_GUARD_PLIST_EOF
/usr/sbin/chown root:wheel "$LD_PLIST" && /bin/chmod 644 "$LD_PLIST" || fail "plist 权限"
/usr/bin/plutil -lint "$LD_PLIST" >/dev/null || fail "plist 校验"
/bin/launchctl bootstrap system "$LD_PLIST" || fail "launchctl bootstrap"
/bin/sleep 6

# ---- 4. 复核（第 8 项的证据写进日志）----
if /sbin/pfctl -a "$PF_ANCHOR" -sr 2>/dev/null | /usr/bin/grep -q 'block return'; then
  log "锚点 $PF_ANCHOR 规则："
  /sbin/pfctl -a "$PF_ANCHOR" -sr 2>/dev/null | while IFS= read -r _r; do log "  $_r"; done
else
  fail "锚点 $PF_ANCHOR 没有规则"
fi
if /bin/launchctl print "system/$LD_LABEL" 2>/dev/null | /usr/bin/grep -q 'state = running'; then
  log "launchd 服务 $LD_LABEL：running"
else
  fail "launchd 服务 $LD_LABEL 没有在运行"
fi
/sbin/pfctl -s info 2>/dev/null | /usr/bin/grep -q 'Status: Enabled' && log "pf：Enabled" || fail "pf 未启用"

# ---- 5. 安全阀：普通用户 60 秒内要能连上 claude.ai ----
if ! wait_claude 60; then
  fail "装上后 claude.ai 不通：自动解除断网开关"
  killswitch_off
  wait_claude 60 || true
  log "已解除。错误数：$errors"
  exit 30
fi
log "断网开关安装完成，错误数：$errors"
log "解除命令：sudo sh \"$GUARD_DIR/off.sh\""
exit "$errors"
