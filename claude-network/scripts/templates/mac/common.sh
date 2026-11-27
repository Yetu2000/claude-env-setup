# ---- claude-network 公共函数（生成时内联进每个脚本，脚本可脱离 skill 独立运行）----
# 约定：所有外部命令写绝对路径；动态值只经单引号字面量赋给变量。
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
USER_NAME=@@USER@@
USER_UID=@@UID@@
USER_HOME=@@HOME@@
VERGE_DIR=@@VERGE_DIR@@
APP=@@APP@@
APP_ID='io.github.clash-verge-rev.clash-verge-rev'
PORT=@@PORT@@
GUARD_DIR=@@GUARD_DIR@@
PF_ANCHOR=@@ANCHOR@@
LD_LABEL=@@LABEL@@
LD_PLIST="/Library/LaunchDaemons/$LD_LABEL.plist"
VERGE_SERVICE='io.github.clash-verge-rev.clash-verge-service'

log() {
  _line="$(/bin/date '+%Y-%m-%d %H:%M:%S') $*"
  printf '%s\n' "$_line"
  if [ -n "${LOG:-}" ]; then printf '%s\n' "$_line" >>"$LOG" 2>/dev/null || true; fi
}

is_root() { [ "$(/usr/bin/id -u)" -eq 0 ]; }

# 以登录用户身份执行：root 下经 launchctl asuser 回到用户的 GUI 会话
as_user() {
  if is_root; then
    /bin/launchctl asuser "$USER_UID" /usr/bin/sudo -u "$USER_NAME" "$@"
  else
    "$@"
  fi
}

# 普通用户、清空代理变量、--noproxy '*'：测的是虚拟网卡，不是代理端口
user_curl() {
  as_user /usr/bin/env -i HOME="$USER_HOME" PATH=/usr/bin:/bin /usr/bin/curl --noproxy '*' -sS -o /dev/null --max-time "${2:-10}" "$1" 2>/dev/null
}

user_curl_body() {
  as_user /usr/bin/env -i HOME="$USER_HOME" PATH=/usr/bin:/bin /usr/bin/curl --noproxy '*' -sS --max-time "${2:-10}" "$1" 2>/dev/null
}

claude_ok() { user_curl 'https://claude.ai/cdn-cgi/trace' 10; }

# 在 $1 秒内等 claude.ai 通；通了返回 0
wait_claude() {
  _limit=${1:-120}
  _waited=0
  while [ "$_waited" -lt "$_limit" ]; do
    if claude_ok; then
      log "claude.ai 已通（等了 ${_waited}s）"
      return 0
    fi
    /bin/sleep 5
    _waited=$((_waited + 5))
  done
  log "claude.ai 在 ${_limit}s 内没有连通"
  return 1
}

port_open() { /usr/bin/nc -z -G 2 127.0.0.1 "$PORT" >/dev/null 2>&1; }

core_sock() {
  for _s in /tmp/verge/verge-mihomo*.sock; do
    if [ -S "$_s" ]; then printf '%s\n' "$_s"; return 0; fi
  done
  return 1
}

# api 方法 路径 [JSON 正文] [超时]；-f：HTTP 4xx/5xx（节点名不对、切换失败）按失败返回
api() {
  _sock=$(core_sock) || return 1
  if [ -n "${3:-}" ]; then
    /usr/bin/curl -fsS --max-time "${4:-15}" --unix-socket "$_sock" -X "$1" -H 'Content-Type: application/json' -d "$3" "http://localhost$2"
  else
    /usr/bin/curl -fsS --max-time "${4:-15}" --unix-socket "$_sock" -X "$1" "http://localhost$2"
  fi
}

# 用客户端的测速接口确认节点真的通（$1 为已 URL 编码的节点名）
delay_ok() {
  _out=$(api GET "/proxies/$1/delay?timeout=6000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204" '' 12) || return 1
  case "$_out" in
    *'"delay"'*) log "测速通过：$_out"; return 0 ;;
  esac
  log "测速失败：$_out"
  return 1
}

# select 组(URL 编码) 节点(JSON 字符串)
select_node() {
  api PUT "/proxies/$1" "{\"name\":$2}" >/dev/null && log "已切换 $1 → $2"
}

tun_iface() {
  for _i in $(/sbin/ifconfig -l); do
    case "$_i" in
      utun*)
        if /sbin/ifconfig "$_i" 2>/dev/null | /usr/bin/grep -q 'inet 198\.18\.'; then printf '%s\n' "$_i"; return 0; fi
        ;;
    esac
  done
  return 1
}

