"""Clash Verge Rev：目录、文件、版本、服务模式、开机自启与配置修补。

依据（clash-verge-rev 2.x 的实际文件布局，本机 2.5.6 只读核对）：
  配置目录   Windows %APPDATA%\\io.github.clash-verge-rev.clash-verge-rev
             macOS   ~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev
  verge.yaml       客户端开关（enable_tun_mode / enable_auto_launch / enable_system_proxy /
                   enable_dns_settings / profile_dns_settings.<uid>.enabled ...）
  config.yaml      客户端的 clash 基础配置：mixed-port / ipv6 / log-level / mode / tun 子项；
                   生成运行配置时这些键会盖掉订阅与扩展里的同名键，所以 ipv6 与 tun 必须在这里改
  profiles.yaml    订阅索引；uid=Merge / uid=Script 是全局扩展
  profiles/Script.js  全局扩展脚本：本 skill 把规则、DNS、代理组全部写在这里（订阅更新冲不掉）
  clash-verge.yaml 客户端最近一次生成的运行配置（只读，用于核对与找控制器地址）
"""

import glob
import os
import plistlib
import re

from . import util, yamlmini

APP_DIR_NAME = "io.github.clash-verge-rev.clash-verge-rev"
MAC_APP_CANDIDATES = ["/Applications/Clash Verge.app", os.path.expanduser("~/Applications/Clash Verge.app")]
MAC_SERVICE_LABEL = "io.github.clash-verge-rev.clash-verge-service"
WIN_SERVICE_NAME = "clash_verge_service"
DEFAULT_SCRIPT_TEMPLATE = "function main(config, profileName) {\n  return config;\n}"

# 这些键出现在全局 Merge 里会和本 skill 的脚本冲突（Merge 先于 Script 执行，但 proxies 会被整段替换）。
MERGE_CONFLICT_KEYS = {"proxies", "proxy-groups", "rules", "rule-providers", "dns", "tun", "proxy-providers"}


def config_dir():
    if util.IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.join(util.home(), "AppData", "Roaming")), APP_DIR_NAME)
    return os.path.join(util.home(), "Library", "Application Support", APP_DIR_NAME)


def files(directory=None):
    directory = directory or config_dir()
    return {
        "dir": directory,
        "verge": os.path.join(directory, "verge.yaml"),
        "config": os.path.join(directory, "config.yaml"),
        "profiles": os.path.join(directory, "profiles.yaml"),
        "runtime": os.path.join(directory, "clash-verge.yaml"),
        "merge": os.path.join(directory, "profiles", "Merge.yaml"),
        "script": os.path.join(directory, "profiles", "Script.js"),
        "dns_config": os.path.join(directory, "dns_config.yaml"),
    }


