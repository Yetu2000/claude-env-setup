# clash-guard 断网开关：一条命令解除（可重复执行）。
#   右键 C:\ProgramData\clash-guard\off.cmd →「以管理员身份运行」
#   或管理员 PowerShell：powershell -ExecutionPolicy Bypass -File "C:\ProgramData\clash-guard\off.ps1"
# 顺序：删计划任务 → 删规则组 → 默认出站恢复为安装前的值（找不到就恢复为系统默认 NotConfigured）→ 复核。
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Write-Output '需要管理员权限。'; exit 77 }
$task = 'clash-guard-killswitch'
$group = 'clash-guard'
$state = 'C:\ProgramData\clash-guard\state\original.json'
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
Write-Output ('已删除计划任务（如存在）：' + $task)
$rules = @(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue)
if ($rules.Count -gt 0) { $rules | Remove-NetFirewallRule -ErrorAction SilentlyContinue }
Write-Output ('已删除规则组 ' + $group + '：' + $rules.Count + ' 条')
$orig = @{}
try { foreach ($i in @((Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json).profiles)) { $orig[[string]$i.name] = [string]$i.default_outbound } } catch {}
foreach ($name in 'Domain', 'Private', 'Public') {
  $value = $orig[$name]
  # 原值是 Block 时多半是重复安装留下的，按系统默认恢复，避免「Block 且无放行」整机断网
  if ($value -ne 'Allow' -and $value -ne 'NotConfigured') { $value = 'NotConfigured' }
  Set-NetFirewallProfile -Name $name -DefaultOutboundAction $value -ErrorAction Continue
  Write-Output ('默认出站 ' + $name + ' → ' + $value)
}
$problems = @()
if (@(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue).Count -gt 0) { $problems += '规则组仍有规则' }
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) { $problems += '计划任务仍在' }
foreach ($name in 'Domain', 'Private', 'Public') {
  if ([string](Get-NetFirewallProfile -Name $name).DefaultOutboundAction -eq 'Block') { $problems += ($name + ' 默认出站仍为 Block') }
}
if ($problems.Count -gt 0) {
  Write-Output ('解除未完全生效：' + ($problems -join '；'))
  Write-Output '兜底（管理员 PowerShell）：'
  Write-Output "  Unregister-ScheduledTask -TaskName 'clash-guard-killswitch' -Confirm:`$false"
  Write-Output "  Get-NetFirewallRule -Group 'clash-guard' | Remove-NetFirewallRule"
  Write-Output '  Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction NotConfigured'
  exit 1
}
Write-Output '断网开关已解除（复核通过）。'
exit 0