client_running() { /usr/bin/pgrep -x clash-verge >/dev/null 2>&1; }

quit_client() {
  if client_running; then
    log "退出 Clash Verge"
    as_user /usr/bin/osascript -e "tell application id \"$APP_ID\" to quit" >/dev/null 2>&1 || true
    _n=0
    while client_running && [ "$_n" -lt 15 ]; do /bin/sleep 1; _n=$((_n + 1)); done
    if client_running; then
      log "正常退出超时，强制结束"
      as_user /usr/bin/pkill -x clash-verge >/dev/null 2>&1 || /usr/bin/pkill -x clash-verge >/dev/null 2>&1 || true
      /bin/sleep 2
    fi
  fi
}

start_client() {
  log "启动 Clash Verge"
  as_user /usr/bin/open -a "$APP" >/dev/null 2>&1 || log "open -a 失败"
}

# 等代理端口与控制器就绪；刚重启时可能几十秒起不来，超过一半时间还不行就让核心重载一次配置
wait_ready() {
  _limit=${1:-90}
  _waited=0
  _reloaded=0
  while [ "$_waited" -lt "$_limit" ]; do
    if port_open && api GET /version '' 5 >/dev/null 2>&1; then
      log "代理端口 $PORT 与控制器已就绪（${_waited}s）"
      return 0
    fi
    if [ "$_reloaded" -eq 0 ] && [ "$_waited" -ge $((_limit / 2)) ] && core_sock >/dev/null; then
      log "端口还没起来，让核心重载一次配置"
      api PUT '/configs?force=true' '{"path":"","payload":""}' >/dev/null 2>&1 || true
      _reloaded=1
    fi
    /bin/sleep 3
    _waited=$((_waited + 3))
  done
  log "代理端口 $PORT 在 ${_limit}s 内未就绪"
  return 1
}

# 卸载服务模式后 /tmp/verge 会残留 sock / pid，客户端再打开时核心起不来：服务没加载时清掉
clean_stale_verge_tmp() {
  # 只在 root 下做：普通用户查不到 system 域的服务，会误判为未加载而删掉正在用的 sock
  is_root || return 0
  if ! /bin/launchctl print "system/$VERGE_SERVICE" >/dev/null 2>&1; then
    if [ -d /tmp/verge ]; then
      log "服务模式未加载，清理 /tmp/verge 残留的 sock / pid"
      /bin/rm -f /tmp/verge/*.sock /tmp/verge/*.pid 2>/dev/null || true
    fi
  fi
}

# 关掉所有网络服务上的系统代理（客户端被强行退出时，系统代理可能还指着它的端口）
system_proxy_off() {
  /usr/sbin/networksetup -listallnetworkservices 2>/dev/null | /usr/bin/sed '1d' | /usr/bin/sed 's/^\*//' | while IFS= read -r _svc; do
    [ -n "$_svc" ] || continue
    /usr/sbin/networksetup -setwebproxystate "$_svc" off >/dev/null 2>&1 || true
    /usr/sbin/networksetup -setsecurewebproxystate "$_svc" off >/dev/null 2>&1 || true
    /usr/sbin/networksetup -setsocksfirewallproxystate "$_svc" off >/dev/null 2>&1 || true
    /usr/sbin/networksetup -setautoproxystate "$_svc" off >/dev/null 2>&1 || true
  done
  log "已关闭所有网络服务的系统代理"
}

# 断网开关解除（off.sh 不存在时用兜底命令）
killswitch_off() {
  if [ -f "$GUARD_DIR/off.sh" ]; then
    /bin/sh "$GUARD_DIR/off.sh" >>"${LOG:-/dev/null}" 2>&1 && log "断网开关已解除（off.sh）" && return 0
  fi
  /bin/launchctl bootout "system/$LD_LABEL" >/dev/null 2>&1 || true
  /bin/rm -f "$LD_PLIST"
  /sbin/pfctl -a "$PF_ANCHOR" -F all >/dev/null 2>&1 || true
  if [ -f "$GUARD_DIR/token" ]; then /sbin/pfctl -X "$(/bin/cat "$GUARD_DIR/token")" >/dev/null 2>&1 || true; fi
  log "断网开关已用兜底命令解除"
}
# ---- 公共函数结束 ----
