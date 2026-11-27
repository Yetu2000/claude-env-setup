# claude-network：故障演练 9~11（管理员 PowerShell：powershell -ExecutionPolicy Bypass -File "<本文件路径>"）。
# 三个演练写在同一个脚本里；每个演练结束先自动恢复、确认 claude.ai 通了再做下一个；不管中途成败，结束时回到正常状态。
# 说明：Windows 防火墙按程序与地址判定，不区分管理员与普通用户，所以这里由本脚本直接跑 curl.exe 即可测准
#      （curl.exe 不在放行清单里，和普通程序待遇相同）。
#   9  海外出口切到连不上的节点：claude.ai、api.ipify.org 必须失败（不能换节点、不能变成本地 IP），baidu.com 照常
#   10 关掉虚拟网卡：curl 外网必须被拒（exit 7 或 28）
#   11 退出客户端等 3 秒：curl 外网必须被拒；然后重开，claude.ai 恢复
$ErrorActionPreference = 'Continue'
$script:LogFile = @@LOG@@
@@COMMON@@
$Result = @@RESULT@@
$OverseasUrl = @@OVERSEAS_URL@@
$DrillJson = @@DRILL_JSON@@
$NormalJson = @@NORMAL_JSON@@
$pass = @{ 9 = 'FAIL'; 10 = 'FAIL'; 11 = 'FAIL' }

if (-not (Test-Admin)) { Write-Output '需要管理员权限。'; exit 77 }
Write-Log '==== 故障演练 9~11 ===='

function Restore-Normal {
  if (-not (Test-ClientRunning)) { Start-Client }
  [void](Wait-Ready 120)
  [void](Invoke-CoreApi 'PATCH' '/configs' '{"tun":{"enable":true}}')
  [void](Select-Node $OverseasUrl $NormalJson)
  return (Wait-Claude 120)
}

if (-not (Wait-Claude 30)) { Write-Log '演练前 claude.ai 就不通：不做演练'; exit 2 }
if (-not (Get-TunAdapter)) { Write-Log '虚拟网卡不在：先修好再演练'; exit 3 }
if (-not (Get-ScheduledTask -TaskName $GuardTask -ErrorAction SilentlyContinue)) { Write-Log '注意：断网开关没装，10、11 两项预期会失败' }

# ---------- 演练 9 ----------
Write-Log '-- 演练 9：海外出口切到连不上的节点 --'
if (Select-Node $OverseasUrl $DrillJson) {
  Start-Sleep -Seconds 2
  $c1 = Invoke-CurlCode 'https://claude.ai/cdn-cgi/trace' 8
  $ip2 = Invoke-CurlBody 'https://api.ipify.org' 8
  $c3 = Invoke-CurlCode 'https://www.baidu.com' 8
  Write-Log ('claude.ai exit=' + $c1 + '；api.ipify.org 返回=[' + $ip2 + ']；baidu.com exit=' + $c3)
  if ($c1 -ne 0 -and $ip2 -eq '' -and $c3 -eq 0) { $pass[9] = 'PASS' }
}
if (-not (Restore-Normal)) { Write-Log '演练 9 后恢复失败' }

# ---------- 演练 10 ----------
Write-Log '-- 演练 10：关掉虚拟网卡 --'
[void](Invoke-CoreApi 'PATCH' '/configs' '{"tun":{"enable":false}}')
$n = 0
while ((Get-TunAdapter) -and $n -lt 10) { Start-Sleep -Seconds 1; $n++ }
if (Get-TunAdapter) {
  Write-Log '虚拟网卡没有关掉，演练 10 无法进行'
} else {
  $codes = @((Invoke-CurlCode 'https://api.ipify.org' 8), (Invoke-CurlCode 'https://www.baidu.com' 8), (Invoke-CurlCode 'http://1.1.1.1' 8))
  Write-Log ('虚拟网卡已关：api.ipify.org / baidu.com / 1.1.1.1 exit=' + ($codes -join ' / ') + '（期望 7 或 28）')
  if (@($codes | Where-Object { $_ -ne 7 -and $_ -ne 28 }).Count -eq 0) { $pass[10] = 'PASS' }
}
if (-not (Restore-Normal)) { Write-Log '演练 10 后恢复失败' }

# ---------- 演练 11 ----------
Write-Log '-- 演练 11：退出客户端 --'
Stop-Client
Get-Process -Name 'verge-mihomo', 'verge-mihomo-alpha' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
if (Get-TunAdapter) {
  Write-Log '退出后虚拟网卡仍在（核心可能被服务拉起）：本项判为未通过，需人工复查'
} else {
  $codes = @((Invoke-CurlCode 'https://api.ipify.org' 8), (Invoke-CurlCode 'https://www.baidu.com' 8))
  Write-Log ('客户端已退出：api.ipify.org / baidu.com exit=' + ($codes -join ' / ') + '（期望 7 或 28）')
  if (@($codes | Where-Object { $_ -ne 7 -and $_ -ne 28 }).Count -eq 0) { $pass[11] = 'PASS' }
}
Disable-SystemProxy
if (-not (Restore-Normal)) { Write-Log '演练 11 后恢复失败' }

$final = if (Test-Claude) { '正常' } else { 'claude.ai 不通' }
Write-Log ('==== 结果：演练9=' + $pass[9] + ' 演练10=' + $pass[10] + ' 演练11=' + $pass[11] + '；结束时状态：' + $final + ' ====')
Set-Content -LiteralPath $Result -Encoding UTF8 -Value @('drill9=' + $pass[9], 'drill10=' + $pass[10], 'drill11=' + $pass[11], 'final=' + $final)
exit 0
