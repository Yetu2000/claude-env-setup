"""在本机对 PowerShell 公共函数做只读冒烟测试（只调用 GET 与测速接口，不改任何设置）。

用法：python tests/live_readonly_ps.py   （需要 Windows + 正在运行的 Clash Verge）
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from cnetlib import controller, gen, nodes, util  # noqa: E402

TAIL = r"""
Write-Output ('admin=' + (Test-Admin))
Write-Output ('pipe=' + (Get-CorePipe))
$r = Invoke-CoreApi 'GET' '/version'; Write-Output ('version ' + $r.Status + ' ' + $r.Body)
$r = Invoke-CoreApi 'GET' ('/proxies/' + '@@GROUP@@'); Write-Output ('group ' + $r.Status + ' ' + $r.Body.Substring(0, [Math]::Min(120, $r.Body.Length)))
$r = Invoke-CoreApi 'GET' '/rules'; Write-Output ('rules ' + $r.Status + ' bytes=' + $r.Body.Length)
Write-Output ('delay ' + (Test-NodeDelay '@@NODE@@'))
$t = Get-TunAdapter; Write-Output ('tun=' + $t.Name)
Write-Output ('port=' + (Test-Port)); Write-Output ('claude=' + (Test-Claude)); Write-Output ('running=' + (Test-ClientRunning))
"""


def main():
    util.configure_stdout()
    if not util.IS_WINDOWS:
        print("只在 Windows 上运行")
        return 0
    c = controller.Controller()
    groups = [n for n, v in c.proxies().items() if v.get("type") == "Selector" and n != "GLOBAL"]
    found, _ = nodes.collect(c)
    ctx = gen.context()
    body = gen.common(ctx) + TAIL.replace("@@GROUP@@", util.url_path_escape(groups[0])).replace("@@NODE@@", util.url_path_escape(found[0]["name"]))
    path = os.path.join(os.environ["TEMP"], "cnet-common-test.ps1")
    util.write_text(path, body, newline="\r\n", bom=True)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    print(result.stdout)
    print(result.stderr[-1500:])
    os.remove(path)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
