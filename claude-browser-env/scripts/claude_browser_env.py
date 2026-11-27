#!/usr/bin/env python3
"""Claude 浏览器环境、时区与语言：审计、计划、执行与回滚（Windows / macOS）。

做三件事：
1. 浏览器：默认浏览器是谁、装了哪些普通浏览器和指纹浏览器、每个浏览器资料（Profile）的语言、
   WebRTC 防护（托管策略 / 防泄漏扩展 / Firefox 偏好）、是否留有 claude.ai 的 Cookie 与访问记录（只计数）。
2. 出口：分别探测「直连（走 TUN）」和「走系统代理」两条路径的出口 IP、国家、城市、时区。
3. 系统：当前时区、国家或地区、语言列表、自动设置时区开关；按出口给出目标值，并可由用户执行对齐。

原则：--audit / --plan 只读，可以交给 AI 运行；--apply / --rollback 必须在真实终端里由用户输入 CONFIRM / RESTORE。
执行前把原值写进 ~/ClaudeBrowserEnv/<时间戳>/manifest.json，可以 --rollback 整批还原。
不读 Cookie 的值、不读密码、不改浏览器文件、不装扩展、不改网络与代理设置。只用 Python 标准库（3.9+）。
"""

from __future__ import annotations

import argparse
import configparser
import dataclasses
import datetime as dt
import json
import os
import pathlib
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

VERSION = "0.1.0"
BATCH_ROOT_NAME = "ClaudeBrowserEnv"
CLAUDE_HOSTS = ("claude.ai", "claude.com", "anthropic.com")

WEBRTC_PROTECT_ID = "bkmmlbllpjdpgcgdohbaghfaecnddhni"
WEBRTC_PROTECT_URL = "https://chromewebstore.google.com/detail/webrtc-protect-protect-ip/" + WEBRTC_PROTECT_ID
# 能管住 WebRTC 的常见扩展（Chrome 应用商店 ID）。第一个是本 skill 推荐的。
WEBRTC_EXTENSIONS = {
    WEBRTC_PROTECT_ID: "WebRTC Protect",
    "eiadekoaikejlgdbkbdfeijglgfdalml": "WebRTC Leak Prevent",
    "fjkmabmdepjfammlpliljpnbhleegehm": "WebRTC Control",
    "cjpalhdlnbpafiaodnhcjpidfbjhhkga": "uBlock Origin（需在设置里勾选防 WebRTC 泄露）",
}
WEBRTC_SAFE_POLICIES = {"disable_non_proxied_udp"}
WEBRTC_PARTIAL_POLICIES = {"default_public_interface_only", "default_public_and_private_interfaces"}

# --------------------------------------------------------------------------- 浏览器目录

CHROMIUM_BROWSERS = (
    # id, 名称, Windows (环境变量, 相对路径), macOS ~/Library/Application Support 下的相对路径, 策略注册表键, macOS 受管偏好域
    ("chrome", "Google Chrome", ("LOCALAPPDATA", r"Google\Chrome\User Data"), "Google/Chrome", r"SOFTWARE\Policies\Google\Chrome", "com.google.Chrome"),
    ("edge", "Microsoft Edge", ("LOCALAPPDATA", r"Microsoft\Edge\User Data"), "Microsoft Edge", r"SOFTWARE\Policies\Microsoft\Edge", "com.microsoft.Edge"),
    ("brave", "Brave", ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\User Data"), "BraveSoftware/Brave-Browser", r"SOFTWARE\Policies\BraveSoftware\Brave", "com.brave.Browser"),
    ("chromium", "Chromium", ("LOCALAPPDATA", r"Chromium\User Data"), "Chromium", r"SOFTWARE\Policies\Chromium", "org.chromium.Chromium"),
    ("vivaldi", "Vivaldi", ("LOCALAPPDATA", r"Vivaldi\User Data"), "Vivaldi", None, None),
    ("opera", "Opera", ("APPDATA", r"Opera Software\Opera Stable"), "com.operasoftware.Opera", None, None),
    ("360se", "360 安全浏览器", ("APPDATA", r"360se6\User Data"), None, None, None),
    ("360chrome", "360 极速浏览器", ("LOCALAPPDATA", r"360Chrome\Chrome\User Data"), None, None, None),
    ("qqbrowser", "QQ 浏览器", ("LOCALAPPDATA", r"Tencent\QQBrowser\User Data"), None, None, None),
)

BROWSER_NAMES = {row[0]: row[1] for row in CHROMIUM_BROWSERS}
BROWSER_NAMES.update({"firefox": "Firefox", "safari": "Safari", "ie": "Internet Explorer", "sogou": "搜狗浏览器"})

# Windows 默认浏览器 ProgId 前缀 → 浏览器
WIN_PROGID_PREFIXES = (
    ("ChromeHTML", "chrome"), ("ChromiumHTM", "chromium"), ("MSEdgeHTM", "edge"), ("MSEdgeBHTML", "edge"),
    ("MSEdgeDHTML", "edge"), ("FirefoxURL", "firefox"), ("FirefoxHTML", "firefox"), ("BraveHTML", "brave"),
    ("VivaldiHTM", "vivaldi"), ("Opera", "opera"), ("360SeSES", "360se"), ("360se", "360se"),
    ("360ChromeURL", "360chrome"), ("QQBrowser", "qqbrowser"), ("SogouExplorer", "sogou"), ("IE.HTTP", "ie"),
)
WIN_EXE_HINTS = (
    ("chrome.exe", "chrome"), ("msedge.exe", "edge"), ("firefox.exe", "firefox"), ("brave.exe", "brave"),
    ("vivaldi.exe", "vivaldi"), ("opera.exe", "opera"), ("launcher.exe", "opera"), ("360se.exe", "360se"),
    ("360chrome.exe", "360chrome"), ("qqbrowser.exe", "qqbrowser"), ("sogouexplorer.exe", "sogou"), ("iexplore.exe", "ie"),
)
MAC_BUNDLE_IDS = {
    "com.google.chrome": "chrome", "com.google.chrome.beta": "chrome", "com.google.chrome.canary": "chrome",
    "com.microsoft.edgemac": "edge", "org.mozilla.firefox": "firefox", "org.mozilla.firefoxdeveloperedition": "firefox",
    "com.apple.safari": "safari", "com.brave.browser": "brave", "org.chromium.chromium": "chromium",
    "com.vivaldi.vivaldi": "vivaldi", "com.operasoftware.opera": "opera",
}

# 指纹浏览器：(id, 名称, 匹配关键字（小写）, macOS 应用名, 数据目录名)
FINGERPRINT_BROWSERS = (
    ("adspower", "AdsPower", ("adspower",), ("AdsPower Global.app", "AdsPower.app"), ("adspower_global", "adspower")),
    ("bitbrowser", "比特浏览器 BitBrowser", ("bitbrowser", "比特浏览器"), ("BitBrowser.app", "比特浏览器.app"), ("BitBrowser",)),
    ("hubstudio", "Hubstudio", ("hubstudio",), ("Hubstudio.app",), ("Hubstudio",)),
    ("morelogin", "MoreLogin", ("morelogin",), ("MoreLogin.app",), ("MoreLogin",)),
    ("multilogin", "Multilogin", ("multilogin",), ("Multilogin.app", "Multilogin X.app"), ("Multilogin",)),
    ("gologin", "GoLogin", ("gologin",), ("GoLogin.app",), ("GoLogin",)),
    ("dolphin", "Dolphin Anty", ("dolphin anty", "dolphin{anty}", "dolphin_anty"), ("Dolphin Anty.app",), ("dolphin_anty",)),
    ("incogniton", "Incogniton", ("incogniton",), ("Incogniton.app",), ("Incogniton",)),
    ("vmlogin", "VMLogin", ("vmlogin",), ("VMLogin.app",), ("VMLogin",)),
    ("kameleo", "Kameleo", ("kameleo",), ("Kameleo.app",), ("Kameleo",)),
    ("octo", "Octo Browser", ("octo browser",), ("Octo Browser.app",), ("Octo Browser",)),
    ("ziniao", "紫鸟浏览器", ("紫鸟", "ziniao", "superbrowser"), ("紫鸟浏览器.app", "SuperBrowser.app"), ("SuperBrowser",)),
    ("roxybrowser", "RoxyBrowser", ("roxybrowser",), ("RoxyBrowser.app",), ("RoxyBrowser",)),
)

# --------------------------------------------------------------------------- 地区知识

# Windows GeoID（Set-WinHomeLocation 用）。数值由 [Globalization.RegionInfo]::new(<CC>).GeoId 核对过，
# 注意它不是 ISO 数字代码（例如新加坡是 215 而不是 702，日本是 122 而不是 392）。
GEO_IDS = {
    "US": 244, "CA": 39, "MX": 166, "BR": 32, "AR": 11, "CL": 46, "CO": 51, "PE": 187, "GB": 242, "IE": 68,
    "DE": 94, "FR": 84, "NL": 176, "BE": 21, "CH": 223, "AT": 14, "IT": 118, "ES": 217, "PT": 193, "SE": 221,
    "NO": 177, "DK": 61, "FI": 77, "PL": 191, "CZ": 75, "HU": 109, "RO": 200, "GR": 98, "TR": 235, "RU": 203,
    "UA": 241, "AE": 224, "SA": 205, "IL": 117, "IN": 113, "PK": 190, "TH": 227, "VN": 251, "ID": 111, "MY": 167,
    "SG": 215, "PH": 201, "HK": 104, "MO": 151, "TW": 237, "JP": 122, "KR": 134, "AU": 12, "NZ": 183, "ZA": 209,
    "EG": 67, "NG": 175, "CN": 45,
}

COUNTRY_NAMES = {
    "US": "美国", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西", "AR": "阿根廷", "CL": "智利", "CO": "哥伦比亚", "PE": "秘鲁",
    "GB": "英国", "IE": "爱尔兰", "DE": "德国", "FR": "法国", "NL": "荷兰", "BE": "比利时", "CH": "瑞士", "AT": "奥地利",
    "IT": "意大利", "ES": "西班牙", "PT": "葡萄牙", "SE": "瑞典", "NO": "挪威", "DK": "丹麦", "FI": "芬兰", "PL": "波兰",
    "CZ": "捷克", "HU": "匈牙利", "RO": "罗马尼亚", "GR": "希腊", "TR": "土耳其", "RU": "俄罗斯", "UA": "乌克兰",
    "AE": "阿联酋", "SA": "沙特", "IL": "以色列", "IN": "印度", "PK": "巴基斯坦", "TH": "泰国", "VN": "越南", "ID": "印尼",
    "MY": "马来西亚", "SG": "新加坡", "PH": "菲律宾", "HK": "中国香港", "MO": "中国澳门", "TW": "中国台湾", "JP": "日本",
    "KR": "韩国", "AU": "澳大利亚", "NZ": "新西兰", "ZA": "南非", "EG": "埃及", "NG": "尼日利亚", "CN": "中国大陆",
}

# 华人常见、用中文界面很自然的地区：保留中文为第一语言即可。
CHINESE_FRIENDLY = {"SG", "MY", "HK", "MO", "TW"}
# 这些地区有自己的英语区域标签；其他地区统一用 en-US。
ENGLISH_TAGS = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "NZ": "en-NZ", "IE": "en-IE", "SG": "en-SG",
    "MY": "en-MY", "HK": "en-HK", "IN": "en-IN", "PH": "en-PH", "ZA": "en-ZA",
}

