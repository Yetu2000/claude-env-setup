# claude-network：安装断网开关（管理员 PowerShell：powershell -ExecutionPolicy Bypass -File "<本文件路径>"）。
# 装好后：代理崩了、被退出、虚拟网卡没了，普通程序的流量出不了物理网卡，只有局域网能通；
# 防火墙配置本身开机即生效，计划任务 clash-guard-killswitch（SYSTEM）开机与每 5 分钟补回被清掉的规则。
# 文件在 C:\ProgramData\clash-guard\，一条命令解除：右键 off.cmd「以管理员身份运行」。
# 安全阀：装之前确认虚拟网卡真的起来、claude.ai 通；装完 60 秒内连不上 claude.ai 就自动解除。
$ErrorActionPreference = 'Continue'
$script:LogFile = @@LOG@@
@@COMMON@@
$errors = 0
function Add-Error([string]$m) { Write-Log ('错误：' + $m); $script:errors++ }

if (-not (Test-Admin)) { Write-Output '需要管理员权限。'; exit 77 }
Write-Log '==== 安装断网开关 ===='

# ---- 0. 前置检查（任何一项不过都不改任何东西）----
$tun = Get-TunAdapter
if (-not $tun) { Write-Log '没有检测到虚拟网卡（198.18.x 地址且已连接）：现在装会让整台电脑立刻断网，拒绝安装'; exit 10 }
Write-Log ('虚拟网卡：' + $tun.Name + '（' + $tun.InterfaceDescription + '）')
$off = @(Get-NetFirewallProfile | Where-Object { -not $_.Enabled })
if ($off.Count -gt 0) { Write-Log ('Windows 防火墙在这些配置文件上是关闭的：' + (($off | ForEach-Object Name) -join ',') + '；第三方安全软件接管防火墙时断网开关无效，拒绝安装'); exit 11 }
if (-not (Test-Claude)) { Write-Log '安装前 claude.ai 就不通：先把代理修好再装，拒绝安装'; exit 12 }
if ((Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue) -or @(Get-NetFirewallRule -Group $FwGroup -ErrorAction SilentlyContinue).Count -gt 0) {
  Write-Log '已安装过：先解除旧的再重装'
  Disable-KillSwitch
}

