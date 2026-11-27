# claude-network 一键还原（以管理员身份运行同目录的 restore.cmd，或在管理员 PowerShell 里执行：
#   powershell -ExecutionPolicy Bypass -File "<本文件路径>"）
# 回到备份时（@@STAMP@@）的样子：解除断网开关 → 退出客户端 → 放回整个客户端配置目录 → 还原 IPv6 / DNS 策略 / 系统代理
# → 以普通权限重开客户端 → 确认代理端口真的通了（不通就等端口空出来再开一次）。断网时也能用；中间出错继续往下做。
$ErrorActionPreference = 'Continue'
$Backup = @@BACKUP@@
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:LogFile = Join-Path $Backup ('restore-' + $Stamp + '.log')
@@COMMON@@

if (-not (Test-Admin)) {
  Write-Output '需要管理员权限：请右键 restore.cmd 选「以管理员身份运行」，或在管理员 PowerShell 里执行本脚本。'
  exit 77
}
Write-Log ('==== 一键还原开始，备份：' + $Backup + ' ====')

# 1. 断网开关
Disable-KillSwitch

# 2. 退出客户端
Stop-Client

# 3. 放回客户端配置目录（当前目录改名保留，不删除）
$src = Join-Path $Backup 'verge'
if (Test-Path -LiteralPath $src) {
  if (Test-Path -LiteralPath $VergeDir) {
    try { Rename-Item -LiteralPath $VergeDir -NewName ((Split-Path -Leaf $VergeDir) + '.before-restore-' + $Stamp) -ErrorAction Stop; Write-Log '当前配置目录已改名保留' }
    catch { Write-Log ('改名当前配置目录失败（继续，改为覆盖）：' + $_) }
  }
  # robocopy 退出码 0~7 都是成功；目标目录存在（改名失败）时直接覆盖其中文件
  & robocopy.exe $src $VergeDir /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -lt 8) { Write-Log '已放回备份的配置目录' } else { Write-Log ('放回配置目录有错误（robocopy ' + $LASTEXITCODE + '），继续') }
} else { Write-Log '备份里没有 verge 目录，跳过' }

# 4. 网络：系统代理先关，再按备份时的值还原 IPv6 绑定、DNS 多宿主解析策略、系统代理
Disable-SystemProxy
@@NETWORK_RESTORE@@
Clear-DnsClientCache -ErrorAction SilentlyContinue

# 5. 重开客户端，确认代理端口真的通了
Start-Client
if (-not (Wait-Ready 90)) {
  Write-Log '端口没起来：等端口空出来再开一次'
  Stop-Client
  $n = 0
  while ((Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) -and $n -lt 30) { Start-Sleep -Seconds 2; $n++ }
  Start-Client
  if (-not (Wait-Ready 90)) { Write-Log '第二次仍未就绪' }
}
if (Test-Port) { Write-Log ('代理端口 ' + $Port + ' 已监听') } else { Write-Log ('代理端口 ' + $Port + ' 未监听') }
if (Test-Claude) { Write-Log '还原完成：claude.ai 可访问' } else { Write-Log '还原完成：claude.ai 暂时不通（备份时的配置本身可能就不通，或客户端还在启动）' }
Write-Log ('日志：' + $script:LogFile)
exit 0