# IANA → Windows 时区 ID（CLDR windowsZones 的常用子集）。不在表里时请用 --windows-timezone 显式指定。
IANA_TO_WINDOWS = {
    "America/Los_Angeles": "Pacific Standard Time", "America/Vancouver": "Pacific Standard Time",
    "America/Tijuana": "Pacific Standard Time (Mexico)", "America/Denver": "Mountain Standard Time",
    "America/Boise": "Mountain Standard Time", "America/Edmonton": "Mountain Standard Time",
    "America/Phoenix": "US Mountain Standard Time", "America/Chicago": "Central Standard Time",
    "America/Winnipeg": "Central Standard Time", "America/Mexico_City": "Central Standard Time (Mexico)",
    "America/New_York": "Eastern Standard Time", "America/Toronto": "Eastern Standard Time",
    "America/Detroit": "Eastern Standard Time", "America/Indiana/Indianapolis": "US Eastern Standard Time",
    "America/Halifax": "Atlantic Standard Time", "America/Anchorage": "Alaskan Standard Time",
    "Pacific/Honolulu": "Hawaiian Standard Time", "America/Sao_Paulo": "E. South America Standard Time",
    "America/Argentina/Buenos_Aires": "Argentina Standard Time", "America/Buenos_Aires": "Argentina Standard Time",
    "America/Bogota": "SA Pacific Standard Time", "America/Lima": "SA Pacific Standard Time",
    "America/Santiago": "Pacific SA Standard Time",
    "Europe/London": "GMT Standard Time", "Europe/Dublin": "GMT Standard Time", "Europe/Lisbon": "GMT Standard Time",
    "Europe/Berlin": "W. Europe Standard Time", "Europe/Amsterdam": "W. Europe Standard Time",
    "Europe/Zurich": "W. Europe Standard Time", "Europe/Rome": "W. Europe Standard Time",
    "Europe/Stockholm": "W. Europe Standard Time", "Europe/Vienna": "W. Europe Standard Time",
    "Europe/Oslo": "W. Europe Standard Time", "Europe/Luxembourg": "W. Europe Standard Time",
    "Europe/Paris": "Romance Standard Time", "Europe/Brussels": "Romance Standard Time",
    "Europe/Madrid": "Romance Standard Time", "Europe/Copenhagen": "Romance Standard Time",
    "Europe/Warsaw": "Central European Standard Time", "Europe/Prague": "Central Europe Standard Time",
    "Europe/Budapest": "Central Europe Standard Time", "Europe/Helsinki": "FLE Standard Time",
    "Europe/Kiev": "FLE Standard Time", "Europe/Kyiv": "FLE Standard Time", "Europe/Athens": "GTB Standard Time",
    "Europe/Bucharest": "GTB Standard Time", "Europe/Istanbul": "Turkey Standard Time",
    "Europe/Moscow": "Russian Standard Time",
    "Asia/Dubai": "Arabian Standard Time", "Asia/Riyadh": "Arab Standard Time", "Asia/Jerusalem": "Israel Standard Time",
    "Asia/Karachi": "Pakistan Standard Time", "Asia/Kolkata": "India Standard Time", "Asia/Calcutta": "India Standard Time",
    "Asia/Bangkok": "SE Asia Standard Time", "Asia/Ho_Chi_Minh": "SE Asia Standard Time", "Asia/Saigon": "SE Asia Standard Time",
    "Asia/Jakarta": "SE Asia Standard Time", "Asia/Singapore": "Singapore Standard Time",
    "Asia/Kuala_Lumpur": "Singapore Standard Time", "Asia/Manila": "Singapore Standard Time",
    "Asia/Hong_Kong": "China Standard Time", "Asia/Macau": "China Standard Time", "Asia/Shanghai": "China Standard Time",
    "Asia/Taipei": "Taipei Standard Time", "Asia/Tokyo": "Tokyo Standard Time", "Asia/Seoul": "Korea Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time", "Australia/Melbourne": "AUS Eastern Standard Time",
    "Australia/Brisbane": "E. Australia Standard Time", "Australia/Perth": "W. Australia Standard Time",
    "Australia/Adelaide": "Cen. Australia Standard Time", "Pacific/Auckland": "New Zealand Standard Time",
    "Africa/Johannesburg": "South Africa Standard Time", "Africa/Cairo": "Egypt Standard Time",
    "Africa/Lagos": "W. Central Africa Standard Time", "Etc/UTC": "UTC", "UTC": "UTC",
}
WINDOWS_TO_IANA = {}
for _iana, _win in IANA_TO_WINDOWS.items():
    WINDOWS_TO_IANA.setdefault(_win, _iana)
WINDOWS_TO_IANA["China Standard Time"] = "Asia/Shanghai"