def app_path():
    """客户端程序路径：Windows 返回 clash-verge.exe，macOS 返回 .app 包路径。"""
    if util.IS_MAC:
        for candidate in MAC_APP_CANDIDATES:
            if os.path.isdir(candidate):
                return candidate
        return None
    candidates = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        candidates.append(os.path.join(base, "Clash Verge", "clash-verge.exe"))
        candidates.append(os.path.join(base, "Programs", "Clash Verge", "clash-verge.exe"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    code, out, _ = util.powershell(
        "Get-Process clash-verge -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path"
    )
    path = out.strip()
    if code == 0 and path and os.path.isfile(path):
        return path
    return None


def core_paths():
    """客户端自带内核的候选路径（只读）。"""
    app = app_path()
    paths = []
    if not app:
        return paths
    if util.IS_MAC:
        for name in ("verge-mihomo", "verge-mihomo-alpha"):
            paths.append(os.path.join(app, "Contents", "MacOS", name))
    else:
        base = os.path.dirname(app)
        for name in ("verge-mihomo.exe", "verge-mihomo-alpha.exe"):
            paths.append(os.path.join(base, name))
    return [path for path in paths if os.path.isfile(path)]


def version():
    app = app_path()
    if not app:
        return None
    if util.IS_MAC:
        try:
            with open(os.path.join(app, "Contents", "Info.plist"), "rb") as handle:
                return plistlib.load(handle).get("CFBundleShortVersionString")
        except (OSError, ValueError):
            return None
    code, out, _ = util.powershell("(Get-Item -LiteralPath " + util.ps_quote(app) + ").VersionInfo.ProductVersion")
    return out.strip() or None if code == 0 else None


def is_running():
    if util.IS_MAC:
        code, out, _ = util.run(["/usr/bin/pgrep", "-x", "clash-verge"])
        return code == 0 and out.strip() != ""
    code, out, _ = util.powershell("@(Get-Process clash-verge -ErrorAction SilentlyContinue).Count")
    return code == 0 and out.strip() not in ("", "0")


def service_installed():
    """服务模式是否已安装（TUN 需要它）。返回 (True/False/None, 说明)。"""
    if util.IS_MAC:
        exists = os.path.exists("/Library/LaunchDaemons/" + MAC_SERVICE_LABEL + ".plist")
        code, _, _ = util.run(["/bin/launchctl", "print", "system/" + MAC_SERVICE_LABEL])
        if exists or code == 0:
            return True, "launchd " + MAC_SERVICE_LABEL + ("（已加载）" if code == 0 else "（plist 在，未确认加载）")
        return False, "未发现 " + MAC_SERVICE_LABEL
    code, out, _ = util.powershell(
        "$s=Get-Service " + WIN_SERVICE_NAME + " -ErrorAction SilentlyContinue; if($s){[string]$s.Status}else{'absent'}"
    )
    status = out.strip()
    if code != 0 or status == "":
        return None, "无法查询服务"
    if status == "absent":
        return False, "未安装 Windows 服务 " + WIN_SERVICE_NAME
    return status == "Running", "Windows 服务 " + WIN_SERVICE_NAME + " 状态 " + status


def auto_launch_registered():
    """客户端是否真的注册了开机自启（verge.yaml 的开关只是意图）。返回 (True/False/None, 证据)。"""
    if util.IS_MAC:
        agents = glob.glob(os.path.expanduser("~/Library/LaunchAgents/*")) + glob.glob("/Library/LaunchAgents/*")
        hits = [path for path in agents if re.search(r"clash[ -]?verge", os.path.basename(path), re.I)]
        if hits:
            return True, "LaunchAgent：" + ", ".join(hits)
        return None, "未在 LaunchAgents 找到 Clash Verge；新版 macOS 可能登记在「登录项」，需在 系统设置→通用→登录项 核对"
    script = (
        "$hits=@();"
        "foreach($k in 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'){"
        "$p=Get-ItemProperty $k -ErrorAction SilentlyContinue; if($p){foreach($n in $p.PSObject.Properties){"
        "if(([string]$n.Value) -match 'clash-verge'){$hits+=('Run:'+$n.Name)}}}};"
        "$sf=[Environment]::GetFolderPath('Startup'); Get-ChildItem -LiteralPath $sf -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Name -match 'Clash Verge'} | ForEach-Object {$hits+=('Startup:'+$_.Name)};"
        "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {$_.TaskName -match 'Clash Verge'} | "
        "ForEach-Object {$hits+=('Task:'+$_.TaskName+':'+$_.State)};"
        "$hits -join ';'"
    )
    code, out, _ = util.powershell(script)
    if code != 0:
        return None, "查询失败"
    text = out.strip()
    if text:
        return True, text
    return False, "Run 注册表、启动文件夹、计划任务里都没有 Clash Verge"


def read_state(directory=None):
    """读取客户端开关与当前订阅。全部只读。"""
    paths = files(directory)
    verge_text = util.read_text(paths["verge"], "") or ""
    config_text = util.read_text(paths["config"], "") or ""
    profiles_text = util.read_text(paths["profiles"], "") or ""
    profiles = yamlmini.parse_verge_profiles(profiles_text)
    state = {
        "dir": paths["dir"],
        "exists": os.path.isdir(paths["dir"]),
        "enable_tun_mode": yamlmini.get_top_scalar(verge_text, "enable_tun_mode"),
        "enable_auto_launch": yamlmini.get_top_scalar(verge_text, "enable_auto_launch"),
        "enable_silent_start": yamlmini.get_top_scalar(verge_text, "enable_silent_start"),
        "enable_system_proxy": yamlmini.get_top_scalar(verge_text, "enable_system_proxy"),
        "enable_dns_settings": yamlmini.get_top_scalar(verge_text, "enable_dns_settings"),
        "profile_dns_override_on": bool(re.search(r"^profile_dns_settings:[\s\S]*?^\s{4}enabled:\s*true", verge_text, re.M)),
        "clash_core": yamlmini.get_top_scalar(verge_text, "clash_core"),
        "mixed_port": yamlmini.get_top_scalar(verge_text, "verge_mixed_port")
        or yamlmini.get_top_scalar(config_text, "mixed-port")
        or 7897,
        "ipv6": yamlmini.get_top_scalar(config_text, "ipv6"),
        "mode": yamlmini.get_top_scalar(config_text, "mode"),
        "log_level": yamlmini.get_top_scalar(config_text, "log-level"),
        "tun_stack": yamlmini.get_child_scalar(config_text, "tun", "stack"),
        "tun_strict_route": yamlmini.get_child_scalar(config_text, "tun", "strict-route"),
        "controller_pipe": yamlmini.get_top_scalar(config_text, "external-controller-pipe"),
        "controller_unix": yamlmini.get_top_scalar(config_text, "external-controller-unix"),
        "profile_current": profiles.get("current"),
        "profile_items": profiles.get("items", []),
    }
    current = None
    for item in state["profile_items"]:
        if item.get("uid") == state["profile_current"]:
            current = item
    state["current_profile"] = current
    return state


def extension_findings(state, directory=None):
    """检查全局扩展和当前订阅自己的增强文件：非默认内容会影响或覆盖本 skill 的脚本。"""
    paths = files(directory)
    findings = []
    merge_text = util.read_text(paths["merge"], "")
    if merge_text is not None and not yamlmini.is_effectively_empty_yaml(merge_text):
        conflict = sorted(set(yamlmini.top_level_keys(merge_text)) & MERGE_CONFLICT_KEYS)
        if conflict:
            findings.append({"level": "warn", "file": paths["merge"],
                             "msg": "全局 Merge 含 " + ",".join(conflict) + "，会改写订阅节点/规则；需先和用户确认是否清掉"})
    script_text = util.read_text(paths["script"], "")
    if script_text and "claude-network begin" not in script_text:
        normalized = re.sub(r"//[^\n]*", "", script_text).strip()
        if re.sub(r"\s+", "", normalized) != re.sub(r"\s+", "", DEFAULT_SCRIPT_TEMPLATE):
            findings.append({"level": "warn", "file": paths["script"],
                             "msg": "全局 Script.js 不是默认模板：本 skill 会整份替换它（旧内容在备份里），需先告诉用户"})
    current = state.get("current_profile") or {}
    option = current.get("option") or {}
    for kind in ("merge", "script", "rules", "proxies", "groups"):
        uid = option.get(kind)
        if not uid:
            continue
        item = next((i for i in state.get("profile_items", []) if i.get("uid") == uid), None)
        if not item or not item.get("file"):
            continue
        path = os.path.join(paths["dir"], "profiles", item["file"])
        text = util.read_text(path, "") or ""
        if kind == "script":
            body = re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", text))
            if body and body != re.sub(r"\s+", "", DEFAULT_SCRIPT_TEMPLATE):
                findings.append({"level": "warn", "file": path, "msg": "当前订阅自带的增强脚本不是默认模板，可能在全局脚本之后改写规则"})
        elif not yamlmini.is_effectively_empty_yaml(text):
            meaningful = [k for k in yamlmini.top_level_keys(text) if k not in ("profile",)]
            if kind in ("rules", "proxies", "groups"):
                if re.search(r"^(prepend|append|delete):\s*\n\s*-", text, re.M) or re.search(r"^(prepend|append|delete):\s*\[.+\]", text, re.M):
                    findings.append({"level": "warn", "file": path, "msg": "当前订阅的 " + kind + " 增强文件有内容（prepend/append/delete），会在全局脚本之后生效"})
            elif meaningful:
                findings.append({"level": "warn", "file": path, "msg": "当前订阅的 Merge 增强有 " + ",".join(meaningful) + "，会在全局脚本之后生效"})
    return findings


def patch_config_yaml(text, platform):
    """客户端 clash 基础配置：IPv6 关、规则模式、info 日志、TUN 子项（enable 由 verge.yaml 控制）。"""
    text = yamlmini.set_top_scalar(text, "ipv6", False)
    text = yamlmini.set_top_scalar(text, "mode", "rule")
    text = yamlmini.set_top_scalar(text, "log-level", "info")
    text = yamlmini.set_top_scalar(text, "allow-lan", False)
    text = yamlmini.set_child(text, "tun", "stack", "gvisor")
    text = yamlmini.set_child(text, "tun", "auto-route", True)
    text = yamlmini.set_child(text, "tun", "auto-detect-interface", True)
    # Windows 必须 strict-route：否则系统会从物理网卡向路由器发 DNS（多宿主解析），造成 DNS 泄漏。
    text = yamlmini.set_child(text, "tun", "strict-route", platform == "windows")
    text = yamlmini.set_child(text, "tun", "dns-hijack", ["any:53", "tcp://any:53"])
    return text


def patch_verge_yaml(text):
    """客户端开关：开 TUN、开开机自启、关系统代理、关 DNS 覆写（全局与每个订阅）。"""
    text = yamlmini.set_top_scalar(text, "enable_tun_mode", True)
    text = yamlmini.set_top_scalar(text, "enable_auto_launch", True)
    text = yamlmini.set_top_scalar(text, "enable_system_proxy", False)
    text = yamlmini.set_top_scalar(text, "enable_dns_settings", False)
    text = yamlmini.map_block_set_all(text, "profile_dns_settings", "enabled", False)
    return text
