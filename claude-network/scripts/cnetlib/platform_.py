"""平台事实采集（全部只读、不需要管理员）：网络服务、DNS、IPv6、系统代理、其他代理程序、Tailscale。"""

import getpass
import json
import os
import re

from . import util

OTHER_PROXY_PROCESSES = {
    "darwin": ["Surge", "ClashX", "ClashX Pro", "ClashX Meta", "Mihomo Party", "mihomo-party", "sing-box", "SFM",
               "Stash", "Shadowrocket", "V2rayU", "V2RayXS", "Quantumult X", "Loon", "Hiddify", "FlClash",
               "Tailscale", "tailscaled", "WireGuard", "OpenVPN Connect"],
    "windows": ["v2rayN", "Clash for Windows", "mihomo-party", "Mihomo Party", "sing-box", "nekoray", "nekobox",
                "Hiddify", "FlClash", "clash-win64", "Shadowsocks", "tailscale-ipn", "tailscaled", "wireguard", "openvpn"],
}


def current_user():
    return getpass.getuser()


def user_uid():
    if util.IS_WINDOWS:
        return None
    return os.getuid()


def other_proxy_processes():
    names = OTHER_PROXY_PROCESSES.get(util.platform_name(), [])
    found = []
    if util.IS_MAC:
        code, out, _ = util.run(["/bin/ps", "-axco", "comm"])
        running = set(line.strip() for line in out.splitlines()) if code == 0 else set()
        found = [n for n in names if n in running]
    elif util.IS_WINDOWS:
        code, out, _ = util.powershell("Get-Process | ForEach-Object { $_.ProcessName } | Sort-Object -Unique")
        running = set(line.strip().lower() for line in out.splitlines()) if code == 0 else set()
        found = [n for n in names if n.lower() in running]
    return found


# ---------------- macOS ----------------

def mac_services():
    """返回 [{"name": 服务名, "enabled": bool}]（networksetup 第一行是说明，* 开头表示已停用）。"""
    code, out, _ = util.run(["/usr/sbin/networksetup", "-listallnetworkservices"])
    services = []
    if code != 0:
        return services
    for line in out.splitlines()[1:]:
        line = line.rstrip()
        if not line:
            continue
        enabled = not line.startswith("*")
        services.append({"name": line.lstrip("*"), "enabled": enabled})
    return services


def _mac_proxy(kind, service):
    code, out, _ = util.run(["/usr/sbin/networksetup", "-get" + kind, service])
    info = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        info[key.strip().lower()] = value.strip()
    return {"enabled": info.get("enabled", "No") == "Yes", "server": info.get("server", ""), "port": info.get("port", "")}


def mac_network_state():
    state = []
    for svc in mac_services():
        name = svc["name"]
        _, dns_out, _ = util.run(["/usr/sbin/networksetup", "-getdnsservers", name])
        dns = [line.strip() for line in dns_out.splitlines() if re.match(r"^[0-9a-fA-F:.]+$", line.strip())]
        _, info_out, _ = util.run(["/usr/sbin/networksetup", "-getinfo", name])
        ipv6 = "unknown"
        if re.search(r"^IPv6:\s*Automatic", info_out, re.M):
            ipv6 = "automatic"
        elif re.search(r"^IPv6:\s*Off", info_out, re.M):
            ipv6 = "off"
        elif re.search(r"^IPv6:\s*Manual", info_out, re.M) or "IPv6 IP address" in info_out:
            ipv6 = "manual"
        elif re.search(r"^IPv6:\s*Link-local", info_out, re.I | re.M):
            ipv6 = "linklocal"
        _, auto_out, _ = util.run(["/usr/sbin/networksetup", "-getautoproxyurl", name])
        auto_url = re.search(r"^URL:\s*(.*)$", auto_out, re.M)
        auto_on = re.search(r"^Enabled:\s*Yes", auto_out, re.M) is not None
        state.append({
            "name": name,
            "enabled": svc["enabled"],
            "dns": dns,
            "ipv6": ipv6,
            "web": _mac_proxy("webproxy", name),
            "secureweb": _mac_proxy("securewebproxy", name),
            "socks": _mac_proxy("socksfirewallproxy", name),
            "autoproxy": {"enabled": auto_on, "url": auto_url.group(1).strip() if auto_url else ""},
        })
    return state


def mac_resolvers():
    code, out, _ = util.run(["/usr/sbin/scutil", "--dns"])
    return sorted(set(re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", out))) if code == 0 else []


def mac_pf_has_apple_anchor():
    text = util.read_text("/etc/pf.conf", "") or ""
    return re.search(r'^\s*anchor\s+"com\.apple/\*"', text, re.M) is not None


# ---------------- Windows ----------------

WIN_STATE_SCRIPT = r"""
$o = @{}
$o.ipv6 = @(Get-NetAdapterBinding -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue | ForEach-Object { @{ name = $_.Name; enabled = [bool]$_.Enabled } })
$o.physical = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
$pol = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' -ErrorAction SilentlyContinue
$o.smart_name_resolution = if ($pol -and $null -ne $pol.DisableSmartNameResolution) { [int]$pol.DisableSmartNameResolution } else { $null }
$dc = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters' -ErrorAction SilentlyContinue
$o.parallel_a_aaaa = if ($dc -and $null -ne $dc.DisableParallelAandAAAA) { [int]$dc.DisableParallelAandAAAA } else { $null }
$is = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
$o.proxy = @{ enable = [int]$is.ProxyEnable; server = [string]$is.ProxyServer; override = [string]$is.ProxyOverride; autoconfig = [string]$is.AutoConfigURL }
$o.dns = @(Get-DnsClientServerAddress -ErrorAction SilentlyContinue | Where-Object { $_.ServerAddresses } | ForEach-Object { @{ alias = $_.InterfaceAlias; family = [int]$_.AddressFamily; servers = @($_.ServerAddresses) } })
$o.firewall = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue | ForEach-Object { @{ name = [string]$_.Name; enabled = [bool]$_.Enabled; default_outbound = [string]$_.DefaultOutboundAction } })
$o.tun = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -like '198.18.*' } | ForEach-Object { @{ alias = $_.InterfaceAlias; ip = $_.IPAddress } })
$o.guard_task = [bool](Get-ScheduledTask -TaskName 'clash-guard-killswitch' -ErrorAction SilentlyContinue)
$o.guard_rules = @(Get-NetFirewallRule -Group 'clash-guard' -ErrorAction SilentlyContinue).Count
$o | ConvertTo-Json -Depth 5 -Compress
"""


def win_network_state():
    code, out, err = util.powershell(WIN_STATE_SCRIPT, timeout=90)
    if code != 0 or not out.strip():
        return {"error": (err or out)[:300]}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except ValueError:
        return {"error": "无法解析：" + out[:200]}


def network_state():
    if util.IS_MAC:
        return {"services": mac_network_state(), "resolvers": mac_resolvers()}
    if util.IS_WINDOWS:
        return win_network_state()
    return {}


def tailscale_dns_takeover(state):
    if util.IS_MAC:
        return "100.100.100.100" in (state.get("resolvers") or [])
    for item in state.get("dns", []) or []:
        if "100.100.100.100" in (item.get("servers") or []):
            return True
    return False