CN_TIMEZONES = {"Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "Asia/Urumqi", "PRC", "Asia/Hong_Kong", "Asia/Macau"}

TAG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
IANA_RE = re.compile(r"^[A-Za-z]+(/[A-Za-z0-9_+\-]+){1,2}$|^UTC$")
WINTZ_RE = re.compile(r"^[A-Za-z0-9 ().+\-]{2,64}$")


# --------------------------------------------------------------------------- 基础工具

@dataclasses.dataclass
class Env:
    platform: str  # "windows" / "darwin"
    home: pathlib.Path
    environ: dict

    def win_dir(self, var: str) -> pathlib.Path | None:
        value = self.environ.get(var)
        if value:
            return pathlib.Path(value)
        if var == "LOCALAPPDATA":
            return self.home / "AppData" / "Local"
        if var == "APPDATA":
            return self.home / "AppData" / "Roaming"
        return None

    @property
    def mac_support(self) -> pathlib.Path:
        return self.home / "Library" / "Application Support"


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def build_env(environ: dict | None = None, platform: str | None = None, home: pathlib.Path | None = None) -> Env:
    environ = dict(os.environ if environ is None else environ)
    return Env(platform=platform or current_platform(), home=pathlib.Path(home or pathlib.Path.home()), environ=environ)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json_atomic(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != "nt":
        os.chmod(temp, 0o600)
    os.replace(temp, path)


def dig(data, *keys):
    for item in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(item)
    return data


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def base_tag(tag: str) -> str:
    """zh-Hans-CN → zh-CN；en_US → en-US。用于比较语言标签。"""
    parts = tag.replace("_", "-").split("-")
    parts = [p for i, p in enumerate(parts) if not (i > 0 and len(p) == 4 and p.isalpha())]
    if not parts:
        return ""
    out = [parts[0].lower()] + [p.upper() if len(p) == 2 else p for p in parts[1:]]
    return "-".join(out)


def same_tag(a: str, b: str) -> bool:
    return base_tag(a).lower() == base_tag(b).lower()


def tag_lang(tag: str) -> str:
    return base_tag(tag).split("-")[0]


def tag_region(tag: str) -> str:
    parts = base_tag(tag).split("-")
    return parts[1] if len(parts) > 1 and len(parts[1]) == 2 else ""


def chromium_time(value) -> str | None:
    """Chromium 的时间是 1601-01-01 起的微秒数。"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return (dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=value)).strftime("%Y-%m-%d")
    except OverflowError:
        return None


def unix_us_time(value) -> str | None:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(value / 1_000_000).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- 系统命令（测试可替换）

class System:
    """所有外部命令都经由这里；测试注入替身。"""

    def __init__(self, env: Env):
        self.env = env

    def run(self, args: list[str], timeout: int = 30, env: dict | None = None) -> tuple[int, str, str]:
        try:
            done = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            return done.returncode, done.stdout, done.stderr
        except FileNotFoundError:
            return 127, "", "命令不存在：" + str(args[0])
        except subprocess.TimeoutExpired:
            return 124, "", "超时"
        except OSError as error:
            return 126, "", str(error)

    def powershell(self, script: str, timeout: int = 60) -> tuple[int, str, str]:
        prefix = (
            "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$OutputEncoding=[Text.Encoding]::UTF8;"
        )
        exe = os.path.join(self.env.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.exists(exe):
            exe = "powershell.exe"
        return self.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + script], timeout=timeout)

    def reg_get(self, root: str, path: str, name: str):
        if self.env.platform != "windows":
            return None
        try:
            import winreg  # noqa: PLC0415

            hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCR": winreg.HKEY_CLASSES_ROOT}[root]
            with winreg.OpenKey(hive, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except (OSError, KeyError, ImportError):
            return None

    def reg_subkeys(self, root: str, path: str, wow64_32: bool = False) -> list[tuple[str, dict]]:
        if self.env.platform != "windows":
            return []
        try:
            import winreg  # noqa: PLC0415

            hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[root]
            access = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if wow64_32 else 0)
            out = []
            with winreg.OpenKey(hive, path, 0, access) as key:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    values = {}
                    try:
                        with winreg.OpenKey(key, sub) as child:
                            for name in ("DisplayName", "InstallLocation", "DisplayIcon"):
                                try:
                                    values[name] = str(winreg.QueryValueEx(child, name)[0])
                                except OSError:
                                    pass
                    except OSError:
                        continue
                    out.append((sub, values))
            return out
        except (OSError, KeyError, ImportError):
            return []

    def curl(self, url: str, proxy: str | None = None, timeout: int = 10) -> tuple[int, str, str]:
        exe = shutil.which("curl") or "curl"
        if self.env.platform == "windows":
            system_curl = os.path.join(self.env.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
            if os.path.exists(system_curl):
                exe = system_curl
        elif os.path.exists("/usr/bin/curl"):
            exe = "/usr/bin/curl"
        env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        cmd = [exe, "-sS", "--max-time", str(timeout), "-H", "Accept: application/json", "-A", "curl/8"]
        cmd += ["-x", proxy] if proxy else ["--noproxy", "*"]
        return self.run(cmd + [url], timeout=timeout + 5, env=env)


# --------------------------------------------------------------------------- 默认浏览器

def classify_progid(progid: str | None, command: str | None = None) -> str:
    if progid:
        for prefix, browser in WIN_PROGID_PREFIXES:
            if progid.lower().startswith(prefix.lower()):
                return browser
    lowered = (command or "").lower()
    for exe, browser in WIN_EXE_HINTS:
        if exe in lowered:
            return browser
    return "unknown"


def mac_default_from_plist(data: dict | None) -> str | None:
    """从 com.apple.launchservices.secure.plist 里取 https 的处理程序 bundle id。"""
    handlers = (data or {}).get("LSHandlers") or []
    for scheme in ("https", "http"):
        for item in handlers:
            if isinstance(item, dict) and str(item.get("LSHandlerURLScheme", "")).lower() == scheme:
                bundle = item.get("LSHandlerRoleAll") or item.get("LSHandlerRoleViewer")
                if bundle:
                    return str(bundle)
    return None


def default_browser(env: Env, system: System) -> dict:
    if env.platform == "windows":
        base = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{}\UserChoice"
        progid = system.reg_get("HKCU", base.format("https"), "ProgId") or system.reg_get("HKCU", base.format("http"), "ProgId")
        command = system.reg_get("HKCR", f"{progid}\\shell\\open\\command", "") if progid else None
        browser = classify_progid(progid, command)
        out = {"id": browser, "name": BROWSER_NAMES.get(browser, "未知"), "raw": progid, "command": command}
        if browser == "ie":
            out["note"] = "默认浏览器被设成了 IE（Windows 10/11 上通常会转交给 Edge，也可能被安全软件改过），建议在系统设置里明确选一个默认浏览器"
        elif browser == "unknown":
            out["note"] = "无法识别，打开方式：" + str(command or "未知")
        return out
    plist_path = env.home / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    data = None
    try:
        with open(plist_path, "rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        data = None
    bundle = mac_default_from_plist(data)
    if not bundle:
        return {"id": "safari", "name": "Safari", "raw": None, "command": None, "note": "未找到自定义 https 处理程序，按系统默认 Safari 计"}
    browser = MAC_BUNDLE_IDS.get(bundle.lower(), "unknown")
    return {"id": browser, "name": BROWSER_NAMES.get(browser, bundle), "raw": bundle, "command": None}


# --------------------------------------------------------------------------- SQLite（只计数）

def sqlite_rows(db: pathlib.Path, sql: str, params: tuple = ()) -> tuple[list | None, str]:
    """复制到临时目录后只读查询；浏览器运行时文件可能被锁，返回 (None, 原因)。"""
    if not db.is_file():
        return None, "没有 " + db.name + " 数据库"
    temp_dir = tempfile.mkdtemp(prefix="cbe-")
    try:
        target = pathlib.Path(temp_dir) / db.name
        try:
            shutil.copy2(db, target)
            for suffix in ("-wal", "-journal"):
                side = db.with_name(db.name + suffix)
                if side.is_file():
                    shutil.copy2(side, target.with_name(target.name + suffix))
        except OSError:
            return None, "被占用（浏览器正在运行，关闭后再审计可读到）"
        try:
            connection = sqlite3.connect(str(target))
            try:
                return connection.execute(sql, params).fetchall(), "ok"
            finally:
                connection.close()
        except sqlite3.Error as error:
            return None, "无法读取：" + str(error)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def host_filter(column: str) -> tuple[str, tuple]:
    clauses, params = [], []
    for host in CLAUDE_HOSTS:
        clauses.append(f"({column} = ? OR {column} = ? OR {column} LIKE ?)")
        params += [host, "." + host, "%." + host]
    return " OR ".join(clauses), tuple(params)


def url_filter(column: str) -> tuple[str, tuple]:
    clauses, params = [], []
    for host in CLAUDE_HOSTS:
        clauses.append(f"({column} LIKE ? OR {column} LIKE ?)")
        params += [f"https://{host}/%", f"https://%.{host}/%"]
    return " OR ".join(clauses), tuple(params)


def claude_traces_chromium(profile: pathlib.Path) -> dict:
    where, params = host_filter("host_key")
    cookie_db = profile / "Network" / "Cookies"
    if not cookie_db.is_file():
        cookie_db = profile / "Cookies"
    rows, cookie_status = sqlite_rows(cookie_db, f"SELECT host_key, COUNT(*) FROM cookies WHERE {where} GROUP BY host_key", params)
    where_u, params_u = url_filter("url")
    hist, hist_status = sqlite_rows(profile / "History", f"SELECT COUNT(*), MAX(last_visit_time) FROM urls WHERE {where_u}", params_u)
    return summarize_traces(rows, cookie_status, hist, hist_status, chromium_time)


def claude_traces_firefox(profile: pathlib.Path) -> dict:
    where, params = host_filter("host")
    rows, cookie_status = sqlite_rows(profile / "cookies.sqlite", f"SELECT host, COUNT(*) FROM moz_cookies WHERE {where} GROUP BY host", params)
    where_u, params_u = url_filter("url")
    hist, hist_status = sqlite_rows(profile / "places.sqlite", f"SELECT COUNT(*), MAX(last_visit_date) FROM moz_places WHERE {where_u}", params_u)
    return summarize_traces(rows, cookie_status, hist, hist_status, unix_us_time)


def summarize_traces(rows, cookie_status, hist, hist_status, time_fn) -> dict:
    cookies = None
    if rows is not None:
        cookies = {}
        for host, count in rows:
            name = str(host).lstrip(".")
            cookies[name] = cookies.get(name, 0) + int(count)
    visits, last = None, None
    if hist is not None and hist:
        visits = int(hist[0][0] or 0)
        last = time_fn(hist[0][1])
    used = None
    if cookies is not None or visits is not None:
        used = bool(cookies) or bool(visits)
    return {
        "cookies": cookies, "cookie_status": cookie_status, "visits": visits, "last_visit": last,
        "history_status": hist_status, "used_claude": used,
    }


# --------------------------------------------------------------------------- Chromium 系

def chromium_user_data(env: Env, row) -> pathlib.Path | None:
    _, _, win, mac, _, _ = row
    if env.platform == "windows":
        base = env.win_dir(win[0])
        return base / win[1] if base else None
    if env.platform == "darwin" and mac:
        return env.mac_support / mac
    return None


def chromium_profile_dirs(user_data: pathlib.Path) -> list[tuple[str, str]]:
    """返回 [(目录名, 显示名)]；Opera 这类单资料浏览器的目录名为 "."。"""
    local_state = read_json(user_data / "Local State", {}) or {}
    cache = dig(local_state, "profile", "info_cache") or {}
    names = {}
    if isinstance(cache, dict):
        for directory, info in cache.items():
            if (user_data / directory / "Preferences").is_file():
                names[directory] = str((info or {}).get("name") or directory)
    try:
        for child in user_data.iterdir():
            if child.is_dir() and (child.name == "Default" or child.name.startswith("Profile ")) and (child / "Preferences").is_file():
                names.setdefault(child.name, child.name)
    except OSError:
        pass
    if not names and (user_data / "Preferences").is_file():
        names["."] = "默认"
    order = sorted(names, key=lambda d: (d != "Default", d))
    return [(d, names[d]) for d in order]


def extension_enabled(entry) -> bool | None:
    if not isinstance(entry, dict):
        return None
    if "state" in entry:
        return entry.get("state") == 1
    reasons = entry.get("disable_reasons")
    if reasons:
        return False
    return True


def find_key(data, wanted: str):
    if isinstance(data, dict):
        for key, value in data.items():
            if key == wanted:
                return value
            found = find_key(value, wanted)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_key(item, wanted)
            if found is not None:
                return found
    return None


def chromium_profile_report(profile: pathlib.Path, read_traces: bool = True) -> dict:
    prefs = read_json(profile / "Preferences", {}) or {}
    secure = read_json(profile / "Secure Preferences", {}) or {}
    languages = dig(prefs, "intl", "selected_languages") or dig(prefs, "intl", "accept_languages") or ""
    lang_list = [item.strip() for item in str(languages).split(",") if item.strip()]
    settings = {}
    for source in (dig(secure, "extensions", "settings"), dig(prefs, "extensions", "settings")):
        if isinstance(source, dict):
            for ext_id, entry in source.items():
                settings.setdefault(ext_id, entry)
    extensions = []
    for ext_id, name in WEBRTC_EXTENSIONS.items():
        entry = settings.get(ext_id)
        on_disk = (profile / "Extensions" / ext_id).is_dir()
        if entry is None and not on_disk:
            continue
        enabled = extension_enabled(entry)
        extensions.append({"id": ext_id, "name": name, "enabled": True if enabled is None and on_disk else enabled})
    controlled = None
    for ext_id, entry in settings.items():
        value = find_key(entry, "webrtc.ip_handling_policy")
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and extension_enabled(entry) is not False:
            controlled = {"extension": ext_id, "policy": value}
            break
    report = {
        "languages": lang_list,
        "webrtc_pref": dig(prefs, "webrtc", "ip_handling_policy"),
        "webrtc_extensions": extensions,
        "webrtc_extension_policy": controlled,
    }
    if read_traces:
        report["claude"] = claude_traces_chromium(profile)
    return report


def chromium_policy(env: Env, system: System, row) -> dict:
    _, _, _, _, reg_key, mac_domain = row
    out = {}
    if env.platform == "windows" and reg_key:
        for hive in ("HKLM", "HKCU"):
            for name in ("WebRtcIPHandling", "DnsOverHttpsMode"):
                value = system.reg_get(hive, reg_key, name)
                if value is not None and name not in out:
                    out[name] = str(value)
    elif env.platform == "darwin" and mac_domain:
        for path in (pathlib.Path("/Library/Managed Preferences") / (mac_domain + ".plist"),
                     pathlib.Path("/Library/Managed Preferences") / env.home.name / (mac_domain + ".plist")):
            try:
                with open(path, "rb") as handle:
                    data = plistlib.load(handle)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            for name in ("WebRtcIPHandling", "DnsOverHttpsMode"):
                if name in data and name not in out:
                    out[name] = str(data[name])
    return out


def webrtc_verdict_chromium(policy: dict, profile: dict) -> tuple[str, str]:
    managed = policy.get("WebRtcIPHandling")
    if managed in WEBRTC_SAFE_POLICIES:
        return "protected", "托管策略 WebRtcIPHandling=" + managed
    ext_policy = profile.get("webrtc_extension_policy") or {}
    if ext_policy.get("policy") in WEBRTC_SAFE_POLICIES:
        return "protected", "扩展已把 WebRTC 设为 " + ext_policy["policy"]
    enabled = [item["name"] for item in profile.get("webrtc_extensions", []) if item.get("enabled")]
    if enabled:
        return "protected", "已装并启用：" + "、".join(enabled) + "（以 browserleaks 实测为准）"
    if profile.get("webrtc_pref") in WEBRTC_SAFE_POLICIES:
        return "protected", "浏览器偏好 webrtc.ip_handling_policy=" + profile["webrtc_pref"]
    if managed in WEBRTC_PARTIAL_POLICIES or ext_policy.get("policy") in WEBRTC_PARTIAL_POLICIES:
        return "partial", "只隐藏了部分候选地址（" + str(managed or ext_policy.get("policy")) + "），建议改成 disable_non_proxied_udp"
    disabled = [item["name"] for item in profile.get("webrtc_extensions", []) if item.get("enabled") is False]
    if disabled:
        return "missing", "已安装但被停用：" + "、".join(disabled)
    return "missing", "未发现 WebRTC 防护"


# --------------------------------------------------------------------------- Firefox

PREF_RE = re.compile(r'user_pref\(\s*"([^"]+)"\s*,\s*(.+?)\s*\)\s*;')
FIREFOX_KEYS = (
    "intl.accept_languages", "intl.locale.requested", "media.peerconnection.enabled",
    "media.peerconnection.ice.default_address_only", "media.peerconnection.ice.no_host",
    "media.peerconnection.ice.proxy_only", "privacy.resistFingerprinting",
)


def parse_prefs_js(text: str) -> dict:
    out = {}
    for name, raw in PREF_RE.findall(text or ""):
        raw = raw.strip()
        if raw in ("true", "false"):
            value = raw == "true"
        elif raw.startswith('"') and raw.endswith('"'):
            value = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            try:
                value = int(raw)
            except ValueError:
                value = raw
        out[name] = value
    return out


def firefox_root(env: Env) -> pathlib.Path | None:
    if env.platform == "windows":
        base = env.win_dir("APPDATA")
        return base / "Mozilla" / "Firefox" if base else None
    if env.platform == "darwin":
        return env.mac_support / "Firefox"
    return None


def firefox_profiles(root: pathlib.Path) -> list[tuple[str, pathlib.Path, bool]]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(root / "profiles.ini", encoding="utf-8")
    except (OSError, configparser.Error):
        return []
    out = []
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        path = parser.get(section, "Path", fallback="")
        if not path:
            continue
        relative = parser.get(section, "IsRelative", fallback="1") == "1"
        full = (root / path) if relative else pathlib.Path(path)
        out.append((parser.get(section, "Name", fallback=path), full, parser.get(section, "Default", fallback="0") == "1"))
    return out


def firefox_policies(env: Env) -> dict:
    if env.platform == "windows":
        candidates = [pathlib.Path(env.environ.get(var, default)) / "Mozilla Firefox" / "distribution" / "policies.json"
                      for var, default in (("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
    else:
        candidates = [pathlib.Path("/Applications/Firefox.app/Contents/Resources/distribution/policies.json")]
    for path in candidates:
        data = read_json(path)
        prefs = dig(data, "policies", "Preferences")
        if isinstance(prefs, dict):
            return {key: (value.get("Value") if isinstance(value, dict) else value) for key, value in prefs.items()}
    return {}


def webrtc_verdict_firefox(prefs: dict) -> tuple[str, str]:
    if prefs.get("media.peerconnection.enabled") is False:
        return "protected", "WebRTC 已整体关闭（media.peerconnection.enabled=false）"
    if prefs.get("media.peerconnection.ice.proxy_only") is True:
        return "protected", "只走代理（media.peerconnection.ice.proxy_only=true）"
    if prefs.get("media.peerconnection.ice.default_address_only") is True and prefs.get("media.peerconnection.ice.no_host") is True:
        return "protected", "default_address_only 与 no_host 均已开启（以 browserleaks 实测为准）"
    if prefs.get("media.peerconnection.ice.default_address_only") is True or prefs.get("media.peerconnection.ice.no_host") is True:
        return "partial", "只开了 default_address_only / no_host 其中一项"
    return "missing", "未发现 WebRTC 防护"


# --------------------------------------------------------------------------- 浏览器汇总

def scan_browsers(env: Env, system: System, read_traces: bool = True) -> list[dict]:
    out = []
    for row in CHROMIUM_BROWSERS:
        user_data = chromium_user_data(env, row)
        if not user_data or not user_data.is_dir():
            continue
        policy = chromium_policy(env, system, row)
        local_state = read_json(user_data / "Local State", {}) or {}
        profiles = []
        for directory, name in chromium_profile_dirs(user_data):
            path = user_data if directory == "." else user_data / directory
            report = chromium_profile_report(path, read_traces=read_traces)
            status, detail = webrtc_verdict_chromium(policy, report)
            report.update({"dir": directory, "name": name, "webrtc": status, "webrtc_detail": detail})
            profiles.append(report)
        out.append({
            "id": row[0], "name": row[1], "kind": "chromium", "user_data": str(user_data),
            "ui_locale": dig(local_state, "intl", "app_locale"), "policy": policy, "profiles": profiles,
        })
    root = firefox_root(env)
    if root and (root / "profiles.ini").is_file():
        policy = firefox_policies(env)
        profiles = []
        for name, path, is_default in firefox_profiles(root):
            if not path.is_dir():
                continue
            prefs = {}
            for filename in ("prefs.js", "user.js"):
                try:
                    prefs.update(parse_prefs_js((path / filename).read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
            prefs.update(policy)
            status, detail = webrtc_verdict_firefox(prefs)
            languages = [item.strip() for item in str(prefs.get("intl.accept_languages") or "").split(",") if item.strip()]
            report = {
                "dir": path.name, "name": name, "default": is_default, "languages": languages,
                "ui_locale": prefs.get("intl.locale.requested"), "resist_fingerprinting": prefs.get("privacy.resistFingerprinting") is True,
                "webrtc": status, "webrtc_detail": detail,
            }
            if read_traces:
                report["claude"] = claude_traces_firefox(path)
            profiles.append(report)
        out.append({"id": "firefox", "name": "Firefox", "kind": "firefox", "user_data": str(root), "policy": policy, "profiles": profiles})
    if env.platform == "darwin" and (pathlib.Path("/Applications/Safari.app").exists()):
        out.append({
            "id": "safari", "name": "Safari", "kind": "safari", "user_data": None, "policy": {},
            "profiles": [{"dir": "-", "name": "Safari", "languages": [], "webrtc": "partial",
                          "webrtc_detail": "Safari 默认用 mDNS 隐藏内网地址，公网候选地址仍可能暴露；没有扩展可装时以 browserleaks 实测为准",
                          "claude": {"used_claude": None, "cookie_status": "Safari 数据受系统保护，脚本不读取"}}],
        })
    return out


def scan_fingerprint_browsers(env: Env, system: System) -> list[dict]:
    hits: dict[str, dict] = {}

    def hit(fid: str, name: str, evidence: str):
        entry = hits.setdefault(fid, {"id": fid, "name": name, "evidence": []})
        if evidence not in entry["evidence"] and len(entry["evidence"]) < 4:
            entry["evidence"].append(evidence)

    def match(text: str):
        lowered = text.lower()
        for fid, name, keywords, _, _ in FINGERPRINT_BROWSERS:
            if any(keyword in lowered for keyword in keywords):
                return fid, name
        return None

    if env.platform == "windows":
        uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        for hive, wow in (("HKCU", False), ("HKLM", False), ("HKLM", True)):
            for _, values in system.reg_subkeys(hive, uninstall, wow64_32=wow):
                display = values.get("DisplayName", "")
                found = match(display) if display else None
                if found:
                    hit(found[0], found[1], "已安装：" + display)
        start_menus = [env.win_dir("APPDATA"), pathlib.Path(env.environ.get("ProgramData", r"C:\ProgramData"))]
        for base in start_menus:
            if not base:
                continue
            folder = base / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for path in _walk_names(folder, depth=2):
                found = match(path.stem)
                if found:
                    hit(found[0], found[1], "开始菜单：" + path.stem)
        for fid, name, _, _, data_dirs in FINGERPRINT_BROWSERS:
            for var in ("APPDATA", "LOCALAPPDATA"):
                base = env.win_dir(var)
                for data in data_dirs:
                    if base and (base / data).is_dir():
                        hit(fid, name, "数据目录：" + str(base / data))
    elif env.platform == "darwin":
        for fid, name, _, apps, data_dirs in FINGERPRINT_BROWSERS:
            for folder in (pathlib.Path("/Applications"), env.home / "Applications"):
                for app in apps:
                    if (folder / app).exists():
                        hit(fid, name, "应用：" + str(folder / app))
            for data in data_dirs:
                if (env.mac_support / data).is_dir():
                    hit(fid, name, "数据目录：" + str(env.mac_support / data))
    return list(hits.values())


def _walk_names(folder: pathlib.Path, depth: int):
    try:
        for child in folder.iterdir():
            if child.is_dir() and depth > 0:
                yield from _walk_names(child, depth - 1)
            elif child.suffix.lower() in (".lnk", ".url"):
                yield child
    except OSError:
        return


# --------------------------------------------------------------------------- 系统时区与语言

WIN_STATE_SCRIPT = r"""
$tz = Get-TimeZone
$langs = @(Get-WinUserLanguageList | ForEach-Object { [pscustomobject]@{ tag = [string]$_.LanguageTag; tips = @($_.InputMethodTips | ForEach-Object { [string]$_ }) } })
$geo = $null; try { $geo = Get-WinHomeLocation } catch {}
$ui = ''; try { $o = Get-ItemProperty -LiteralPath 'HKCU:\Control Panel\Desktop' -Name PreferredUILanguages -ErrorAction Stop; $ui = [string](@($o.PreferredUILanguages)[0]) } catch {}
$sys = ''; try { $sys = [string](Get-WinSystemLocale).Name } catch {}
ConvertTo-Json -Depth 6 -Compress -InputObject ([pscustomobject]@{
  timezone = [string]$tz.Id; timezone_display = [string]$tz.DisplayName
  culture = [string](Get-Culture).Name; ui_culture = [string](Get-UICulture).Name; ui_override = $ui
  languages = $langs; geo_id = $(if ($geo) { [int]$geo.GeoId } else { 0 }); geo_name = $(if ($geo) { [string]$geo.HomeLocation } else { '' })
  system_locale = $sys
})
"""


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def system_state(env: Env, system: System) -> dict:
    if env.platform == "windows":
        code, out, err = system.powershell(WIN_STATE_SCRIPT)
        state = {"platform": "windows", "error": None}
        try:
            data = json.loads(out.strip().splitlines()[-1]) if code == 0 and out.strip() else None
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            state["error"] = "读取时区与语言失败：" + (err.strip() or out.strip() or f"退出码 {code}")[:300]
            return state
        languages = [{"tag": str(item.get("tag", "")), "tips": [str(t) for t in _as_list(item.get("tips"))]}
                     for item in _as_list(data.get("languages")) if isinstance(item, dict) and item.get("tag")]
        state.update({
            "timezone": data.get("timezone"), "timezone_display": data.get("timezone_display"),
            "timezone_iana": WINDOWS_TO_IANA.get(str(data.get("timezone"))),
            "culture": data.get("culture"), "ui_culture": data.get("ui_culture"), "ui_override": data.get("ui_override") or "",
            "languages": [item["tag"] for item in languages], "language_entries": languages,
            "geo_id": int(data.get("geo_id") or 0), "geo_name": data.get("geo_name"), "system_locale": data.get("system_locale"),
        })
        start = system.reg_get("HKLM", r"SYSTEM\CurrentControlSet\Services\tzautoupdate", "Start")
        state["auto_timezone"] = None if start is None else int(start) != 4
        return state
    state = {"platform": "darwin", "error": None}
    try:
        link = os.readlink("/etc/localtime")
        state["timezone"] = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else link
    except OSError:
        state["timezone"] = None
    state["timezone_iana"] = state["timezone"]
    code, out, _ = system.run(["/usr/bin/defaults", "export", "NSGlobalDomain", "-"])
    data = {}
    if code == 0:
        try:
            data = plistlib.loads(out.encode("utf-8"))
        except (plistlib.InvalidFileException, ValueError):
            data = {}
    state["languages"] = [str(x) for x in (data.get("AppleLanguages") or [])]
    state["languages_existed"] = "AppleLanguages" in data
    state["apple_locale"] = data.get("AppleLocale")
    state["apple_locale_existed"] = "AppleLocale" in data
    try:
        with open("/Library/Preferences/com.apple.timezone.auto.plist", "rb") as handle:
            state["auto_timezone"] = bool(plistlib.load(handle).get("Active"))
    except (OSError, plistlib.InvalidFileException, ValueError):
        state["auto_timezone"] = None
    return state


# --------------------------------------------------------------------------- 出口探测

def parse_ipinfo(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    country = data.get("country") or data.get("country_code") or data.get("countryCode")
    ip = data.get("ip") or data.get("query")
    if not ip or not country:
        return None
    return {
        "ip": str(ip), "country": str(country).upper(), "region": data.get("region") or data.get("regionName"),
        "city": data.get("city"), "timezone": data.get("timezone"), "org": data.get("org") or data.get("isp"),
    }


PROBE_URLS = ("https://ipinfo.io/json", "https://ipapi.co/json/")


def probe_exit(system: System, proxy: str | None = None) -> dict:
    errors = []
    for url in PROBE_URLS:
        code, out, err = system.curl(url, proxy=proxy)
        if code == 0:
            info = parse_ipinfo(out)
            if info:
                info["source"] = url.split("/")[2]
                return info
            errors.append(url.split("/")[2] + " 返回内容无法解析")
        else:
            errors.append(url.split("/")[2] + "：" + (err.strip() or f"退出码 {code}")[:120])
    return {"error": "；".join(errors)}


def parse_scutil_proxy(text: str) -> str | None:
    values = dict(re.findall(r"^\s*(\w+)\s*:\s*(\S+)\s*$", text or "", re.M))
    for prefix in ("HTTPS", "HTTP", "SOCKS"):
        if values.get(prefix + "Enable") == "1" and values.get(prefix + "Proxy") and values.get(prefix + "Port"):
            scheme = "socks5h" if prefix == "SOCKS" else "http"
            return f"{scheme}://{values[prefix + 'Proxy']}:{values[prefix + 'Port']}"
    return None


def parse_windows_proxy(server: str | None) -> str | None:
    if not server:
        return None
    server = server.strip()
    if "=" in server:
        parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
        for key in ("https", "http"):
            if parts.get(key):
                return "http://" + parts[key].split("://")[-1]
        if parts.get("socks"):
            return "socks5h://" + parts["socks"].split("://")[-1]
        return None
    return server if "://" in server else "http://" + server


def system_proxy(env: Env, system: System) -> str | None:
    if env.platform == "windows":
        settings = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        if system.reg_get("HKCU", settings, "ProxyEnable") == 1:
            return parse_windows_proxy(system.reg_get("HKCU", settings, "ProxyServer"))
        return None
    code, out, _ = system.run(["/usr/sbin/scutil", "--proxy"])
    return parse_scutil_proxy(out) if code == 0 else None


def probe_exits(env: Env, system: System, proxy: str | None) -> dict:
    result = {"direct": probe_exit(system)}
    chosen = proxy or system_proxy(env, system)
    result["proxy_used"] = chosen
    if chosen:
        result["via_proxy"] = probe_exit(system, proxy=chosen)
    return result


# --------------------------------------------------------------------------- 推荐

def pick_exit(exits: dict) -> tuple[dict | None, list[str]]:
    """选用于对齐的出口：优先直连（TUN）——Claude Code、桌面端和浏览器都会走它；其次系统代理。"""
    notes = []
    direct = exits.get("direct") or {}
    via = exits.get("via_proxy") or {}
    d_ok = bool(direct.get("country")) and direct.get("country") != "CN"
    v_ok = bool(via.get("country")) and via.get("country") != "CN"
    if direct.get("country") == "CN":
        notes.append("直连出口在中国大陆：Clash 的 TUN（虚拟网卡）没有接管流量，命令行工具和不走系统代理的程序会直接用真实 IP。先回到第二步把 TUN 配好。")
    if d_ok and v_ok and (direct.get("country") != via.get("country") or (direct.get("timezone") and via.get("timezone") and direct["timezone"] != via["timezone"])):
        notes.append(f"两条路径出口不一致：直连 {fmt_exit(direct)}，系统代理 {fmt_exit(via)}。浏览器和 Claude Code 可能落在不同地区，建议在 Clash 里让它们走同一个节点。")
    if d_ok:
        return direct, notes
    if v_ok:
        notes.append("按系统代理的出口对齐；没走系统代理的程序仍可能暴露真实 IP。")
        return via, notes
    return None, notes


def fmt_exit(info: dict | None) -> str:
    if not info or not info.get("country"):
        return "未知"
    place = " / ".join(str(x) for x in (COUNTRY_NAMES.get(info["country"], info["country"]), info.get("region"), info.get("city")) if x)
    return f"{info.get('ip')}（{place}，时区 {info.get('timezone') or '未知'}）"


def english_tag(country: str) -> str:
    return ENGLISH_TAGS.get(country, "en-US")


def target_languages(current: list[str], country: str, english_first: bool) -> list[str]:
    """计算目标语言顺序。默认保留中文为第一语言，补上出口地区的英语；--english-first 时英语排第一。"""
    current = [tag for tag in current if tag]
    chinese = next((tag for tag in current if tag_lang(tag) == "zh"), None) or "zh-CN"
    wanted = english_tag(country)
    english = next((tag for tag in current if same_tag(tag, wanted)), None)
    if english is None and country not in ENGLISH_TAGS:
        english = next((tag for tag in current if tag_lang(tag) == "en"), None)
    english = english or wanted
    head = [english, chinese] if english_first else [chinese, english]
    rest = [tag for tag in current if not any(same_tag(tag, h) for h in head)]
    return head + rest


def browser_accept_languages(target: list[str]) -> list[str]:
    """浏览器里的语言顺序：每个带地区的标签后补上基础语言（zh-CN, zh, en-SG, en）。"""
    out = []
    for tag in target:
        tag = base_tag(tag)
        if tag not in out:
            out.append(tag)
        lang = tag_lang(tag)
        if lang not in out:
            out.append(lang)
    return out


def languages_match(browser: list[str], target: list[str]) -> bool:
    """浏览器语言的第一项要与系统第一语言一致，且出口地区的英语在列表中。"""
    if not browser:
        return False
    primary = base_tag(target[0])
    first = base_tag(browser[0])
    if not (same_tag(first, primary) or (tag_lang(first) == tag_lang(primary) and not tag_region(first))):
        return False
    return any(tag_lang(item) == "en" for item in browser) or tag_lang(primary) == "en"


def recommend(env: Env, state: dict, exits: dict, overrides: dict) -> dict:
    exit_info, notes = pick_exit(exits)
    country = (overrides.get("country") or (exit_info or {}).get("country") or "").upper() or None
    iana = overrides.get("timezone") or (exit_info or {}).get("timezone")
    if overrides.get("timezone") and not overrides.get("country") and exit_info:
        country = exit_info.get("country")
    rec = {"exit": exit_info, "country": country, "country_name": COUNTRY_NAMES.get(country or "", country), "notes": notes,
           "timezone_iana": iana, "timezone_windows": None, "ops": [], "browser_languages": [], "ready": False}
    if not country or not iana:
        rec["notes"].append("拿不到海外出口的国家和时区：先确认 Clash 已连上海外节点，或用 --country 与 --timezone 显式指定（例如 --country US --timezone America/Los_Angeles）。")
        return rec
    if not IANA_RE.match(iana):
        rec["notes"].append(f"时区名不合法：{iana}")
        return rec
    if iana in CN_TIMEZONES or country == "CN":
        rec["notes"].append("目标地区是中国（含港澳的东八区时区不变），不需要调整时区。")
    win_tz = overrides.get("windows_timezone") or IANA_TO_WINDOWS.get(iana)
    rec["timezone_windows"] = win_tz
    english_first = bool(overrides.get("english_first"))
    current_langs = state.get("languages") or []
    target = target_languages(current_langs, country, english_first)
    rec["target_languages"] = target
    rec["browser_languages"] = browser_accept_languages(target)
    rec["chinese_friendly"] = country in CHINESE_FRIENDLY
    if country in CHINESE_FRIENDLY:
        rec["language_advice"] = f"{rec['country_name']}华人多，系统和浏览器保留中文为第一语言没问题，补上 {english_tag(country)} 作为第二语言即可。"
    elif english_first:
        rec["language_advice"] = f"按 --english-first：{english_tag(country)} 排第一，中文排第二。"
    else:
        rec["language_advice"] = (f"系统显示语言可以继续用中文；把 {english_tag(country)} 加为第二语言。想更贴近 {rec['country_name']} 本地用户，"
                                  f"可加 --english-first 让英语排第一（Windows 会锁定当前显示语言，界面不会变成英文）。")

    if env.platform == "windows":
        if not win_tz:
            rec["notes"].append(f"时区 {iana} 不在内置对照表里，请加 --windows-timezone \"<Windows 时区 ID>\"（可用 tzutil /l 查看）。")
            return rec
        if not WINTZ_RE.match(win_tz):
            rec["notes"].append("Windows 时区 ID 不合法：" + win_tz)
            return rec
        if str(state.get("timezone") or "").lower() != win_tz.lower():
            rec["ops"].append({"kind": "timezone", "from": state.get("timezone"), "to": win_tz,
                               "title": f"系统时区：{state.get('timezone')} → {win_tz}（{iana}）"})
        geo = GEO_IDS.get(country)
        if geo and not overrides.get("skip_region") and int(state.get("geo_id") or 0) != geo:
            rec["ops"].append({"kind": "home_location", "from": state.get("geo_id"), "to": geo,
                               "title": f"国家或地区：{state.get('geo_name') or state.get('geo_id')} → {rec['country_name']}（GeoID {geo}）"})
        if not overrides.get("skip_languages") and [t.lower() for t in current_langs] != [t.lower() for t in target]:
            entries = state.get("language_entries") or [{"tag": t, "tips": []} for t in current_langs]
            rec["ops"].append({"kind": "languages", "from": entries, "to": target,
                               "title": f"语言列表：{', '.join(current_langs) or '（空）'} → {', '.join(target)}"})
            if english_first and not state.get("ui_override") and state.get("ui_culture") and tag_lang(state["ui_culture"]) != "en":
                rec["ops"].append({"kind": "ui_override", "from": "", "to": state["ui_culture"],
                                   "title": f"锁定 Windows 显示语言为 {state['ui_culture']}（英语排第一后界面不变）"})
    else:
        if str(state.get("timezone") or "") != iana:
            rec["ops"].append({"kind": "timezone", "from": state.get("timezone"), "to": iana,
                               "title": f"系统时区：{state.get('timezone')} → {iana}（需要输入开机密码）"})
        locale = state.get("apple_locale") or "zh_CN"
        lang_part = locale.split("_")[0] if "_" in locale else locale
        new_locale = f"{lang_part}_{country}"
        if not overrides.get("skip_region") and locale != new_locale:
            rec["ops"].append({"kind": "apple_locale", "from": state.get("apple_locale"), "existed": state.get("apple_locale_existed"),
                               "to": new_locale, "title": f"地区（AppleLocale）：{locale} → {new_locale}（日期与数字格式会随之变化）"})
        mac_target = [t.replace("_", "-") for t in target]
        if not overrides.get("skip_languages") and [t.lower() for t in current_langs] != [t.lower() for t in mac_target]:
            rec["ops"].append({"kind": "apple_languages", "from": current_langs, "existed": state.get("languages_existed"),
                               "to": mac_target, "title": f"首选语言：{', '.join(current_langs) or '（空）'} → {', '.join(mac_target)}"})
            if english_first:
                rec["notes"].append("macOS 的界面语言跟着首选语言第一项走：英语排第一后，重新登录系统界面会变成英文。")
    if state.get("auto_timezone"):
        rec["notes"].append("「自动设置时区」是开着的：系统会按定位把时区改回去，请先手动关掉（Windows：设置 → 时间和语言 → 日期和时间；macOS：系统设置 → 通用 → 日期与时间）。")
    rec["ready"] = True
    return rec


# --------------------------------------------------------------------------- 审计

def under_claude_code(environ: dict) -> bool:
    return environ.get("CLAUDECODE") == "1" or bool(environ.get("CLAUDE_CODE_ENTRYPOINT"))


def audit(env: Env, system: System, proxy: str | None = None, network: bool = True, overrides: dict | None = None) -> dict:
    state = system_state(env, system)
    exits = probe_exits(env, system, proxy) if network else {"direct": {"error": "已用 --no-network 跳过"}}
    browsers = scan_browsers(env, system)
    rec = recommend(env, state, exits, overrides or {})
    for browser in browsers:
        for profile in browser.get("profiles", []):
            if browser["kind"] in ("chromium", "firefox") and rec.get("target_languages"):
                profile["language_ok"] = languages_match(profile.get("languages") or [], rec["target_languages"])
    return {
        "version": VERSION, "platform": env.platform, "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "under_claude_code": under_claude_code(env.environ),
        "default_browser": default_browser(env, system),
        "browsers": browsers,
        "fingerprint_browsers": scan_fingerprint_browsers(env, system),
        "system": state, "exits": exits, "recommendation": rec,
        "webrtc_extension_url": WEBRTC_PROTECT_URL,
    }


def print_audit(report: dict) -> None:
    p = print
    p(f"== Claude 浏览器 / 时区 / 语言审计（{report['platform']}，{report['checked_at']}）==")
    d = report["default_browser"]
    p(f"\n[默认浏览器] {d['name']}" + (f"（{d['raw']}）" if d.get("raw") else "") + (f"  {d['note']}" if d.get("note") else ""))
    p("  Claude Code 登录时会用默认浏览器打开授权页；要登录在别的浏览器里，就把终端里打印的登录链接复制过去。")
    p("\n[浏览器与资料]")
    if not report["browsers"]:
        p("  没找到常见浏览器的用户数据目录。")
    for browser in report["browsers"]:
        extra = f"，界面语言 {browser['ui_locale']}" if browser.get("ui_locale") else ""
        pol = browser.get("policy") or {}
        extra += f"，托管策略 {pol}" if pol and browser["kind"] == "chromium" else ""
        p(f"  {browser['name']}{extra}")
        for prof in browser["profiles"]:
            claude = prof.get("claude") or {}
            if claude.get("used_claude") is True:
                used = f"有 Claude 痕迹（Cookie {sum((claude.get('cookies') or {}).values())} 条，访问 {claude.get('visits') or 0} 次，最近 {claude.get('last_visit') or '未知'}）"
            elif claude.get("used_claude") is False:
                used = "没有 Claude 痕迹"
            else:
                used = "Claude 痕迹未知：" + str(claude.get("cookie_status") or "")
            langs = ",".join(prof.get("languages") or []) or "（未单独设置，只用界面语言）"
            ok = prof.get("language_ok")
            lang_mark = "" if ok is None else ("  ✓" if ok else "  ✗ 与目标不一致")
            p(f"    - {prof['name']}（{prof['dir']}）：语言 {langs}{lang_mark}；WebRTC {prof['webrtc']}（{prof['webrtc_detail']}）；{used}")
    p("\n[指纹浏览器]")
    if report["fingerprint_browsers"]:
        for item in report["fingerprint_browsers"]:
            p(f"  {item['name']}：" + "；".join(item["evidence"]))
    else:
        p("  未检测到常见指纹浏览器（AdsPower、比特、Hubstudio、MoreLogin 等）。")
    s = report["system"]
    p("\n[系统]")
    if s.get("error"):
        p("  " + s["error"])
    else:
        p(f"  时区：{s.get('timezone')}" + (f"（{s.get('timezone_iana')}）" if s.get("timezone_iana") and s.get("timezone_iana") != s.get("timezone") else ""))
        if report["platform"] == "windows":
            p(f"  国家或地区：{s.get('geo_name')}（GeoID {s.get('geo_id')}）；区域格式 {s.get('culture')}；显示语言 {s.get('ui_culture')}"
              + (f"（已锁定 {s['ui_override']}）" if s.get("ui_override") else ""))
        else:
            p(f"  地区（AppleLocale）：{s.get('apple_locale')}")
        p(f"  语言列表：{', '.join(s.get('languages') or []) or '（空）'}")
        auto = s.get("auto_timezone")
        p(f"  自动设置时区：{'开' if auto else ('关' if auto is False else '未知')}")
    e = report["exits"]
    p("\n[出口]")
    p(f"  直连（TUN）：{fmt_exit(e.get('direct')) if not (e.get('direct') or {}).get('error') else e['direct']['error']}")
    if e.get("proxy_used"):
        via = e.get("via_proxy") or {}
        p(f"  系统代理 {e['proxy_used']}：{fmt_exit(via) if not via.get('error') else via['error']}")
    r = report["recommendation"]
    p("\n[建议]")
    if r.get("country"):
        p(f"  按 {r['country_name']} 对齐：时区 {r.get('timezone_iana')}" + (f"（Windows：{r['timezone_windows']}）" if r.get("timezone_windows") else ""))
    if r.get("language_advice"):
        p("  语言：" + r["language_advice"])
    if r.get("browser_languages"):
        p("  浏览器语言顺序建议：" + ", ".join(r["browser_languages"]))
    for op in r.get("ops", []):
        p("  待调整 · " + op["title"])
    if r.get("ready") and not r.get("ops"):
        p("  系统时区与语言已经和出口一致。")
    for note in r.get("notes", []):
        p("  注意 · " + note)
    p(f"\n  本地浏览器的 WebRTC 防泄漏扩展：{report['webrtc_extension_url']}")


# --------------------------------------------------------------------------- 执行与回滚

def validate_tag(tag: str) -> str:
    if not TAG_RE.match(tag or ""):
        raise ValueError("语言标签不合法：" + repr(tag))
    return tag


def ps_list(tags: list[str]) -> str:
    return "@(" + ",".join("'" + validate_tag(t) + "'" for t in tags) + ")"


def win_languages_script(target: list[str], previous: list[dict] | None = None) -> str:
    """重排语言列表：复用已有条目（保留输入法），缺的才新建；previous 不为空时按原样还原。"""
    if previous is None:
        return (
            "$cur = Get-WinUserLanguageList\n"
            "$new = New-WinUserLanguageList 'en-US'; $new.Clear()\n"
            f"foreach ($t in {ps_list(target)}) {{ $hit = $cur | Where-Object {{ $_.LanguageTag -eq $t }} | Select-Object -First 1; "
            "if ($hit) { $new.Add($hit) } else { $new.Add($t) } }\n"
            "Set-WinUserLanguageList -LanguageList $new -Force -WarningAction SilentlyContinue\n"
        )
    lines = ["$cur = Get-WinUserLanguageList", "$new = New-WinUserLanguageList 'en-US'; $new.Clear()"]
    for entry in previous:
        tag = validate_tag(entry["tag"])
        tips = [t for t in entry.get("tips", []) if re.match(r"^[0-9A-Fa-f]{4}:[{}0-9A-Fa-f\-]+$", t)]
        lines.append(
            f"$hit = $cur | Where-Object {{ $_.LanguageTag -eq '{tag}' }} | Select-Object -First 1; "
            f"if (-not $hit) {{ $hit = (New-WinUserLanguageList '{tag}')[0]; "
            + ("$hit.InputMethodTips.Clear(); " + " ".join(f"$hit.InputMethodTips.Add('{t}');" for t in tips) if tips else "")
            + " }; $new.Add($hit)"
        )
    lines.append("Set-WinUserLanguageList -LanguageList $new -Force -WarningAction SilentlyContinue")
    return "\n".join(lines) + "\n"


class OpError(RuntimeError):
    pass


def apply_op(env: Env, system: System, op: dict, undo: bool = False) -> None:
    kind = op["kind"]
    value = op["from"] if undo else op["to"]

    def check(result, what):
        code, out, err = result
        if code != 0:
            raise OpError(f"{what}失败：" + (err.strip() or out.strip() or f"退出码 {code}")[:400])
        return out

    if env.platform == "windows":
        if kind == "timezone":
            if not value or not WINTZ_RE.match(str(value)):
                raise OpError("时区 ID 不合法：" + repr(value))
            check(system.run(["tzutil.exe", "/s", str(value)]), "设置时区")
            got = check(system.run(["tzutil.exe", "/g"]), "读取时区").strip()
            if got.lower() != str(value).lower():
                raise OpError(f"时区校验不一致：期望 {value}，实际 {got}")
        elif kind == "home_location":
            geo = int(value)
            if not 1 <= geo <= 999:
                raise OpError("GeoID 超出范围")
            check(system.powershell(f"Set-WinHomeLocation -GeoId {geo}; [string](Get-WinHomeLocation).GeoId"), "设置国家或地区")
        elif kind == "languages":
            script = win_languages_script(op["to"]) if not undo else win_languages_script([], previous=op["from"])
            check(system.powershell(script), "设置语言列表")
        elif kind == "ui_override":
            if undo and not value:
                check(system.powershell("Set-WinUILanguageOverride"), "取消显示语言锁定")
            else:
                check(system.powershell(f"Set-WinUILanguageOverride -Language '{validate_tag(str(value))}'"), "锁定显示语言")
        else:
            raise OpError("未知操作：" + kind)
        return
    if kind == "timezone":
        if not value or not IANA_RE.match(str(value)):
            raise OpError("时区不合法：" + repr(value))
        check(system.run(["/usr/bin/sudo", "/usr/sbin/systemsetup", "-settimezone", str(value)], timeout=120), "设置时区")
    elif kind == "apple_locale":
        if undo and not op.get("existed"):
            system.run(["/usr/bin/defaults", "delete", "-g", "AppleLocale"])
        else:
            if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z]{4})?_[A-Z]{2}$", str(value)):
                raise OpError("AppleLocale 不合法：" + repr(value))
            check(system.run(["/usr/bin/defaults", "write", "-g", "AppleLocale", "-string", str(value)]), "设置地区")
    elif kind == "apple_languages":
        if undo and not op.get("existed"):
            system.run(["/usr/bin/defaults", "delete", "-g", "AppleLanguages"])
        else:
            tags = [validate_tag(str(t)) for t in value]
            check(system.run(["/usr/bin/defaults", "write", "-g", "AppleLanguages", "-array", *tags]), "设置首选语言")
    else:
        raise OpError("未知操作：" + kind)


def batch_root(env: Env) -> pathlib.Path:
    return env.home / BATCH_ROOT_NAME


def execute(env: Env, system: System, rec: dict, log=print) -> pathlib.Path:
    batch = batch_root(env) / now_stamp()
    suffix = 1
    while batch.exists():
        suffix += 1
        batch = batch_root(env) / f"{now_stamp()}-{suffix}"
    manifest = {
        "version": VERSION, "platform": env.platform, "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "exit": rec.get("exit"), "country": rec.get("country"), "timezone_iana": rec.get("timezone_iana"),
        "ops": [dict(op, applied=False) for op in rec["ops"]], "status": "started",
    }
    write_json_atomic(batch / "manifest.json", manifest)
    log("已写入备份：" + str(batch / "manifest.json"))
    failures = []
    for index, op in enumerate(manifest["ops"]):
        try:
            apply_op(env, system, op)
        except (OpError, ValueError) as error:
            failures.append(f"{op['title']}：{error}")
            log("  ✗ " + op["title"] + "：" + str(error))
            continue
        manifest["ops"][index]["applied"] = True
        manifest["ops"][index]["applied_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        write_json_atomic(batch / "manifest.json", manifest)
        log("  ✓ " + op["title"])
    manifest["status"] = "partial" if failures else "done"
    manifest["failures"] = failures
    write_json_atomic(batch / "manifest.json", manifest)
    return batch


def list_batches(env: Env) -> list[tuple[pathlib.Path, dict]]:
    root = batch_root(env)
    out = []
    if root.is_dir():
        for child in sorted(root.iterdir(), reverse=True):
            data = read_json(child / "manifest.json")
            if isinstance(data, dict):
                out.append((child, data))
    return out


def rollback(env: Env, system: System, batch: pathlib.Path, dry_run: bool = False, log=print) -> list[str]:
    manifest = read_json(batch / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("ops"), list):
        raise SystemExit("不是有效的批次目录（缺少 manifest.json）：" + str(batch))
    if manifest.get("platform") != env.platform:
        raise SystemExit("这个批次是在另一个系统上生成的，拒绝还原。")
    allowed = {"windows": {"timezone", "home_location", "languages", "ui_override"},
               "darwin": {"timezone", "apple_locale", "apple_languages"}}[env.platform]
    failures = []
    for index in range(len(manifest["ops"]) - 1, -1, -1):
        op = manifest["ops"][index]
        if not op.get("applied"):
            continue
        if op.get("kind") not in allowed:
            failures.append("备份内容不可信：未知操作 " + str(op.get("kind")))
            continue
        title = "还原 · " + op.get("title", op["kind"])
        if dry_run:
            log("  将" + title)
            continue
        try:
            apply_op(env, system, op, undo=True)
        except (OpError, ValueError) as error:
            failures.append(f"{title}：{error}")
            log("  ✗ " + title + "：" + str(error))
            continue
        manifest["ops"][index]["applied"] = False
        write_json_atomic(batch / "manifest.json", manifest)
        log("  ✓ " + title)
    if not dry_run:
        manifest["status"] = "rolled_back" if not failures else "rollback_partial"
        write_json_atomic(batch / "manifest.json", manifest)
    return failures


# --------------------------------------------------------------------------- 命令行

def require_terminal(env: Env) -> None:
    if under_claude_code(env.environ):
        raise SystemExit("检测到由 Claude Code 启动：写操作必须由你自己在普通终端里运行。")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit("写操作需要在真实终端里运行并亲自输入确认词（stdin/stdout 不是终端）。")


def overrides_from(args) -> dict:
    return {
        "country": (args.country or "").upper() or None, "timezone": args.timezone, "windows_timezone": args.windows_timezone,
        "english_first": args.english_first, "skip_languages": args.skip_languages, "skip_region": args.skip_region,
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Claude 浏览器环境、时区与语言：审计 / 计划 / 执行 / 回滚")
    parser.add_argument("--audit", action="store_true", help="只读审计（AI 可以运行）")
    parser.add_argument("--plan", action="store_true", help="只读：列出 --apply 将要做的修改")
    parser.add_argument("--apply", action="store_true", help="对齐系统时区、国家或地区、语言列表（真实终端，输入 CONFIRM）")
    parser.add_argument("--json", action="store_true", help="与 --audit / --plan 一起用，输出 JSON")
    parser.add_argument("--proxy", help="额外经这个代理探测出口，例如 http://127.0.0.1:7897（默认用系统代理）")
    parser.add_argument("--no-network", action="store_true", help="不探测出口 IP")
    parser.add_argument("--country", help="显式指定出口国家，例如 US、SG")
    parser.add_argument("--timezone", help="显式指定 IANA 时区，例如 America/Los_Angeles")
    parser.add_argument("--windows-timezone", help="显式指定 Windows 时区 ID，例如 \"Pacific Standard Time\"")
    parser.add_argument("--english-first", action="store_true", help="英语排第一（Windows 会锁定当前显示语言）")
    parser.add_argument("--skip-languages", action="store_true", help="不改语言列表")
    parser.add_argument("--skip-region", action="store_true", help="不改国家或地区（macOS 为 AppleLocale）")
    parser.add_argument("--list-batches", action="store_true", help="列出历史批次")
    parser.add_argument("--rollback", metavar="BATCH_DIR", help="按批次还原")
    parser.add_argument("--dry-run", action="store_true", help="与 --rollback 一起用，只显示将要还原的内容")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)

    env = build_env()
    if env.platform not in ("windows", "darwin"):
        print("只支持 Windows 与 macOS。")
        return 2
    system = System(env)
    overrides = overrides_from(args)
    if args.timezone and not IANA_RE.match(args.timezone):
        print("时区名不合法：" + args.timezone)
        return 2

    if args.list_batches:
        batches = list_batches(env)
        if not batches:
            print("没有历史批次。")
        for path, data in batches:
            applied = sum(1 for op in data.get("ops", []) if op.get("applied"))
            print(f"{path}  {data.get('status')}  {data.get('country')} {data.get('timezone_iana')}  已应用 {applied} 项")
        return 0

    if args.rollback:
        batch = pathlib.Path(args.rollback).expanduser()
        if args.dry_run:
            failures = rollback(env, system, batch, dry_run=True)
            return 1 if failures else 0
        require_terminal(env)
        print("将按 " + str(batch / "manifest.json") + " 还原，下面是将要还原的内容：")
        rollback(env, system, batch, dry_run=True)
        if input("确认还原请输入 RESTORE：").strip() != "RESTORE":
            print("已取消。")
            return 1
        failures = rollback(env, system, batch)
        print("还原完成。" if not failures else "部分还原失败：\n  " + "\n  ".join(failures))
        print("注销并重新登录一次，语言设置才会完全恢复。")
        return 1 if failures else 0

    if args.apply:
        require_terminal(env)
        report = audit(env, system, proxy=args.proxy, network=not args.no_network, overrides=overrides)
        rec = report["recommendation"]
        print("出口：" + fmt_exit(rec.get("exit")))
        for note in rec.get("notes", []):
            print("注意：" + note)
        if not rec.get("ready"):
            print("条件不满足，没有做任何修改。")
            return 1
        if not rec["ops"]:
            print("系统时区与语言已经和出口一致，无需修改。")
            return 0
        print("\n将要修改：")
        for op in rec["ops"]:
            print("  · " + op["title"])
        print("\n原值会先写入 " + str(batch_root(env)) + "，可以 --rollback 还原。")
        if input("确认执行请输入 CONFIRM：").strip() != "CONFIRM":
            print("已取消。")
            return 1
        batch = execute(env, system, rec)
        data = read_json(batch / "manifest.json") or {}
        if data.get("status") == "partial":
            print("部分完成。已完成的项可以这样还原：")
        else:
            print("完成。之后要还原：")
        print(f'  {"py -3" if env.platform == "windows" else "python3"} "{pathlib.Path(__file__).resolve()}" --rollback "{batch}"')
        print("请完全退出并重新打开浏览器（时区才会刷新）；语言列表与地区的改动注销重新登录后完全生效。")
        return 1 if data.get("status") == "partial" else 0

    report = audit(env, system, proxy=args.proxy, network=not args.no_network, overrides=overrides)
    if args.plan:
        rec = report["recommendation"]
        if args.json:
            print(json.dumps(rec, ensure_ascii=False, indent=2))
        else:
            print("出口：" + fmt_exit(rec.get("exit")))
            for op in rec.get("ops", []):
                print("  · " + op["title"])
            if rec.get("ready") and not rec.get("ops"):
                print("  无需修改。")
            for note in rec.get("notes", []):
                print("注意：" + note)
        return 0
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_audit(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
