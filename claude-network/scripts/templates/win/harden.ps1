# claude-network：堵住 DNS 与 IPv6 的旁路（管理员 PowerShell：powershell -ExecutionPolicy Bypass -File "<本文件路径>"）
# 为什么要改：
#   - IPv6：核心已关 IPv6、不返回 AAAA；再关掉物理网卡的 IPv6 协议绑定，杜绝写死 IPv6 地址的程序绕过虚拟网卡。
#   - DNS：Windows 默认「智能多宿主名称解析」会同时向所有网卡的 DNS 发查询，路由器/运营商能看到你查的域名；
#     关掉它（组策略 DisableSmartNameResolution=1），配合 TUN 的 strict-route，只走虚拟网卡的 DNS。
# 改完 60 秒内连不上 claude.ai 就自动改回去。
$ErrorActionPreference = 'Continue'
$script:LogFile = @@LOG@@
@@COMMON@@
if (-not (Test-Admin)) { Write-Output '需要管理员权限。'; exit 77 }
Write-Log '==== DNS / IPv6 加固开始 ===='

function Invoke-Revert {
  Write-Log '还原 IPv6 绑定与 DNS 策略到备份时的值'
@@REVERT@@
  Clear-DnsClientCache -ErrorAction SilentlyContinue
}

if (-not (Test-Claude)) { Write-Log '改之前 claude.ai 就不通：不做任何修改'; exit 2 }
$errors = 0
$tun = Get-TunAdapter
# 只改备份里记录过的物理网卡：备份之后新加的网卡不动，保证 restore 能原样改回
$recorded = @(@@ADAPTERS@@)
foreach ($ad in @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue)) {
  if ($tun -and $ad.ifIndex -eq $tun.ifIndex) { continue }
  if ($recorded -notcontains $ad.Name) { Write-Log ('备份里没有这块网卡，IPv6 不改：' + $ad.Name); continue }
  $b = Get-NetAdapterBinding -Name $ad.Name -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue
  if ($b -and $b.Enabled) {
    try { Disable-NetAdapterBinding -Name $ad.Name -ComponentID ms_tcpip6 -ErrorAction Stop; Write-Log ('已关闭网卡 IPv6：' + $ad.Name) }
    catch { Write-Log ('关闭网卡 IPv6 失败：' + $ad.Name + ' ' + $_); $errors++ }
  }
}
$pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient'
try {
  if (-not (Test-Path $pol)) { New-Item -Path $pol -Force | Out-Null }
  Set-ItemProperty -Path $pol -Name DisableSmartNameResolution -Value 1 -Type DWord -ErrorAction Stop
  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters' -Name DisableParallelAandAAAA -Value 1 -Type DWord -ErrorAction Stop
  Write-Log '已关闭智能多宿主名称解析'
} catch { Write-Log ('写 DNS 策略失败：' + $_); $errors++ }
Clear-DnsClientCache -ErrorAction SilentlyContinue

if (Wait-Claude 60) { Write-Log ('加固完成，错误数：' + $errors); exit $errors }
Invoke-Revert
[void](Wait-Claude 60)
Write-Log '加固后 claude.ai 不通，已自动改回原值'
exit 5
