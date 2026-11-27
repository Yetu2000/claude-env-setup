# claude-network：切换到新配置（不需要管理员；由一次性计划任务在你的桌面会话里后台运行，
# AI 断线也会自己跑完、自己恢复）。
# 流程：确认备份完整 → 退出客户端 → 替换 3 个文件 → 重开 → 等端口 → 测速确认节点通 → 选节点
#       → 2 分钟内连不上 claude.ai 就自动还原这 3 个文件并重开。脚本结束时网一定是通的。
$ErrorActionPreference = 'Continue'
$script:LogFile = @@LOG@@
@@COMMON@@
$Backup = @@BACKUP@@
$Stage = @@STAGE@@
$Result = @@RESULT@@
$Mode = @@MODE@@
$OverseasUrl = @@OVERSEAS_URL@@
$JumpGroupUrl = @@JUMPGROUP_URL@@
$ExitUrl = @@EXIT_URL@@
$ExitJson = @@EXIT_JSON@@
$JumpUrl = @@JUMP_URL@@
$JumpJson = @@JUMP_JSON@@
$ResUrl = @@RES_URL@@
$ResJson = @@RES_JSON@@
$SelfTask = @@SELF_TASK@@
$Files = @('verge.yaml', 'config.yaml', 'profiles\Script.js')

function Finish([string]$Text, [int]$Code) {
  Set-Content -LiteralPath $Result -Value $Text -Encoding UTF8
  Write-Log ('结果：' + $Text)
  if ($SelfTask) { Unregister-ScheduledTask -TaskName $SelfTask -Confirm:$false -ErrorAction SilentlyContinue }
  exit $Code
}

function Copy-Set([string]$From, [switch]$Strict) {
  # -Strict：来源里没有的文件在目标里删掉（还原成「原来没有」）
  foreach ($f in $Files) {
    $src = Join-Path $From $f
    $dst = Join-Path $VergeDir $f
    if (Test-Path -LiteralPath $src) {
      try { Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop }
      catch { Write-Log ('复制 ' + $f + ' 失败：' + $_) }
    } elseif ($Strict -and (Test-Path -LiteralPath $dst)) {
      Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue
      Write-Log ('备份里没有 ' + $f + '，已删除新写入的那份')
    }
  }
}

function Invoke-Rollback([string]$Why) {
  Write-Log ('==== 失败，自动还原到备份时的 3 个文件：' + $Why + ' ====')
  Stop-Client
  Copy-Set (Join-Path $Backup 'verge') -Strict
  Start-Client
  [void](Wait-Ready 120)
  if (Wait-Claude 120) { Finish ('ROLLED_BACK: ' + $Why + '（已还原，claude.ai 通）') 3 }
  Finish ('ROLLED_BACK_BUT_OFFLINE: ' + $Why + '（已还原但 claude.ai 仍不通，请按桌面「断网急救.txt」处理）') 4
}

Write-Log ('==== claude-network switch 开始（方案 ' + $Mode + '）====')
Set-Content -LiteralPath $Result -Value 'RUNNING' -Encoding UTF8
if (-not (Test-Path -LiteralPath (Join-Path $Backup 'BACKUP_OK'))) { Finish ('ABORTED: 备份未完成（缺 BACKUP_OK），没有改任何东西') 2 }
foreach ($f in $Files) {
  if (-not (Test-Path -LiteralPath (Join-Path $Stage $f))) { Finish ('ABORTED: 预备文件缺失 ' + $f + '，没有改任何东西') 2 }
}

# 1. 先退出：客户端运行时会把内存里的配置写回磁盘，开着改会被冲掉
Stop-Client
# 2. 替换
Copy-Set $Stage
Write-Log '已写入新的 verge.yaml / config.yaml / profiles\Script.js'
# 3. 重开并等端口
Start-Client
if (-not (Wait-Ready 120)) { Invoke-Rollback '代理端口 / 控制器 120 秒内没有就绪' }
Start-Sleep -Seconds 3

# 4. 切节点之前先测速：海外出口里没有直连，选了死节点等于海外全断
if ($Mode -eq 'A') {
  if (-not (Test-NodeDelay $ExitUrl)) { Invoke-Rollback '海外出口节点测速不通' }
  if (-not (Select-Node $OverseasUrl $ExitJson)) { Invoke-Rollback '切换海外出口失败' }
} else {
  if (-not (Test-NodeDelay $JumpUrl)) { Invoke-Rollback '跳板节点测速不通' }
  if (-not (Select-Node $JumpGroupUrl $JumpJson)) { Invoke-Rollback '切换跳板失败' }
  if (-not (Select-Node $OverseasUrl $ResJson)) { Invoke-Rollback '切换海外出口失败' }
  if (-not (Test-NodeDelay $ResUrl)) { Invoke-Rollback '住宅出口（经跳板）测速不通：检查住宅代理的 IP、端口、账号、密码、白名单与有效期' }
}

# 5. 经虚拟网卡访问 claude.ai（重载后 fake-ip 会变，系统缓存的旧地址短暂连不上是正常的）
if (-not (Wait-Claude 120)) { Invoke-Rollback '2 分钟内连不上 claude.ai' }
$trace = (Invoke-CurlBody 'https://claude.ai/cdn-cgi/trace' 10) -split "`n" | Where-Object { $_ -match '^(ip|loc)=' }
Finish ('OK: 新配置已生效，' + ($trace -join ' ')) 0