# ---- 1. 目录与原值备份 ----
New-Item -ItemType Directory -Force -Path $GuardDir, (Join-Path $GuardDir 'state') | Out-Null
& icacls.exe $GuardDir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { Add-Error '设置安装目录权限失败' }
$origFile = Join-Path $GuardDir 'state\original.json'
if (-not (Test-Path -LiteralPath $origFile)) {
  $profiles = @(Get-NetFirewallProfile | ForEach-Object { @{ name = [string]$_.Name; default_outbound = [string]$_.DefaultOutboundAction } })
  (@{ tool = 'clash-guard'; saved = (Get-Date -Format o); profiles = $profiles } | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $origFile -Encoding UTF8
  Write-Log '已记录防火墙默认出站原值'
}

# ---- 2. 代理程序清单（按绝对路径放行）：服务与内核。客户端界面本身不放行——
#      它不经代理直接拉订阅、查更新的流量也该被拦（虚拟网卡在时它的流量本来就走虚拟网卡）----
$programs = @()
if (Test-Path -LiteralPath $App) {
  foreach ($n in 'verge-mihomo.exe', 'verge-mihomo-alpha.exe', 'resources\clash-verge-service.exe', 'clash-verge-service.exe') {
    $c = Join-Path (Split-Path -Parent $App) $n
    if (Test-Path -LiteralPath $c) { $programs += $c }
  }
}
foreach ($proc in @(Get-Process -Name 'verge-mihomo', 'verge-mihomo-alpha', 'clash-verge-service' -ErrorAction SilentlyContinue)) {
  try { if ($proc.Path) { $programs += $proc.Path } } catch {}
}
$svc = Get-CimInstance Win32_Service -Filter "Name='clash_verge_service'" -ErrorAction SilentlyContinue
if ($svc -and $svc.PathName) { $programs += ($svc.PathName -replace '^"([^"]+)".*$', '$1' -replace '^(\S+\.exe).*$', '$1') }
$svcData = Join-Path $env:ProgramData 'clash-verge-service'
if (Test-Path -LiteralPath $svcData) {
  $programs += @(Get-ChildItem -LiteralPath $svcData -Recurse -Filter 'verge-mihomo*.exe' -ErrorAction SilentlyContinue | ForEach-Object FullName)
}
$programs = @($programs | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | ForEach-Object { (Resolve-Path -LiteralPath $_).Path } | Sort-Object -Unique)
if ($programs.Count -eq 0) { Write-Log '找不到代理核心程序：拒绝安装'; exit 13 }
foreach ($p in $programs) { Write-Log ('放行程序：' + $p) }
(ConvertTo-Json -InputObject @($programs)) | Set-Content -LiteralPath (Join-Path $GuardDir 'programs.json') -Encoding UTF8

# ---- 3. 写文件 ----
$applyText = @'
@@GUARD_APPLY@@
'@
$offText = @'
@@GUARD_OFF@@
'@
$utf8Bom = New-Object Text.UTF8Encoding $true
[IO.File]::WriteAllText((Join-Path $GuardDir 'apply.ps1'), $applyText, $utf8Bom)
[IO.File]::WriteAllText((Join-Path $GuardDir 'off.ps1'), $offText, $utf8Bom)
[IO.File]::WriteAllText((Join-Path $GuardDir 'off.cmd'), "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0off.ps1`"`r`npause`r`n", (New-Object Text.UTF8Encoding $false))
[IO.File]::WriteAllText((Join-Path $GuardDir 'README-解除.txt'), "断网开关（clash-guard）`r`n解除：右键 C:\ProgramData\clash-guard\off.cmd →「以管理员身份运行」`r`n兜底（管理员 PowerShell）：`r`n  Unregister-ScheduledTask -TaskName 'clash-guard-killswitch' -Confirm:`$false`r`n  Get-NetFirewallRule -Group 'clash-guard' | Remove-NetFirewallRule`r`n  Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction NotConfigured`r`n", $utf8Bom)
foreach ($f in 'apply.ps1', 'off.ps1') {
  $tokens = $null; $parseErrors = $null
  [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $GuardDir $f), [ref]$tokens, [ref]$parseErrors)
  if ($parseErrors.Count -gt 0) { Add-Error ($f + ' 语法错误：' + $parseErrors[0].Message) }
}
if ($errors -gt 0) { Write-Log '写文件阶段出错，未启用任何规则'; exit 20 }

# ---- 4. 加载规则 ----
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $GuardDir 'apply.ps1') | ForEach-Object { Write-Log ('  ' + $_) }
if ($LASTEXITCODE -ne 0) { Add-Error ('apply.ps1 返回 ' + $LASTEXITCODE); Disable-KillSwitch; exit 21 }

# ---- 5. 计划任务：开机与每 5 分钟补回 ----
try {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $GuardDir 'apply.ps1') + '"')
  $t1 = New-ScheduledTaskTrigger -AtStartup
  $t2 = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $GuardTask -Action $action -Trigger $t1, $t2 -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
  Write-Log ('计划任务已注册：' + $GuardTask)
} catch { Add-Error ('注册计划任务失败：' + $_) }

# ---- 6. 复核（第 8 项的证据写进日志）----
$count = @(Get-NetFirewallRule -Group $FwGroup -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' }).Count
Write-Log ('规则组 ' + $FwGroup + ' 已启用规则：' + $count + ' 条')
if ($count -lt 9) { Add-Error '规则数量不足' }
foreach ($pr in Get-NetFirewallProfile) {
  Write-Log ('配置文件 ' + $pr.Name + '：Enabled=' + $pr.Enabled + ' DefaultOutboundAction=' + $pr.DefaultOutboundAction)
  if ([string]$pr.DefaultOutboundAction -ne 'Block') { Add-Error ($pr.Name + ' 默认出站不是 Block') }
}
$task = Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue
if ($task) { Write-Log ('计划任务状态：' + $task.State) } else { Add-Error '计划任务不存在' }

# ---- 7. 安全阀 ----
if (-not (Wait-Claude 60)) {
  Add-Error '装上后 claude.ai 不通：自动解除断网开关'
  Disable-KillSwitch
  [void](Wait-Claude 60)
  Write-Log ('已解除。错误数：' + $errors)
  exit 30
}
Write-Log ('断网开关安装完成，错误数：' + $errors)
Write-Log ('解除：右键 ' + (Join-Path $GuardDir 'off.cmd') + ' →「以管理员身份运行」')
exit $errors
