"""通用工具：路径、原子写入、shell / PowerShell 引号转义、时间戳。"""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# 名字固定：用户手上的急救命令按这些名字写，不要改。
BACKUP_ROOT_NAME = "clash-guard-backup"
WORK_ROOT_NAME = "claude-network"
MAC_GUARD_DIR = "/Library/Application Support/clash-guard"
MAC_PF_ANCHOR = "com.apple/clash-guard"
MAC_LAUNCHD_LABEL = "local.clash-guard"
WIN_GUARD_DIR = r"C:\ProgramData\clash-guard"
WIN_FW_GROUP = "clash-guard"
WIN_TASK_NAME = "clash-guard-killswitch"
EMERGENCY_FILE_NAME = "断网急救.txt"

GROUP_OVERSEAS = "海外出口"
GROUP_JUMP = "跳板"
RESIDENTIAL_NODE = "住宅出口"
DRILL_NODE = "演练-断线(勿选)"

CN_DNS = ["223.5.5.5", "119.29.29.29"]
OVERSEAS_DOH = ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]


def platform_name():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "darwin"
    return sys.platform


def home():
    return os.path.expanduser("~")


def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def work_root():
    # CNET_WORK_ROOT / CNET_BACKUP_ROOT / CNET_DESKTOP 只用于离线测试，正常使用不要设置
    return os.environ.get("CNET_WORK_ROOT") or os.path.join(home(), WORK_ROOT_NAME)


def backup_root():
    return os.environ.get("CNET_BACKUP_ROOT") or os.path.join(home(), BACKUP_ROOT_NAME)


def desktop_dir():
    """桌面目录。Windows 优先读注册表里的真实桌面（可能被 OneDrive 重定向）。"""
    if os.environ.get("CNET_DESKTOP"):
        return os.environ["CNET_DESKTOP"]
    if IS_WINDOWS:
        try:
            import winreg  # noqa: PLC0415

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            value, _ = winreg.QueryValueEx(key, "Desktop")
            path = os.path.expandvars(value)
            if os.path.isdir(path):
                return path
        except OSError:
            pass
    return os.path.join(home(), "Desktop")


def ensure_private_dir(path):
    os.makedirs(path, exist_ok=True)
    if not IS_WINDOWS:
        os.chmod(path, 0o700)
    return path


def write_text(path, text, private=False, executable=False, newline="\n", bom=False):
    """原子写入文本。private=True 时权限 0600（POSIX）。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp = path + ".tmp-cnet"
    data = text.replace("\r\n", "\n")
    if newline != "\n":
        data = data.replace("\n", newline)
    encoding = "utf-8-sig" if bom else "utf-8"
    with open(temp, "w", encoding=encoding, newline="") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if not IS_WINDOWS:
        mode = 0o600 if private else 0o644
        if executable:
            mode = 0o700 if private else 0o755
        os.chmod(temp, mode)
    os.replace(temp, path)
    return path


def read_text(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.read()
    except OSError:
        return default


def write_json(path, data, private=False):
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", private=private)


def read_json(path, default=None):
    text = read_text(path)
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sh_quote(value):
    """POSIX shell 单引号字面量。"""
    return "'" + str(value).replace("'", "'\\''") + "'"


PS_SINGLE_QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


def ps_quote(value):
    """PowerShell 单引号字面量。

    PowerShell 把 ‘ ’ ‚ ‛（U+2018~201B）也当成单引号：只双写 ASCII 的 ' 不够，
    订阅里节点名是不可信输入，一个弯引号就能截断字符串并注入命令（演练脚本以管理员身份运行）。
    每种引号字符都双写，PowerShell 会把成对的它们还原成一个。
    """
    text = str(value)
    for quote in PS_SINGLE_QUOTES:
        text = text.replace(quote, quote + quote)
    return "'" + text + "'"


def url_path_escape(value):
    from urllib.parse import quote  # noqa: PLC0415

    return quote(str(value), safe="")


def run(cmd, timeout=30, env=None, input_text=None):
    """执行命令并返回 (退出码, 标准输出, 标准错误)。命令不存在时返回 127。"""
    try:
        completed = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            timeout=timeout,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except FileNotFoundError:
        return 127, "", "command not found: " + str(cmd[0])
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as error:
        return 126, "", str(error)


def powershell(script, timeout=60):
    """在 Windows PowerShell 5.1 中执行脚本，强制 UTF-8 输出（中文名不乱码）。"""
    prefix = (
        "$ErrorActionPreference='Continue';$ProgressPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$OutputEncoding=[Text.Encoding]::UTF8;"
    )
    return run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + script],
        timeout=timeout,
    )


def clean_env():
    """去掉代理环境变量的环境：测的是虚拟网卡，不是代理端口。"""
    env = dict(os.environ)
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy", "socks_proxy"):
            env.pop(key, None)
    return env


def curl_binary():
    if IS_WINDOWS:
        system_curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if os.path.exists(system_curl):
            return system_curl
    if os.path.exists("/usr/bin/curl"):
        return "/usr/bin/curl"
    return shutil.which("curl") or "curl"


def curl(url, timeout=15, extra=None, follow=False):
    """普通用户身份、清掉代理变量、--noproxy '*' 的 curl。返回 (退出码, 正文, 错误)。"""
    cmd = [curl_binary(), "--noproxy", "*", "-sS", "--max-time", str(timeout)]
    if follow:
        cmd.append("-L")
    if extra:
        cmd.extend(extra)
    cmd.append(url)
    return run(cmd, timeout=timeout + 5, env=clean_env())


def eprint(*args):
    print(*args, file=sys.stderr)


def configure_stdout():
    """Windows 控制台默认 GBK，统一改成 UTF-8，避免中文节点名乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
