# clash-guard 断网开关：补回 Windows 防火墙规则（计划任务 clash-guard-killswitch 以 SYSTEM 身份开机执行、每 5 分钟执行）。
# 原理：三个配置文件的默认出站改为 Block；只放行 回环 / 虚拟网卡（本地地址 198.18.0.0/15）/ DHCP / 局域网 /
#       代理核心程序（按安装时记录的绝对路径）。局域网 53 端口一律拦截（拦截规则优先于放行规则），防止 DNS 经路由器泄漏。
# 被删、被禁用、被改动的规则逐条补回；组内多余规则删除。不使用 WFP 持久过滤器。
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$group = 'clash-guard'
$base = 'C:\ProgramData\clash-guard'
$lan = @('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '100.64.0.0/10', '224.0.0.0/4', 'fe80::/10', 'fc00::/7', 'ff00::/8')
$programs = @()
$programsOk = $false
try { $programs = @((Get-Content -LiteralPath (Join-Path $base 'programs.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json)); $programsOk = ($programs.Count -gt 0) } catch {}
$programPrefix = 'clash-guard: 代理程序放行 '

$rules = @(
  @{ DisplayName = 'clash-guard: 回环放行'; Direction = 'Outbound'; Action = 'Allow'; RemoteAddress = @('127.0.0.1') },
  @{ DisplayName = 'clash-guard: 虚拟网卡出站放行'; Direction = 'Outbound'; Action = 'Allow'; LocalAddress = @('198.18.0.0/15') },
  @{ DisplayName = 'clash-guard: 虚拟网卡入站放行'; Direction = 'Inbound'; Action = 'Allow'; LocalAddress = @('198.18.0.0/15') },
  @{ DisplayName = 'clash-guard: DHCP 出站放行'; Direction = 'Outbound'; Action = 'Allow'; Protocol = 'UDP'; LocalPort = '68'; RemotePort = '67' },
  @{ DisplayName = 'clash-guard: DHCP 入站放行'; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'UDP'; LocalPort = '68'; RemotePort = '67' },
  @{ DisplayName = 'clash-guard: 局域网 DNS 拦截 UDP'; Direction = 'Outbound'; Action = 'Block'; Protocol = 'UDP'; RemotePort = '53'; RemoteAddress = $lan },
  @{ DisplayName = 'clash-guard: 局域网 DNS 拦截 TCP'; Direction = 'Outbound'; Action = 'Block'; Protocol = 'TCP'; RemotePort = '53'; RemoteAddress = $lan },
  @{ DisplayName = 'clash-guard: 局域网与链路本地放行'; Direction = 'Outbound'; Action = 'Allow'; RemoteAddress = $lan },
  @{ DisplayName = 'clash-guard: IPv6 邻居发现放行'; Direction = 'Outbound'; Action = 'Allow'; Protocol = 'ICMPv6'; IcmpType = @('133', '134', '135', '136') }
)
foreach ($p in $programs) {
  # 路径暂时不存在（客户端升级中）也保留放行规则：删掉它会把代理核心自己拦住，整机断网
  if ($p) { $rules += @{ DisplayName = ($programPrefix + $p); Direction = 'Outbound'; Action = 'Allow'; Program = $p } }
}

function Norm($values) {
  $items = @()
  foreach ($v in @($values)) {
    if ($null -eq $v) { continue }
    $s = ([string]$v).ToLowerInvariant()
    if ($s -eq '' -or $s -eq 'any') { continue }
    if ($s -match '^(\d+\.\d+\.\d+\.\d+)/(\d+\.\d+\.\d+\.\d+)$') {
      $bits = 0
      foreach ($o in $Matches[2].Split('.')) { $bits += ([Convert]::ToString([int]$o, 2) -replace '0', '').Length }
      $s = $Matches[1] + '/' + $bits
    }
    $items += $s
  }
  if ($items.Count -eq 0) { return 'any' }
  [string[]]$sorted = $items
  [Array]::Sort($sorted, [StringComparer]::Ordinal)
  return ($sorted -join ',')
}

function Expected($r) {
  return (@('dir=' + (Norm $r.Direction), 'action=' + (Norm $r.Action), 'proto=' + (Norm $r.Protocol), 'program=' + (Norm $r.Program),
      'local=' + (Norm $r.LocalAddress), 'remote=' + (Norm $r.RemoteAddress), 'lport=' + (Norm $r.LocalPort),
      'rport=' + (Norm $r.RemotePort), 'icmp=' + (Norm $r.IcmpType)) -join '|')
}

function Actual($rule) {
  $app = $rule | Get-NetFirewallApplicationFilter
  $addr = $rule | Get-NetFirewallAddressFilter
  $port = $rule | Get-NetFirewallPortFilter
  return (@('dir=' + (Norm $rule.Direction), 'action=' + (Norm $rule.Action), 'proto=' + (Norm $port.Protocol), 'program=' + (Norm $app.Program),
      'local=' + (Norm $addr.LocalAddress), 'remote=' + (Norm $addr.RemoteAddress), 'lport=' + (Norm $port.LocalPort),
      'rport=' + (Norm $port.RemotePort), 'icmp=' + (Norm $port.IcmpType)) -join '|')
}

function New-GuardRule($r) {
  $params = @{ Group = $group; Enabled = 'True'; Profile = 'Any' }
  foreach ($k in $r.Keys) { $params[$k] = $r[$k] }
  New-NetFirewallRule @params -ErrorAction Stop | Out-Null
}

$existing = @{}
foreach ($rule in @(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue)) {
  $key = [string]$rule.DisplayName
  if (-not $existing.ContainsKey($key)) { $existing[$key] = @() }
  $existing[$key] += $rule
}
$wanted = @($rules | ForEach-Object { $_.DisplayName })
foreach ($name in @($existing.Keys)) {
  # programs.json 读不出来时，不清理已有的代理程序放行规则
  if (-not $programsOk -and $name.StartsWith($programPrefix)) { continue }
  if ($wanted -notcontains $name) { @($existing[$name]) | Remove-NetFirewallRule -ErrorAction SilentlyContinue; Write-Output ('已移除多余规则：' + $name) }
}
$errors = 0
foreach ($r in $rules) {
  $name = $r.DisplayName
  try {
    if (-not $existing.ContainsKey($name)) {
      New-GuardRule $r; Write-Output ('已补回：' + $name)
      continue
    }
    $current = @($existing[$name])
    if ($current.Count -ne 1 -or (Actual $current[0]) -ne (Expected $r)) {
      $current | Remove-NetFirewallRule -ErrorAction SilentlyContinue
      New-GuardRule $r; Write-Output ('属性被改动或重复，已重建：' + $name)
    } elseif ([string]$current[0].Enabled -ne 'True') {
      Enable-NetFirewallRule -Name ([string]$current[0].Name); Write-Output ('已重新启用：' + $name)
    }
  } catch { $errors++; Write-Output ('处理规则失败：' + $name + ' ' + $_) }
}
# 放行规则就位后再设默认出站 Block（顺序不能反，否则中间有一刻全断）。
# 有规则没补成功、或者一条代理程序放行都没有时，不去碰默认出站：宁可暂时不拦，也不能把代理核心自己拦住。
$haveCore = @(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Where-Object { ([string]$_.DisplayName).StartsWith($programPrefix) -and [string]$_.Enabled -eq 'True' }).Count -gt 0
if ($errors -eq 0 -and $haveCore) {
  Set-NetFirewallProfile -Profile Domain, Private, Public -DefaultOutboundAction Block
} else {
  Write-Output ('有 ' + $errors + ' 条规则失败或没有代理程序放行规则：本次没有设置默认出站 Block')
  if ($errors -eq 0) { $errors = 1 }
}
Write-Output ('clash-guard 规则已就位：' + $rules.Count + ' 条，错误 ' + $errors)
exit $errors
