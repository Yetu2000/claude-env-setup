# ---- claude-network 公共函数（生成时内联进每个脚本，脚本可脱离 skill 独立运行）----
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
foreach ($v in 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy') {
  Remove-Item -LiteralPath ("Env:" + $v) -ErrorAction SilentlyContinue
}
$UserName = @@USER@@
$VergeDir = @@VERGE_DIR@@
$App = @@APP@@
$Port = @@PORT@@
$GuardDir = @@GUARD_DIR@@
$FwGroup = @@FW_GROUP@@
$GuardTask = @@TASK_NAME@@
$Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not $script:LogFile) { $script:LogFile = $null }

# 注意：只写主机与日志文件，绝不用 Write-Output——函数里的 Write-Output 会混进返回值，
# 让 if (-not (Wait-Claude)) 这类判断永远为假（数组非空即真），安全阀全部失效。
function Write-Log([string]$Message) {
  $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message
  Write-Host $line
  if ($script:LogFile) { try { Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8 } catch {} }
}

function Test-Admin {
  $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 清空代理变量 + --noproxy '*'：测的是虚拟网卡，不是代理端口。返回 curl 退出码。
function Invoke-CurlCode([string]$Url, [int]$Timeout = 10) {
  & $Curl --noproxy '*' -sS -o NUL --max-time $Timeout $Url 2>$null | Out-Null
  return $LASTEXITCODE
}
function Invoke-CurlBody([string]$Url, [int]$Timeout = 10) {
  $out = & $Curl --noproxy '*' -sS --max-time $Timeout $Url 2>$null
  if ($LASTEXITCODE -ne 0) { return '' }
  return (($out | Out-String).Trim())
}
function Test-Claude { return ((Invoke-CurlCode 'https://claude.ai/cdn-cgi/trace' 10) -eq 0) }

function Wait-Claude([int]$Limit = 120) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $Limit) {
    if (Test-Claude) { Write-Log ('claude.ai 已通（等了 ' + [int]$sw.Elapsed.TotalSeconds + 's）'); return $true }
    Start-Sleep -Seconds 5
  }
  Write-Log ('claude.ai 在 ' + $Limit + 's 内没有连通')
  return $false
}

function Test-Port {
  $c = New-Object Net.Sockets.TcpClient
  try { $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null); if ($iar.AsyncWaitHandle.WaitOne(2000) -and $c.Connected) { return $true } } catch {} finally { $c.Close() }
  return $false
}

function Get-CorePipe {
  # Windows PowerShell 5.1 里 [IO.Directory]::GetFiles 不能枚举管道，Get-ChildItem 可以
  $all = @()
  try { $all = @(Get-ChildItem -Path '\\.\pipe\' -ErrorAction Stop | ForEach-Object { $_.Name }) } catch {}
  if ($all.Count -eq 0) { try { $all = @([IO.Directory]::GetFiles('\\.\pipe\') | ForEach-Object { Split-Path $_ -Leaf }) } catch {} }
  $names = @($all | Where-Object { $_ -like 'verge-mihomo*' })
  if ($names.Count -eq 0) { return $null }
  $prod = @($names | Where-Object { $_ -like '*production*' })
  if ($prod.Count -gt 0) { return $prod[0] }
  return $names[0]
}

# 通过命名管道调用 mihomo 控制器：Invoke-CoreApi 方法 路径 [JSON 正文] → @{Status; Body}
function Invoke-CoreApi([string]$Method, [string]$Path, [string]$Body = '', [int]$TimeoutSec = 15) {
  $pipe = Get-CorePipe
  if (-not $pipe) { return @{ Status = 0; Body = 'no-pipe' } }
  # 异步模式：读操作带超时，核心接受连接却不回应时不会无限卡住（卡住就无法回滚）
  $client = New-Object IO.Pipes.NamedPipeClientStream('.', $pipe, [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous)
  try {
    $client.Connect(3000)
    $payload = [Text.Encoding]::UTF8.GetBytes($Body)
    $head = $Method + ' ' + $Path + " HTTP/1.1`r`nHost: localhost`r`nConnection: close`r`n"
    if ($Body -ne '') { $head += "Content-Type: application/json`r`n" }
    $head += 'Content-Length: ' + $payload.Length + "`r`n`r`n"
    $hb = [Text.Encoding]::ASCII.GetBytes($head)
    $client.Write($hb, 0, $hb.Length)
    if ($payload.Length -gt 0) { $client.Write($payload, 0, $payload.Length) }
    $client.Flush()
    $ms = New-Object IO.MemoryStream
    $buf = New-Object byte[] 65536
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
      $left = [int]($TimeoutSec * 1000 - $sw.ElapsedMilliseconds)
      if ($left -le 0) { break }
      $task = $client.ReadAsync($buf, 0, $buf.Length)
      if (-not $task.Wait($left)) { break }
      $n = $task.Result
      if ($n -le 0) { break }
      $ms.Write($buf, 0, $n)
    }
    $raw = $ms.ToArray()
    # 在字节层面拆头部与正文（chunked 的长度按字节计，中文节点名不能按字符切）
    $idx = -1
    for ($k = 0; $k -le $raw.Length - 4; $k++) {
      if ($raw[$k] -eq 13 -and $raw[$k + 1] -eq 10 -and $raw[$k + 2] -eq 13 -and $raw[$k + 3] -eq 10) { $idx = $k; break }
    }
    if ($idx -lt 0) { return @{ Status = 0; Body = [Text.Encoding]::UTF8.GetString($raw) } }
    $headText = [Text.Encoding]::ASCII.GetString($raw, 0, $idx)
    $status = 0
    [void][int]::TryParse(($headText.Split(' ')[1]), [ref]$status)
    $pos = $idx + 4
    $out = New-Object IO.MemoryStream
    if ($headText -match '(?i)transfer-encoding:\s*chunked') {
      while ($pos -lt $raw.Length) {
        $lineEnd = $pos
        while ($lineEnd -lt $raw.Length - 1 -and -not ($raw[$lineEnd] -eq 13 -and $raw[$lineEnd + 1] -eq 10)) { $lineEnd++ }
        $sizeText = ([Text.Encoding]::ASCII.GetString($raw, $pos, $lineEnd - $pos)).Split(';')[0].Trim()
        if ($sizeText -eq '') { break }
        $size = [Convert]::ToInt32($sizeText, 16)
        if ($size -le 0) { break }
        $start = $lineEnd + 2
        $len = [Math]::Min($size, $raw.Length - $start)
        if ($len -le 0) { break }
        $out.Write($raw, $start, $len)
        $pos = $start + $size + 2
      }
    } else {
      $out.Write($raw, $pos, $raw.Length - $pos)
    }
    return @{ Status = $status; Body = [Text.Encoding]::UTF8.GetString($out.ToArray()) }
  } catch {
    return @{ Status = 0; Body = [string]$_ }
  } finally {
    $client.Dispose()
  }
}

# 用客户端的测速接口确认节点真的通（$NodeUrl 为已 URL 编码的节点名）
function Test-NodeDelay([string]$NodeUrl) {
  $r = Invoke-CoreApi 'GET' ('/proxies/' + $NodeUrl + '/delay?timeout=6000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204') '' 12
  if ($r.Status -eq 200 -and $r.Body -match '"delay"') { Write-Log ('测速通过：' + $r.Body); return $true }
  Write-Log ('测速失败：HTTP ' + $r.Status + ' ' + $r.Body)
  return $false
}

function Select-Node([string]$GroupUrl, [string]$NameJson) {
  $r = Invoke-CoreApi 'PUT' ('/proxies/' + $GroupUrl) ('{"name":' + $NameJson + '}')
  if ($r.Status -eq 204 -or $r.Status -eq 200) { Write-Log ('已切换 ' + $GroupUrl + ' → ' + $NameJson); return $true }
  Write-Log ('切换失败：HTTP ' + $r.Status + ' ' + $r.Body)
  return $false
}

function Get-TunAdapter {
  $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -like '198.18.*' } | Select-Object -First 1
  if (-not $ip) { return $null }
  $ad = Get-NetAdapter -InterfaceIndex $ip.InterfaceIndex -ErrorAction SilentlyContinue
  if ($ad -and $ad.Status -eq 'Up') { return $ad }
  return $null
}

function Test-ClientRunning { return [bool](Get-Process -Name 'clash-verge' -ErrorAction SilentlyContinue) }

function Stop-Client {
  if (Test-ClientRunning) {
    Write-Log '退出 Clash Verge'
    Get-Process -Name 'clash-verge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $n = 0
    while ((Test-ClientRunning) -and $n -lt 15) { Start-Sleep -Seconds 1; $n++ }
  }
  # 非服务模式下的内核随客户端退出；服务模式的内核由服务托管，客户端重开时会用新配置重启它
  Get-Process -Name 'verge-mihomo', 'verge-mihomo-alpha' -ErrorAction SilentlyContinue |
    Where-Object { try { $_.Path -and $_.Path -notlike '*clash-verge-service*' } catch { $false } } |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

# 以登录用户的普通权限启动客户端：管理员脚本里用 explorer.exe 转交，避免客户端以管理员身份运行
function Start-Client {
  Write-Log '启动 Clash Verge'
  if (-not (Test-Path -LiteralPath $App)) { Write-Log ('找不到客户端：' + $App); return }
  if (Test-Admin) {
    Start-Process -FilePath (Join-Path $env:SystemRoot 'explorer.exe') -ArgumentList ('"' + $App + '"')
  } else {
    Start-Process -FilePath $App -WorkingDirectory (Split-Path -Parent $App)
  }
}

# 等代理端口与控制器就绪；超过一半时间还不行就让核心重载一次配置
function Wait-Ready([int]$Limit = 90) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $reloaded = $false
  while ($sw.Elapsed.TotalSeconds -lt $Limit) {
    if ((Test-Port) -and ((Invoke-CoreApi 'GET' '/version' '' 5).Status -eq 200)) {
      Write-Log ('代理端口 ' + $Port + ' 与控制器已就绪（' + [int]$sw.Elapsed.TotalSeconds + 's）')
      return $true
    }
    if (-not $reloaded -and $sw.Elapsed.TotalSeconds -ge ($Limit / 2) -and (Get-CorePipe)) {
      Write-Log '端口还没起来，让核心重载一次配置'
      [void](Invoke-CoreApi 'PUT' '/configs?force=true' '{"path":"","payload":""}')
      $reloaded = $true
    }
    Start-Sleep -Seconds 3
  }
  Write-Log ('代理端口 ' + $Port + ' 在 ' + $Limit + 's 内未就绪')
  return $false
}

# 关掉当前用户的系统代理（客户端被强行退出时，系统代理可能还指着它的端口）
function Disable-SystemProxy {
  $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
  try {
    Set-ItemProperty -Path $key -Name ProxyEnable -Value 0 -Type DWord -ErrorAction Stop
    Write-Log '已关闭系统代理（ProxyEnable=0）'
  } catch { Write-Log ('关闭系统代理失败：' + $_) }
}

# 断网开关解除（off.ps1 不存在时用兜底命令）
function Disable-KillSwitch {
  $off = Join-Path $GuardDir 'off.ps1'
  if (Test-Path -LiteralPath $off) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $off | ForEach-Object { Write-Log ('  ' + $_) }
    if ($LASTEXITCODE -eq 0) { Write-Log '断网开关已解除（off.ps1）'; return }
  }
  Unregister-ScheduledTask -TaskName $GuardTask -Confirm:$false -ErrorAction SilentlyContinue
  Get-NetFirewallRule -Group $FwGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
  Set-NetFirewallProfile -Profile Domain, Private, Public -DefaultOutboundAction NotConfigured -ErrorAction SilentlyContinue
  Write-Log '断网开关已用兜底命令解除'
}
# ---- 公共函数结束 ----
