#!/usr/bin/env python3
"""在 Windows 和 macOS 上审计、备份并清理 Claude Code / Claude Desktop 的本机状态。

两档模式：
- 基础清理：缓存日志、登录环境（凭据文件、钥匙串/凭据管理器、账号字段、settings 里的中转站与密钥）、
  轮换 userID/machineID、会话与项目、记忆。
- 深度清理：在基础之上，把整个配置目录（MCP、插件、skills、hooks、设置、后台会话……）与全局配置都挪走，
  只用原来的遥测键重建一个 settings.json，相当于从未用过 Claude Code。

原则：只读审计可以交给 AI；写操作必须在真实终端里由用户输入 CONFIRM。不永久删除——所有挪走的东西
按类别存进 ~/ClaudeCleanup/<时间戳>/，原地修改的文件先留原件，注册表先导出，可用 --rollback 整批还原。
不结束进程、不改遥测、不碰企业托管设置与项目源码。只用标准库。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fnmatch
import glob
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata

VERSION = "0.2.0"
TELEMETRY_KEYS = ("DISABLE_TELEMETRY", "DO_NOT_TRACK", "DISABLE_ERROR_REPORTING", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
TRUTHY = {"1", "true", "yes", "on"}
OAUTH_SUFFIXES = ("", "-custom-oauth", "-staging-oauth", "-local-oauth")
GLOBAL_CONFIG_RE = re.compile(r"^\.claude(-custom-oauth|-staging-oauth|-local-oauth)?\.json$")
NATIVE_HOST = "com.anthropic.claude_code_browser_extension"
WIN_BROWSER_KEYS = (
    r"Google\Chrome", r"Google\Chrome Beta", r"Google\Chrome Dev", r"Google\Chrome SxS", r"Chromium",
    r"Microsoft\Edge", r"BraveSoftware\Brave-Browser", r"Vivaldi", r"Opera Software",
)
MSIX_PACKAGES = ("Claude_pzs8sxrjxfjjc", "AnthropicPBC.Claude_fnn82j28hfe8t")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# 全局配置（.claude.json）里要清除的字段
ACCOUNT_KEYS = frozenset({  # 登录与账号缓存（沿用 suyuan，并补充源码里见到的字段）
    "oauthAccount", "primaryApiKey", "customApiKeyResponses", "anonymousId",
    "additionalModelCostsCache", "additionalModelOptionsCache", "autoCompactWindowsCache",
    "cachedChromeExtensionInstalled", "cachedDynamicConfigs", "cachedExperimentData", "cachedExperimentFeatures",
    "cachedExtraUsageDisabledReason", "cachedFeatureFlags", "cachedFeatureFlagsAt", "cachedGrowthBookFeatures",
    "cachedGrowthBookFeaturesAt", "cachedStatsigGates", "clientDataCache", "clientDataCacheSlots",
    "feedbackSurveyState", "groveConfigCache", "metricsStatusCache", "modelAccessCache",
    "orgModelDefaultCache", "passesEligibilityCache", "s1mAccessCache",
})
PROJECT_KEYS = frozenset({"projects", "githubRepoPaths"})  # 每个打开过的项目路径、信任与权限、项目级 MCP

# settings.json 里属于"登录/中转站/代理"的键：基础清理会删掉它们，其余设置（含遥测键）原样保留
RELAY_ENV_RE = re.compile(
    r"^(ANTHROPIC_[A-Z0-9_]+|CLAUDE_CODE_USE_(BEDROCK|VERTEX|FOUNDRY)|CLAUDE_CODE_SKIP_(BEDROCK|VERTEX|FOUNDRY)_AUTH"
    r"|AWS_[A-Z0-9_]+|CLOUD_ML_REGION|VERTEX_REGION_[A-Z0-9_]+|HTTPS?_PROXY|ALL_PROXY|NO_PROXY)$", re.I)
RELAY_TOP_KEYS = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport")
SHELL_RC = (".zshrc", ".zprofile", ".zshenv", ".bashrc", ".bash_profile", ".profile")

# ---------------------------------------------------------------- 分类（依据 2.1.325 源码枚举配置目录）

CATEGORIES = {
    "cache": "01-缓存日志",
    "login": "02-登录凭据",
    "sessions": "03-会话与项目",
    "memory": "04-记忆",
    "mcp": "05-MCP与插件",
    "custom": "06-自定义配置",
    "settings": "07-设置",
    "state": "08-后台会话与状态",
    "global": "09-全局配置",
    "desktop": "10-ClaudeDesktop",
    "registrations": "11-系统注册",
    "uninstall": "12-CLI程序",
    "repo-local": "13-项目内个人配置",
    "other": "99-其他",
}
BASIC_CATEGORIES = ("cache", "login", "sessions", "memory")

# 配置目录顶层条目 -> (类别, 说明)
CONFIG_MAP: dict[str, tuple[str, str]] = {}
for _cat, _items in {
    "cache": (
        ("cache", "通用缓存"), ("paste-cache", "粘贴缓存"), ("image-cache", "图片缓存"), ("mcp-discovery-cache", "MCP 发现缓存"),
        ("statsig", "旧版开关缓存"), ("stats-cache.json", "统计缓存"), ("telemetry", "未发出的遥测批次"), ("traces", "追踪记录"),
        ("startup-perf", "启动性能记录"), ("debug", "调试日志"), ("shell-snapshots", "Shell 快照"), ("dump-prompts", "提示词转储"),
        ("feedback-bundles", "反馈打包"), ("mcp-needs-auth-cache.json", "MCP 鉴权缓存"), ("gh-pr-status-cache.json", "PR 状态缓存"),
        (".last-update-result.json", "更新结果"), ("logs", "日志"), ("api-dumps", "API 转储"),
    ),
    "login": (
        (".credentials.json", "CLI 登录凭据（明文）"), (".device-keys.json", "设备密钥库"), ("hfi-auth.json", "附加鉴权令牌"),
        (".session_ingress_token", "会话接入令牌"),
    ),
    "sessions": (
        ("projects", "会话转录与项目自动记忆（目录名就是项目绝对路径）"), ("sessions", "会话注册表（含用户名）"),
        ("history.jsonl", "提示词历史（含项目路径）"), ("file-history", "被修改文件的原始副本"), ("todos", "待办"), ("tasks", "任务"),
        ("plans", "计划"), ("session-env", "会话环境脚本"), ("shares", "分享导出"), ("uploads", "上传暂存"),
        ("usage-data", "/insights 报告"), ("ide", "IDE 连接锁"), ("file-transfers", "文件传输暂存"), ("downloads", "下载暂存"),
        ("server-sessions.json", "服务会话记录"), ("active-time.json", "活跃时间记录"),
    ),
    "memory": (("memory", "用户记忆"), ("agent-memory", "子代理记忆")),
    "mcp": (("plugins", "插件与市场"), ("mcp-servers", "MCP 服务器"), ("cowork_plugins", "Cowork 插件"), ("mcp-skill-archives", "MCP skill 包")),
    "custom": (
        ("skills", "skills"), ("commands", "斜杠命令"), ("agents", "子代理定义"), ("hooks", "hooks 脚本"), ("rules", "规则"),
        ("themes", "主题"), ("keybindings.json", "快捷键"), ("workflows", "工作流"), ("teams", "团队"), ("output-styles", "输出风格"),
        ("scheduled-tasks", "定时任务"), ("scripts", "脚本"), ("CLAUDE.md", "用户级指令"), ("loop.md", "loop 指令"), ("dev-mods", "开发模组"),
    ),
    "settings": (
        ("settings.json", "用户设置（hooks、权限、env、中转站……）"), ("settings.local.json", "本地设置"),
        ("remote-settings.json", "服务端下发设置缓存"), ("policy-limits.json", "策略限制缓存"),
    ),
    "state": (
        ("jobs", "后台会话"), ("daemon", "后台会话服务"), ("daemon.json", "后台服务配置"), ("daemon.log", "后台服务日志"),
        ("daemon.log.1", "后台服务日志"), ("state", "运行状态"), ("local", "旧版本地安装"), ("ccr", "远程会话"),
        ("chrome", "Claude in Chrome 状态"), ("remote-control", "远程控制"), ("feedback", "反馈草稿"), ("seed-admin", "管理种子"),
        ("bridge-spawn", "桥接进程"), ("computer-use.lock", "锁文件"), ("server.lock", "锁文件"), ("antproto.json", "协议缓存"),
        (".last-cleanup", "清理节流标记"), (".update.lock", "更新锁"), (".deep-link-register-failed", "深链注册失败标记"),
        (".npm-cache-cleanup", "清理标记"), (".version-cleanup", "清理标记"),
    ),
    "global": ((".config.json", "旧版全局配置（存在时优先生效）"), ("backups", "全局配置历史备份（含旧 ID 与账号）")),
}.items():
    for _name, _reason in _items:
        CONFIG_MAP[_name] = (_cat, _reason)
CONFIG_GLOBS = (
    ("usage*.jsonl", "cache", "用量日志"), ("settings*.json", "settings", "设置"), ("daemon*.json", "state", "后台服务状态"),
    (".claude*.json", "global", "全局配置"),
)


def classify(name: str) -> tuple[str, str]:
    if name in CONFIG_MAP:
        return CONFIG_MAP[name]
    for pattern, category, reason in CONFIG_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return category, reason
    return "other", "未分类（源码清单里没有）"


# ---------------------------------------------------------------- 环境

@dataclasses.dataclass
class Env:
    platform: str
    home: pathlib.Path
    config_dir: pathlib.Path
    global_dir: pathlib.Path
    config_dir_raw: str
    appdata: pathlib.Path | None = None
    localappdata: pathlib.Path | None = None
    temp: pathlib.Path | None = None
    applications: pathlib.Path | None = None
    xdg_data: pathlib.Path | None = None
    xdg_cache: pathlib.Path | None = None
    custom_oauth: bool = False
    batch_root: pathlib.Path | None = None
    repo_files: set = dataclasses.field(default_factory=set)  # 允许移动的项目内个人配置文件（规范化键）

    def __post_init__(self) -> None:
        if self.batch_root is None:
            self.batch_root = self.home / "ClaudeCleanup"


def current_platform() -> str:
    return {"win32": "windows", "darwin": "darwin"}.get(sys.platform, sys.platform)


def build_env(environ: dict | None = None, platform: str | None = None, home: pathlib.Path | None = None) -> Env:
    environ = dict(os.environ if environ is None else environ)
    platform = platform or current_platform()
    home = pathlib.Path(home or pathlib.Path.home())
    raw = environ.get("CLAUDE_CONFIG_DIR", "").strip()
    config_dir = pathlib.Path(raw).expanduser() if raw else home / ".claude"
    env = Env(
        platform=platform, home=home, config_dir=config_dir, global_dir=config_dir if raw else home, config_dir_raw=raw,
        custom_oauth=bool(environ.get("CLAUDE_CODE_CUSTOM_OAUTH_URL")),
        xdg_data=pathlib.Path(environ["XDG_DATA_HOME"]) if environ.get("XDG_DATA_HOME") else home / ".local" / "share",
        xdg_cache=pathlib.Path(environ["XDG_CACHE_HOME"]) if environ.get("XDG_CACHE_HOME") else home / ".cache",
    )
    if platform == "windows":
        env.appdata = pathlib.Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
        env.localappdata = pathlib.Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        env.temp = pathlib.Path(environ.get("TEMP") or environ.get("TMP") or env.localappdata / "Temp")
    elif platform == "darwin":
        env.temp = pathlib.Path(environ.get("TMPDIR") or tempfile.gettempdir())
        env.applications = pathlib.Path("/Applications")
    return env


# ---------------------------------------------------------------- 路径工具

def key(path, platform: str) -> str:
    text = os.path.normpath(os.path.abspath(str(path)))
    return text.lower() if platform in ("windows", "darwin") else text


def same(a, b, platform: str) -> bool:
    return key(a, platform) == key(b, platform)


def within(child, parent, platform: str) -> bool:
    c, p = key(child, platform), key(parent, platform)
    return c != p and c.startswith(p.rstrip("\\/") + os.sep)


def xp(path) -> str:
    """Windows 上加 \\\\?\\ 前缀，支持超过 260 字符的路径（projects 下的目录名常常很长）。"""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    text = os.path.abspath(text)
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def exists(path) -> bool:
    return os.path.lexists(xp(path))


def is_link_like(path) -> bool:
    try:
        info = os.lstat(xp(path))
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def tree_stats(path) -> tuple[int, int]:
    target = xp(path)
    if not os.path.lexists(target) or is_link_like(path):
        return 0, 0
    if os.path.isfile(target):
        return 1, os.path.getsize(target)
    files = size = 0
    for root, dirs, names in os.walk(target):
        dirs[:] = [d for d in dirs if not is_link_like(os.path.join(root, d))]
        for name in names:
            full = os.path.join(root, name)
            try:
                if not is_link_like(full):
                    files += 1
                    size += os.path.getsize(full)
            except OSError:
                pass
    return files, size


def human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def glob_in(base, pattern: str) -> list[pathlib.Path]:
    if base is None:
        return []
    return sorted(pathlib.Path(p) for p in glob.glob(os.path.join(glob.escape(str(base)), pattern)))


def read_json(path, default=None):
    if default is not None and not exists(path):
        return default
    return json.loads(pathlib.Path(xp(path)).read_text(encoding="utf-8-sig"))


def write_json_atomic(path: pathlib.Path, data) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(os.stat(xp(path)).st_mode) if exists(path) else 0o600
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(xp(path), "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- 目标清单

@dataclasses.dataclass
class Target:
    category: str
    path: pathlib.Path
    reason: str


def config_dir_targets(env: Env) -> list[Target]:
    """配置目录顶层的每个条目（按类别）。"""
    try:
        names = sorted(os.listdir(xp(env.config_dir)))
    except OSError:
        return []
    return [Target(*classify(name)[:1], env.config_dir / name, classify(name)[1]) for name in names]


def external_targets(env: Env) -> list[Target]:
    """配置目录以外、白名单内当前存在的路径。"""
    out: list[Target] = []

    def add(category: str, path, reason: str) -> None:
        if path is not None and exists(path):
            out.append(Target(category, pathlib.Path(path), reason))

    if not same(env.home / ".claude", env.config_dir, env.platform):
        add("login", env.home / ".claude" / ".device-keys.json", "设备密钥库（固定在 ~/.claude，不跟 CLAUDE_CONFIG_DIR）")
    if not same(env.global_dir, env.config_dir, env.platform):
        for path in glob_in(env.global_dir, ".claude*.json"):
            if GLOBAL_CONFIG_RE.match(path.name):
                add("global", path, "全局配置（userID、账号、MCP、项目记录）")
    for match in glob_in(env.temp, "cc-transcript-*.txt"):
        add("cache", match, "临时会话转录")
    for match in glob_in(env.temp, "cc-daemon-*"):
        add("cache", match, "后台会话临时文件")
    add("cache", env.temp / "claude-cli-nodejs" if env.temp else None, "临时 CLI 缓存")
    add("uninstall", env.home / ".local" / "bin" / ("claude.exe" if env.platform == "windows" else "claude"), "原生安装的 claude")
    add("uninstall", env.xdg_data / "claude" if env.xdg_data else None, "原生安装的版本目录")
    add("uninstall", env.xdg_cache / "claude" if env.xdg_cache else None, "原生安装的下载暂存")

    if env.platform == "windows":
        a, la = env.appdata, env.localappdata
        add("cache", la / "claude-cli-nodejs", "CLI 缓存与 MCP 日志")
        desktop = a / "Claude"
        for name in ("logs", "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "Crashpad"):
            add("cache", desktop / name, "Desktop 缓存/日志")
        add("desktop", desktop, "Desktop 数据：登录态、Code 会话列表、scratch-workspaces、Cowork、MCP 配置")
        add("desktop", la / "Claude-3p", "Desktop 第三方模式数据")
        add("desktop", a / "Claude-3p", "Desktop 第三方模式数据（旧位置）")
        for package in MSIX_PACKAGES:
            add("desktop", la / "Packages" / package / "LocalCache" / "Roaming" / "Claude", "商店版 Desktop 数据")
        add("desktop-app", la / "AnthropicClaude", "Desktop 应用本体")
        add("desktop-app", la / "Programs" / "Claude", "Desktop 应用本体")
        add("registrations", a / "Claude Code" / "ChromeNativeHost", "Claude in Chrome 原生宿主")
    elif env.platform == "darwin":
        lib, support = env.home / "Library", env.home / "Library" / "Application Support"
        add("cache", lib / "Caches" / "claude-cli-nodejs", "CLI 缓存与 MCP 日志")
        add("cache", lib / "Logs" / "claude-cli-nodejs", "CLI 日志")
        add("cache", lib / "Logs" / "Claude", "Desktop 日志")
        for match in glob_in(lib / "Caches", "com.anthropic.claudefordesktop*"):
            add("cache", match, "Desktop 缓存")
        for match in glob_in(lib / "Logs" / "DiagnosticReports", "Claude*") + glob_in(support / "CrashReporter", "Claude*"):
            add("cache", match, "崩溃报告")
        for path, reason in (
            (support / "Claude", "Desktop 数据：登录态、Code 会话列表、scratch-workspaces、Cowork、虚拟机镜像、MCP 配置"),
            (support / "Claude-3p", "Desktop 第三方模式数据"), (support / "com.anthropic.claudefordesktop", "Desktop 数据"),
            (lib / "HTTPStorages" / "com.anthropic.claudefordesktop", "Desktop 网络存储"),
            (lib / "WebKit" / "com.anthropic.claudefordesktop", "Desktop WebKit 数据"),
            (lib / "Preferences" / "com.anthropic.claudefordesktop.plist", "Desktop 偏好"),
            (lib / "Saved Application State" / "com.anthropic.claudefordesktop.savedState", "Desktop 窗口状态"),
        ):
            add("desktop", path, reason)
        for match in glob_in(lib / "Preferences" / "ByHost", "com.anthropic.claudefordesktop*.plist"):
            add("desktop", match, "Desktop 偏好（ByHost）")
        add("desktop-app", env.applications / "Claude.app" if env.applications else None, "Desktop 应用本体")
        add("uninstall", support / "claude-cli-nodejs", "CLI 数据目录")
        add("registrations", env.home / "Applications" / "Claude Code URL Handler.app", "claude-cli:// 深链处理器")
        for pattern in (f"*/NativeMessagingHosts/{NATIVE_HOST}.json", f"*/*/NativeMessagingHosts/{NATIVE_HOST}.json"):
            for match in glob_in(support, pattern):
                add("registrations", match, "Claude in Chrome 原生宿主清单")
    for path in repo_local_files(env):
        add("repo-local", path, "项目内个人配置（可能含中转站或密钥，通常不进 Git）")
    return out


def all_targets(env: Env) -> list[Target]:
    seen, out = set(), []
    for target in config_dir_targets(env) + external_targets(env):
        k = key(target.path, env.platform)
        if k not in seen:
            seen.add(k)
            out.append(target)
    return out


def collapse(targets: list[Target], platform: str) -> list[Target]:
    return [t for t in targets if not any(within(t.path, o.path, platform) for o in targets if o is not t)]


def managed_settings_dir(env: Env) -> pathlib.Path:
    return pathlib.Path(r"C:\Program Files\ClaudeCode") if env.platform == "windows" else pathlib.Path("/Library/Application Support/ClaudeCode")


def allowed_roots(env: Env) -> list[pathlib.Path]:
    return [p for p in (env.home, env.config_dir, env.global_dir, env.appdata, env.localappdata, env.temp, env.xdg_data, env.xdg_cache) if p]


def check_target(path: pathlib.Path, env: Env, protected: list[pathlib.Path]) -> None:
    p = env.platform
    if not os.path.isabs(str(path)):
        raise RuntimeError(f"目标不是绝对路径：{path}")
    app = env.applications / "Claude.app" if env.applications else None
    explicit = key(path, p) in env.repo_files or (app is not None and same(path, app, p))
    if not explicit and not any(within(path, root, p) for root in allowed_roots(env)):
        raise RuntimeError(f"目标不在允许的根目录内：{path}")
    for root in (env.home, env.config_dir, env.global_dir, env.appdata, env.localappdata, env.temp, env.batch_root):
        if root is not None and (same(path, root, p) or within(root, path, p)):
            raise RuntimeError(f"拒绝移动根目录或其上级：{path}")
    for rule in protected:
        if same(path, rule, p) or within(path, rule, p) or within(rule, path, p):
            raise RuntimeError(f"目标与保护路径重叠，已停止：{path}（保护路径 {rule}）")
    managed = managed_settings_dir(env)
    if same(path, managed, p) or within(path, managed, p):
        raise RuntimeError(f"拒绝触碰企业托管设置：{path}")
    if within(path, env.batch_root, p):
        raise RuntimeError(f"目标位于批次目录内：{path}")
    if is_link_like(path):
        raise RuntimeError(f"目标是符号链接或目录联接，拒绝移动：{path}")


# ---------------------------------------------------------------- 全局配置

def global_config_files(env: Env) -> dict:
    legacy = env.config_dir / ".config.json"
    variants = [p for p in glob_in(env.global_dir, ".claude*.json") if GLOBAL_CONFIG_RE.match(p.name) and os.path.isfile(xp(p))]
    active = env.global_dir / (".claude-custom-oauth.json" if env.custom_oauth else ".claude.json")
    legacy_active = False
    try:
        info = os.stat(xp(legacy))
        if stat.S_ISREG(info.st_mode) and (info.st_size > 0 or info.st_mode & 0o222):
            active, legacy_active = legacy, True
    except OSError:
        pass
    backups = [p for p in glob_in(env.config_dir / "backups", ".claude*.json.backup*") if os.path.isfile(xp(p)) and not is_link_like(p)]
    return {"active": active, "active_exists": exists(active), "legacy_active": legacy_active, "variants": variants,
            "legacy": legacy if exists(legacy) else None, "backups": backups}


def identity_files(env: Env) -> list[pathlib.Path]:
    """所有需要同步修改的全局配置：各后缀版本、旧版 .config.json、backups 里的历史备份。
    backups 必须一起改：.claude.json 丢失或损坏时，Claude Code 会从这里恢复旧值。"""
    info = global_config_files(env)
    files = list(info["variants"]) + ([info["legacy"]] if info["legacy"] else []) + info["backups"]
    seen, out = set(), []
    for path in files:
        k = key(path, env.platform)
        if k not in seen and not is_link_like(path):
            seen.add(k)
            out.append(path)
    return out


def scrub_global_config(data: dict, ids: dict) -> dict:
    """基础清理对全局配置的修改：清登录与账号缓存、清项目记录、换新 ID；其他字段（主题、MCP 等）保留。"""
    updated = {k: v for k, v in data.items() if k not in ACCOUNT_KEYS and k not in PROJECT_KEYS}
    updated.update(ids)
    return updated


def config_projects(env: Env) -> list[str]:
    paths: list[str] = []
    for path in identity_files(env):
        try:
            data = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("projects"), dict):
            paths += list(data["projects"].keys())
    return list(dict.fromkeys(paths))


def config_mcp_servers(env: Env) -> dict:
    info = global_config_files(env)
    try:
        data = read_json(info["active"], {})
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    user = sorted((data.get("mcpServers") or {}).keys()) if isinstance(data.get("mcpServers"), dict) else []
    project = sum(len(p.get("mcpServers") or {}) for p in (data.get("projects") or {}).values() if isinstance(p, dict))
    return {"user": user, "project_scoped": project}


def repo_local_files(env: Env) -> list[pathlib.Path]:
    """项目目录里的个人配置（Claude 默认不提交到 Git）。只认这两个文件名；项目源码一概不碰。"""
    out = []
    for project in config_projects(env):
        root = pathlib.Path(project)
        if not root.is_absolute() or not root.is_dir():
            continue
        for path in (root / ".claude" / "settings.local.json", root / "CLAUDE.local.md"):
            if os.path.isfile(xp(path)) and not is_link_like(path):
                out.append(path)
    env.repo_files = {key(p, env.platform) for p in out}
    return out


def repo_shared_files(env: Env) -> list[str]:
    """项目里通常提交到 Git、与团队共享的 Claude 文件：只报告，不处理。"""
    out = []
    for project in config_projects(env):
        root = pathlib.Path(project)
        for path in (root / ".mcp.json", root / ".claude" / "settings.json", root / "CLAUDE.md"):
            if root.is_absolute() and os.path.isfile(xp(path)):
                out.append(str(path))
    return out


# ---------------------------------------------------------------- settings.json

def settings_files(env: Env) -> list[pathlib.Path]:
    return [p for p in (env.config_dir / "settings.json", env.config_dir / "settings.local.json") if os.path.isfile(xp(p))]


def load_settings(path: pathlib.Path) -> dict:
    data = read_json(path, {})
    if not isinstance(data, dict) or not isinstance(data.get("env", {}), dict):
        raise ValueError(f"{path.name} 的顶层或 env 结构异常，拒绝修改")
    return data


def settings_telemetry(env: Env) -> dict:
    environment = load_settings(env.config_dir / "settings.json").get("env", {})
    return {k: environment.get(k) for k in TELEMETRY_KEYS}


def relay_keys(data: dict) -> list[str]:
    found = [f"env.{k}" for k in (data.get("env") or {}) if RELAY_ENV_RE.match(k) and k not in TELEMETRY_KEYS]
    return found + [k for k in RELAY_TOP_KEYS if k in data]


def scrub_settings(data: dict) -> dict:
    updated = {k: v for k, v in data.items() if k not in RELAY_TOP_KEYS}
    if isinstance(data.get("env"), dict):
        updated["env"] = {k: v for k, v in data["env"].items() if not RELAY_ENV_RE.match(k) or k in TELEMETRY_KEYS}
    return updated


def shell_relay_vars(env: Env, environ: dict) -> list[str]:
    """登录 shell / 用户环境变量里的中转站与密钥：只报告变量名和位置，不读值、不修改。"""
    found = [f"当前进程环境：{k}" for k in environ if RELAY_ENV_RE.match(k) and k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE"))]
    if env.platform == "windows" and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                index = 0
                while True:
                    try:
                        name = winreg.EnumValue(handle, index)[0]
                    except OSError:
                        break
                    if name.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE")):
                        found.append(f"用户环境变量（HKCU\\Environment）：{name}")
                    index += 1
        except OSError:
            pass
    else:
        for rc in SHELL_RC:
            path = env.home / rc
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                match = re.search(r"\b(ANTHROPIC_[A-Z0-9_]+)\s*=", line)
                if match and not line.lstrip().startswith("#"):
                    found.append(f"~/{rc} 第 {number} 行：{match.group(1)}")
    return found


# ---------------------------------------------------------------- 凭据位置

def keychain_services(env: Env) -> list[str]:
    """按源码 Tye() 的规则拼出可能的钥匙串服务名。"""
    hashes = [""]
    if env.config_dir_raw:
        raw = {unicodedata.normalize("NFC", env.config_dir_raw), unicodedata.normalize("NFC", str(env.config_dir))}
        hashes = ["-" + hashlib.sha256(c.encode("utf-8")).hexdigest()[:8] for c in sorted(raw)]
    names = [f"Claude Code{s}-credentials{h}" for s in OAUTH_SUFFIXES for h in hashes]
    names += [f"Claude Code{s}{h}" for s in OAUTH_SUFFIXES for h in hashes]
    return list(dict.fromkeys(names + ["Claude Code-device-keys"]))


def registry_keys() -> list[str]:
    return [r"HKCU\Software\Classes\claude-cli"] + [rf"HKCU\Software\{b}\NativeMessagingHosts\{NATIVE_HOST}" for b in WIN_BROWSER_KEYS]


# ---------------------------------------------------------------- 系统交互（测试时替换）

@dataclasses.dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    path: str
    cmd: str


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


class System:
    def __init__(self, platform: str):
        self.platform = platform

    def processes(self) -> list[Proc]:
        if self.platform == "windows":
            script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;Get-CimInstance Win32_Process | "
                      "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
            try:
                result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=60)
                rows = json.loads(result.stdout) if result.stdout.strip() else []
                rows = rows if isinstance(rows, list) else [rows]
                return [Proc(int(r.get("ProcessId") or 0), int(r.get("ParentProcessId") or 0), str(r.get("Name") or ""),
                             str(r.get("ExecutablePath") or ""), str(r.get("CommandLine") or "")) for r in rows]
            except (OSError, ValueError, subprocess.SubprocessError):
                procs = []
                for line in run(["tasklist", "/FO", "CSV", "/NH"]).stdout.splitlines():
                    cells = [c.strip('"') for c in line.split('","')]
                    if len(cells) >= 2 and cells[1].isdigit():
                        procs.append(Proc(int(cells[1]), 0, cells[0], "", ""))
                return procs
        procs = []
        for line in run(["ps", "-axo", "pid=,ppid=,comm=,args="]).stdout.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
                procs.append(Proc(int(parts[0]), int(parts[1]), os.path.basename(parts[2]), parts[2], parts[3] if len(parts) == 4 else ""))
        return procs

    def keychain_exists(self, service: str) -> bool:
        return run(["/usr/bin/security", "find-generic-password", "-s", service]).returncode == 0

    def keychain_delete(self, service: str) -> None:
        for _ in range(10):  # 同一服务名下可能有多个账户
            result = run(["/usr/bin/security", "delete-generic-password", "-s", service])
            if result.returncode == 44:
                return
            if result.returncode != 0:
                raise RuntimeError(f"钥匙串删除失败（{service}）：{result.stderr.strip()}")
        raise RuntimeError(f"钥匙串删除没有收敛：{service}")

    def credman_targets(self) -> list[str]:
        try:
            output = run(["cmdkey", "/list"]).stdout
        except OSError:
            return []
        found = [m.group(1) for m in (re.search(r"target=(.+?)\s*$", line) for line in output.splitlines()) if m]
        return list(dict.fromkeys(t for t in found if t.startswith("Claude Code")))

    def credman_delete(self, target: str) -> None:
        run(["cmdkey", f"/delete:{target}"])
        if target in self.credman_targets():
            run(["cmdkey", f"/delete:LegacyGeneric:target={target}"])
        if target in self.credman_targets():
            raise RuntimeError(f"凭据管理器条目删除失败：{target}")

    def reg_exists(self, reg_key: str) -> bool:
        return run(["reg", "query", reg_key]).returncode == 0

    def reg_export(self, reg_key: str, file: pathlib.Path) -> None:
        result = run(["reg", "export", reg_key, str(file), "/y"])
        if result.returncode != 0 or not file.is_file():
            raise RuntimeError(f"注册表导出失败（{reg_key}）：{result.stderr.strip() or result.stdout.strip()}")

    def reg_delete(self, reg_key: str) -> None:
        result = run(["reg", "delete", reg_key, "/f"])
        if result.returncode != 0:
            raise RuntimeError(f"注册表删除失败（{reg_key}）：{result.stderr.strip() or result.stdout.strip()}")

    def reg_import(self, file: pathlib.Path) -> None:
        result = run(["reg", "import", str(file)])
        if result.returncode != 0:
            raise RuntimeError(f"注册表导入失败（{file}）：{result.stderr.strip() or result.stdout.strip()}")

    def launchd_loaded(self, label: str) -> bool:
        return self.platform == "darwin" and run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"]).returncode == 0


def is_claude_proc(proc: Proc) -> bool:
    name = proc.name.lower()
    text = f"{proc.path} {proc.cmd}".replace("\\", "/").lower()
    if name in {"claude", "claude.exe", "claude-code"} or name.startswith("claude helper") or "/claude.app/" in text:
        return True
    return name in {"node", "node.exe", "bun", "bun.exe"} and "@anthropic-ai/claude-code" in text


def claude_processes(procs: list[Proc], self_pid: int) -> list[Proc]:
    return [p for p in procs if p.pid != self_pid and is_claude_proc(p)]


def under_claude(procs: list[Proc], environ: dict, self_pid: int) -> bool:
    """当前脚本是否由 Claude Code 启动（它给子 shell 设 CLAUDECODE=1；再沿父进程链确认）。"""
    if environ.get("CLAUDECODE") == "1":
        return True
    table = {p.pid: p for p in procs}
    pid = table[self_pid].ppid if self_pid in table else 0
    for _ in range(16):
        proc = table.get(pid)
        if proc is None or pid <= 1:
            return False
        if is_claude_proc(proc) and "/claude.app/" not in f"{proc.path} {proc.cmd}".replace("\\", "/").lower():
            return True
        pid = proc.ppid
    return False


# ---------------------------------------------------------------- 计划

@dataclasses.dataclass
class Plan:
    mode: str  # basic | deep
    moves: list[Target]
    edits: list[tuple[pathlib.Path, dict, str]] = dataclasses.field(default_factory=list)  # (路径, 新内容, 说明)
    keychain: list[str] = dataclasses.field(default_factory=list)
    credman: list[str] = dataclasses.field(default_factory=list)
    registry: list[str] = dataclasses.field(default_factory=list)
    recreate_settings: dict | None = None  # 深度清理后只含遥测键的新 settings.json
    protected: list[pathlib.Path] = dataclasses.field(default_factory=list)
    choices: dict = dataclasses.field(default_factory=dict)
    new_ids: dict = dataclasses.field(default_factory=dict)


def build_plan(env: Env, system: System, choices: dict) -> Plan:
    """choices：mode=basic|deep；desktop=0|1|2；registrations/uninstall/repo_local 为布尔（深度模式可选）。"""
    mode = choices.get("mode", "basic")
    if mode not in ("basic", "deep"):
        raise ValueError(f"未知模式：{mode}")
    categories = set(BASIC_CATEGORIES)
    if mode == "deep":
        categories |= {"mcp", "custom", "settings", "state", "global", "other"}
        if choices.get("registrations"):
            categories.add("registrations")
        if choices.get("uninstall"):
            categories.add("uninstall")
        if choices.get("repo_local"):
            categories.add("repo-local")
    desktop = int(choices.get("desktop", 0))
    if desktop >= 1:
        categories.add("desktop")
    if desktop >= 2:
        categories.add("desktop-app")
    targets = all_targets(env)
    # 基础模式下，配置目录里不在基础类别的条目全部是保护路径
    protected = [] if mode == "deep" else [t.path for t in config_dir_targets(env) if t.category not in BASIC_CATEGORIES]
    if mode == "basic":
        protected += [p for p in identity_files(env)]  # 全局配置原地修改，不移动
    moves = collapse([t for t in targets if t.category in categories and not is_link_like(t.path)], env.platform)
    plan = Plan(mode=mode, moves=moves, protected=protected, choices=dict(choices))

    if mode == "basic":
        files = identity_files(env)
        plan.new_ids = {"userID": secrets.token_hex(32), "machineID": secrets.token_hex(32)}
        for path in files:
            data = read_json(path)
            if not isinstance(data, dict):
                raise ValueError(f"不是 JSON 对象，拒绝修改：{path}")
            plan.edits.append((path, scrub_global_config(data, plan.new_ids), "换新 userID/machineID，清除账号字段与项目记录"))
        for path in settings_files(env):
            data = load_settings(path)
            if relay_keys(data):
                plan.edits.append((path, scrub_settings(data), "删除中转站/密钥/代理键：" + ", ".join(relay_keys(data))))
    else:
        telemetry = {k: v for k, v in settings_telemetry(env).items() if v is not None}
        plan.recreate_settings = {"env": telemetry} if telemetry else None

    if env.platform == "darwin":
        plan.keychain = [s for s in keychain_services(env) if system.keychain_exists(s)]
    elif env.platform == "windows":
        plan.credman = system.credman_targets()
        if "registrations" in categories:
            plan.registry = [k for k in registry_keys() if system.reg_exists(k)]
    for target in plan.moves:
        check_target(target.path, env, plan.protected)
    return plan


def plan_is_empty(plan: Plan) -> bool:
    return not (plan.moves or plan.edits or plan.keychain or plan.credman or plan.registry)


def describe_plan(plan: Plan, env: Env) -> list[str]:
    title = "基础清理" if plan.mode == "basic" else "深度清理"
    lines = [f"最终执行清单（{title}）："]
    by_cat: dict[str, list[Target]] = {}
    for target in plan.moves:
        by_cat.setdefault(target.category, []).append(target)
    lines.append(f"1. 按类别移入批次目录 {env.batch_root}{os.sep}<时间戳>（不永久删除，可 --rollback）：")
    for category in CATEGORIES:
        for target in by_cat.get(category, []):
            lines.append(f"     [{CATEGORIES[category]}] {target.path}")
    for target in by_cat.get("desktop-app", []):
        lines.append(f"     [{CATEGORIES['desktop']}] {target.path}")
    if plan.edits:
        lines.append("2. 原地修改（原件先存进批次目录）：")
        for path, _data, what in plan.edits:
            lines.append(f"     - {path}：{what}")
    if plan.keychain or plan.credman:
        lines.append(f"3. 删除系统凭据（不可回滚，之后需要重新登录）：{', '.join(plan.keychain + plan.credman)}")
    if plan.registry:
        lines.append("4. 导出后删除注册表项：" + ", ".join(plan.registry))
    if plan.mode == "deep":
        keep = ", ".join(plan.recreate_settings["env"]) if plan.recreate_settings else "无（原来没设遥测键）"
        lines.append(f"5. 重建只含遥测键的 settings.json：{keep}")
    else:
        lines.append("保留不动：MCP、插件、skills、hooks、settings 里的其他设置、后台会话（这些属于深度清理）。")
    lines.append("永不处理：企业托管设置、项目源码与项目里提交到 Git 的 Claude 文件。不结束进程，遥测设置逐值不变。")
    return lines


# ---------------------------------------------------------------- 执行

def batch_label(path: pathlib.Path, env: Env) -> str:
    """批次目录里保留原始位置的相对路径，方便人工查看。"""
    anchors = [(env.config_dir, "claude-config"), (env.temp, "Temp"), (env.localappdata, "AppData-Local"),
               (env.appdata, "AppData-Roaming"), (env.home, "home"), (env.applications, "Applications")]
    for anchor, label in anchors:
        if anchor is not None and within(path, anchor, env.platform):
            return os.path.join(label, os.path.relpath(str(path), str(anchor)))
    drive, rest = os.path.splitdrive(os.path.abspath(str(path)))
    return os.path.join("repos", (drive.replace(":", "") + rest).strip("\\/"))


class PartialFailure(RuntimeError):
    def __init__(self, batch_dir: pathlib.Path, cause: BaseException):
        super().__init__(str(cause))
        self.batch_dir = batch_dir
        self.cause = cause


def write_readme(batch_dir: pathlib.Path, manifest: dict) -> None:
    lines = [f"Claude 环境清理批次 {manifest['created']}（{manifest['mode']}），状态：{manifest['status']}", ""]
    counts: dict[str, int] = {}
    for move in manifest["moves"]:
        counts[move["category_dir"]] = counts.get(move["category_dir"], 0) + 1
    lines.append("moved/ 下按类别存放挪走的内容，子目录保留原始位置：")
    lines += [f"  {name}：{count} 项" for name, count in sorted(counts.items())]
    if manifest["edits"]:
        lines.append(f"edited-originals/：{len(manifest['edits'])} 个被原地修改文件的原件")
    if manifest["registry"]:
        lines.append(f"registry/：{len(manifest['registry'])} 个注册表导出")
    lines += ["", "还原整个批次：python claude_env_cleanup.py --rollback \"<本目录>\"",
              "确认不再需要后，可以直接删除本目录。里面有登录凭据和会话内容，请妥善保管。"]
    (batch_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def execute(env: Env, system: System, plan: Plan, log=print) -> pathlib.Path:
    batch_dir = env.batch_root / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    batch_dir.mkdir(parents=True)
    manifest = {
        "version": 2, "tool": VERSION, "platform": env.platform, "mode": plan.mode, "status": "running",
        "created": dt.datetime.now().isoformat(timespec="seconds"), "config_dir": str(env.config_dir), "choices": plan.choices,
        "edits": [], "moves": [], "created_files": [], "registry": [], "keychain_deleted": [], "credman_deleted": [],
        "telemetry_before": None, "error": None,
    }
    path = batch_dir / "manifest.json"
    save = lambda: write_json_atomic(path, manifest)
    save()
    protected_before = [p for p in plan.protected if exists(p)]
    try:
        manifest["telemetry_before"] = settings_telemetry(env)
        save()
        # 1. 原地修改：先存原件并校验，再写入
        for index, (target, _data, what) in enumerate(plan.edits):
            original = batch_dir / "edited-originals" / f"{index:02d}-{target.name}"
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(xp(target), xp(original))
            if sha256_file(target) != sha256_file(original):
                raise RuntimeError(f"原件备份校验失败：{target}")
            manifest["edits"].append({"path": str(target), "original": str(original), "what": what, "done": False})
            save()
        for entry, (target, data, _what) in zip(manifest["edits"], plan.edits):
            write_json_atomic(target, data)
            entry["done"] = True
            save()
        if plan.edits:
            log(f"[修改] {len(plan.edits)} 个文件（userID 已轮换、账号与中转站字段已清除）")
        # 2. 系统凭据（不可回滚）
        for service in plan.keychain:
            system.keychain_delete(service)
            manifest["keychain_deleted"].append(service)
            save()
        for target in plan.credman:
            system.credman_delete(target)
            manifest["credman_deleted"].append(target)
            save()
        # 3. 注册表：先导出
        for index, reg_key in enumerate(plan.registry):
            export = batch_dir / "registry" / f"{index:02d}.reg"
            export.parent.mkdir(parents=True, exist_ok=True)
            system.reg_export(reg_key, export)
            manifest["registry"].append({"key": reg_key, "export": str(export), "deleted": False})
            save()
            system.reg_delete(reg_key)
            manifest["registry"][-1]["deleted"] = True
            save()
        # 4. 按类别挪进批次目录
        for target in plan.moves:
            check_target(target.path, env, plan.protected)
            category = "desktop" if target.category == "desktop-app" else target.category
            destination = batch_dir / "moved" / CATEGORIES[category] / batch_label(target.path, env)
            while exists(destination):
                destination = destination.with_name(destination.name + "-dup")
            destination.parent.mkdir(parents=True, exist_ok=True)
            manifest["moves"].append({"src": str(target.path), "dst": str(destination), "category": target.category,
                                      "category_dir": CATEGORIES[category], "done": False})
            save()
            shutil.move(xp(target.path), xp(destination))
            if exists(target.path):
                raise RuntimeError(f"移动后原路径仍存在：{target.path}")
            manifest["moves"][-1]["done"] = True
            save()
        if plan.moves:
            log(f"[挪走] {len(plan.moves)} 项，已按类别存入 {batch_dir / 'moved'}")
        # 5. 深度清理：重建只含遥测键的 settings.json
        if plan.recreate_settings is not None:
            settings = env.config_dir / "settings.json"
            if exists(settings):
                raise RuntimeError(f"settings.json 仍然存在，拒绝覆盖：{settings}")
            write_json_atomic(settings, plan.recreate_settings)
            manifest["created_files"].append(str(settings))
            save()
        # 6. 复检
        missing = [str(p) for p in protected_before if not exists(p)]
        if missing:
            raise RuntimeError("受保护路径丢失：" + ", ".join(missing))
        if settings_telemetry(env) != manifest["telemetry_before"]:
            raise RuntimeError("遥测设置发生变化")
        manifest["status"] = "done"
        save()
        write_readme(batch_dir, manifest)
        return batch_dir
    except BaseException as error:  # noqa: BLE001 —— 任何中断都记成部分完成
        manifest["status"] = "partial"
        manifest["error"] = f"{type(error).__name__}: {error}"
        try:
            save()
            write_readme(batch_dir, manifest)
        except OSError:
            pass
        raise PartialFailure(batch_dir, error) from error


# ---------------------------------------------------------------- 回滚

def list_batches(env: Env) -> list[tuple[pathlib.Path, dict]]:
    out = []
    for manifest in glob_in(env.batch_root, "*/manifest.json"):
        try:
            out.append((manifest.parent, read_json(manifest)))
        except (OSError, ValueError):
            continue
    return out


def rollback(env: Env, system: System, batch_dir: pathlib.Path, dry_run: bool = False, log=print) -> list[str]:
    batch_dir = pathlib.Path(batch_dir)
    if not within(batch_dir, env.batch_root, env.platform):
        raise RuntimeError(f"批次目录必须位于 {env.batch_root} 内")
    manifest_path = batch_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("platform") != env.platform:
        raise RuntimeError("批次不是在当前平台上生成的")
    for move in manifest.get("moves", []):  # 先整体校验，任何一条可疑就拒绝整个回滚
        if not within(move["dst"], batch_dir, env.platform):
            raise RuntimeError(f"清单被篡改：{move['dst']} 不在批次目录内")
        if not os.path.isabs(move["src"]):
            raise RuntimeError(f"清单被篡改：{move['src']} 不是绝对路径")
    prefix = "[dry-run] " if dry_run else ""
    problems = []
    aside = batch_dir / "rollback-replaced"
    for created in manifest.get("created_files", []):
        if exists(created) and within(created, env.config_dir, env.platform):
            log(f"{prefix}移开清理后新建的 {created}")
            if not dry_run:
                aside.mkdir(exist_ok=True)
                shutil.move(xp(created), xp(aside / pathlib.Path(created).name))
    for move in reversed(manifest.get("moves", [])):
        src, dst = pathlib.Path(move["src"]), pathlib.Path(move["dst"])
        if not exists(dst):
            continue
        if exists(src):
            problems.append(f"原位置已有新文件，跳过：{src}（批次里的版本保留在 {dst}）")
            continue
        log(f"{prefix}还原 {src}")
        if not dry_run:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(xp(dst), xp(src))
    for edit in manifest.get("edits", []):
        target, original = pathlib.Path(edit["path"]), pathlib.Path(edit["original"])
        if not within(original, batch_dir, env.platform) or not exists(original):
            problems.append(f"找不到原件，跳过：{target}")
            continue
        log(f"{prefix}还原文件 {target}")
        if not dry_run:
            if exists(target):
                aside.mkdir(exist_ok=True)
                shutil.copy2(xp(target), xp(aside / original.name))
            shutil.copy2(xp(original), xp(target))
    for entry in manifest.get("registry", []):
        if entry.get("deleted"):
            log(f"{prefix}导入注册表 {entry['export']}")
            if not dry_run:
                system.reg_import(pathlib.Path(entry["export"]))
    gone = manifest.get("keychain_deleted", []) + manifest.get("credman_deleted", [])
    if gone:
        problems.append("已删除的系统凭据无法还原，需要重新登录：" + ", ".join(gone))
    if not dry_run:
        manifest["status"] = "rolled-back"
        write_json_atomic(manifest_path, manifest)
    return problems


# ---------------------------------------------------------------- 审计

def audit(env: Env, system: System, environ: dict | None = None) -> dict:
    environ = dict(os.environ if environ is None else environ)
    procs = system.processes()
    groups: dict[str, list] = {c: [] for c in list(CATEGORIES) + ["desktop-app"]}
    for target in all_targets(env):
        files, size = tree_stats(target.path)
        groups[target.category].append({"path": str(target.path), "reason": target.reason, "files": files, "bytes": size,
                                        "link": is_link_like(target.path)})
    cfg = global_config_files(env)
    settings_info: dict = {}
    for path in settings_files(env):
        try:
            data = load_settings(path)
            settings_info[path.name] = {"relay_keys": relay_keys(data), "cleanupPeriodDays": data.get("cleanupPeriodDays")}
        except (OSError, ValueError) as error:
            settings_info[path.name] = {"error": str(error)}
    report = {
        "tool": f"claude-env-cleanup {VERSION}", "platform": env.platform,
        "config_dir": str(env.config_dir), "config_dir_from_env": bool(env.config_dir_raw), "config_dir_exists": exists(env.config_dir),
        "global_config": {"active": str(cfg["active"]), "active_exists": cfg["active_exists"], "legacy_active": cfg["legacy_active"],
                          "variants": [str(p) for p in cfg["variants"]], "backups": len(cfg["backups"])},
        "projects_recorded": len(config_projects(env)),
        "mcp_servers": config_mcp_servers(env),
        "settings": settings_info,
        "shell_relay_vars": shell_relay_vars(env, environ),
        "repo_shared_files": repo_shared_files(env),
        "under_claude_code": under_claude(procs, environ, os.getpid()),
        "claude_processes": [{"pid": p.pid, "name": p.name, "path": p.path} for p in claude_processes(procs, os.getpid())],
        "groups": groups,
        "managed_settings": str(managed_settings_dir(env)) if exists(managed_settings_dir(env)) else None,
        "credentials": {"files": [i["path"] for i in groups["login"]]},
        "registry": [], "notes": [],
    }
    try:
        report["telemetry"] = settings_telemetry(env)
    except (OSError, ValueError) as error:
        report["telemetry"] = {"error": str(error)}
    if env.platform == "darwin":
        report["credentials"]["keychain"] = [s for s in keychain_services(env) if system.keychain_exists(s)]
        if system.launchd_loaded("com.anthropic.claude-daemon"):
            report["notes"].append("后台会话 launchd 服务已加载；深度清理前请运行 claude daemon uninstall")
        report["notes"].append("Claude Desktop 的加密钥匙「Claude Safe Storage」在钥匙串里，本工具不处理")
    elif env.platform == "windows":
        report["credentials"]["credential_manager"] = system.credman_targets()
        report["registry"] = [k for k in registry_keys() if system.reg_exists(k)]
        for package in MSIX_PACKAGES:
            if exists(env.localappdata / "Packages" / package):
                report["notes"].append(f"检测到商店版 Desktop（{package}）；卸载应用请用户执行 Get-AppxPackage *Claude* | Remove-AppxPackage")
    cli = shutil.which("claude")
    report["claude_on_path"] = cli
    if cli:
        folder = os.path.dirname(os.path.realpath(cli))
        if "node_modules" in cli.replace("\\", "/") or any(
                os.path.isdir(os.path.join(b, "node_modules", "@anthropic-ai", "claude-code"))
                for b in (folder, os.path.dirname(folder), os.path.join(os.path.dirname(folder), "lib"))):
            report["notes"].append(f"claude 来自 npm 全局安装（{cli}）；要卸载请用户执行 npm uninstall -g @anthropic-ai/claude-code")
    if exists(env.config_dir / "daemon") or exists(env.config_dir / "jobs"):
        report["notes"].append("配置目录里有后台会话数据（daemon/jobs）；清理前请运行 claude daemon stop")
    if cfg["legacy_active"]:
        report["notes"].append(f"旧版 {cfg['active']} 存在且优先生效；清理会一并处理它")
    if report["shell_relay_vars"]:
        report["notes"].append("shell/用户环境变量里还有中转站或密钥（见 shell_relay_vars）；本工具不改 shell 配置，需要用户自己删除")
    if report["repo_shared_files"]:
        report["notes"].append("项目里有提交到 Git 的 .mcp.json / .claude/settings.json / CLAUDE.md，本工具不动（与团队共享）")
    return report


def print_audit(report: dict) -> None:
    g = report["groups"]
    size = lambda items: human(sum(i["bytes"] for i in items))
    print(f"{report['tool']}  只读审计（{report['platform']}）\n")
    origin = "CLAUDE_CONFIG_DIR" if report["config_dir_from_env"] else "默认"
    print(f"- 配置目录（{origin}）：{report['config_dir']}  {'存在' if report['config_dir_exists'] else '不存在'}")
    gc = report["global_config"]
    print(f"- 生效的全局配置：{gc['active']}  {'存在' if gc['active_exists'] else '不存在'}"
          f"{'（旧版 .config.json 优先）' if gc['legacy_active'] else ''}；历史备份 {gc['backups']} 份；记录的项目 {report['projects_recorded']} 个")
    mcp = report["mcp_servers"]
    print(f"- MCP：用户级 {len(mcp.get('user', []))} 个（{', '.join(mcp.get('user', [])) or '无'}），项目级 {mcp.get('project_scoped', 0)} 个")
    for name, info in report["settings"].items():
        print(f"- {name} 里的中转站/密钥/代理键：{', '.join(info.get('relay_keys', [])) or info.get('error') or '无'}")
    creds = report["credentials"]
    if "keychain" in creds:
        print(f"- 钥匙串条目：{', '.join(creds['keychain']) or '无'}")
    if "credential_manager" in creds:
        print(f"- 凭据管理器条目：{', '.join(creds['credential_manager']) or '无'}；注册表残留：{', '.join(report['registry']) or '无'}")
    procs = report["claude_processes"]
    print(f"- Claude 相关进程：{len(procs)} 个" + ("：" + "、".join(f"{p['name']}({p['pid']})" for p in procs[:8]) if procs else ""))
    if report["under_claude_code"]:
        print("- 注意：当前脚本由 Claude Code 启动，只能审计，不能执行清理")
    telemetry = report.get("telemetry", {})
    off = [k for k, v in telemetry.items() if str(v).lower() in TRUTHY] if "error" not in telemetry else []
    print(f"- 遥测关闭键：{', '.join(off) or '未设置'}（清理前后逐值保持不变）")

    print("\n【基础清理】登录环境 + userID 轮换 + 会话与项目 + 记忆（+ 可再生缓存）")
    for category in BASIC_CATEGORIES:
        items = g[category]
        print(f"  {CATEGORIES[category]}：{len(items)} 项 / {size(items)}")
        for item in items:
            print(f"     - {item['path']}  ({human(item['bytes'])})  {item['reason']}")
    print("  另外原地修改：全局配置换新 userID/machineID、清除账号字段与项目记录；settings 删除中转站/密钥键")
    print("\n【深度清理】再加上配置目录里的其余全部内容 + 全局配置本身")
    for category in ("mcp", "custom", "settings", "state", "global", "other"):
        items = g[category]
        if items:
            print(f"  {CATEGORIES[category]}：{len(items)} 项 / {size(items)}：" + ", ".join(pathlib.Path(i["path"]).name for i in items))
    optional = {"registrations": len(g["registrations"]) + len(report["registry"]), "uninstall": len(g["uninstall"]), "repo-local": len(g["repo-local"])}
    print("  可选：" + "；".join(f"{CATEGORIES[c]} {n} 项" for c, n in optional.items()))
    print(f"\n【两种模式都会问】Claude Desktop：数据 {len(g['desktop'])} 项 / {size(g['desktop'])}，应用 {len(g['desktop-app'])} 项")
    if report["shell_relay_vars"]:
        print("\n需要你自己处理的环境变量：")
        for item in report["shell_relay_vars"]:
            print(f"  - {item}")
    if report["managed_settings"]:
        print(f"\n企业托管设置：{report['managed_settings']}（永不触碰）")
    for note in report["notes"]:
        print(f"* {note}")


# ---------------------------------------------------------------- 交互

def ask_yes(prompt: str, default: bool = False) -> bool:
    value = input(f"{prompt} {'[Y/n]' if default else '[y/N]'}：").strip().lower()
    return default if not value else value == "y"


def ask_choice(prompt: str, allowed: set[int], default: int = 0) -> int:
    value = input(prompt).strip() or str(default)
    if not value.isdigit() or int(value) not in allowed:
        raise ValueError("输入不在允许范围内，已停止且未修改")
    return int(value)


def collect_choices(report: dict, preset: str | None) -> dict:
    g = report["groups"]
    if preset:
        mode = preset
    else:
        mode = {1: "basic", 2: "deep"}[ask_choice(
            "\n选择模式：\n  1 基础清理：登录环境、userID 轮换、会话与项目、记忆、缓存日志\n"
            "  2 深度清理：在基础之上，把 MCP、插件、skills、hooks、设置、后台会话等配置目录全部内容和全局配置都挪走\n选择 [1]：",
            {1, 2}, default=1)]
    choices: dict = {"mode": mode, "desktop": 0}
    if g["desktop"] or g["desktop-app"]:
        choices["desktop"] = ask_choice(
            "Claude Desktop：0 保留；1 挪走登录态和全部桌面版数据（含 Code 会话列表、No folder 会话文件、Cowork、Desktop 的 MCP 配置）；"
            "2 再挪走应用本体。选择 [0]：", {0, 1, 2})
    if mode == "deep":
        if g["registrations"] or report["registry"]:
            choices["registrations"] = ask_yes("同时移除 claude-cli:// 深链和 Claude in Chrome 原生宿主注册？")
        if g["uninstall"]:
            choices["uninstall"] = ask_yes("同时挪走原生安装的 claude 程序与版本目录（相当于卸载）？")
        if g["repo-local"]:
            choices["repo_local"] = ask_yes(f"同时挪走 {len(g['repo-local'])} 个项目里的个人配置（.claude/settings.local.json、CLAUDE.local.md）？")
    return choices


def interactive(env: Env, system: System, preset: str | None = None) -> int:
    report = audit(env, system)
    print_audit(report)
    if report["under_claude_code"]:
        print("\n拒绝执行：当前脚本由 Claude Code 启动。请在普通终端里运行；这里只允许 --audit。", file=sys.stderr)
        return 2
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\n拒绝执行：需要在真实终端里完成确认（不接受管道输入）。", file=sys.stderr)
        return 2
    try:
        choices = collect_choices(report, preset)
        print("\n请你自己正常退出所有 Claude Code / Claude Desktop，并在终端运行一次 `claude daemon stop`（没用过后台会话可跳过）。本脚本不会结束进程。")
        input("完成后按 Enter 继续：")
        still = claude_processes(system.processes(), os.getpid())
        if still:
            print("仍检测到 Claude 进程，已取消且未写盘：" + "、".join(f"{p.name}({p.pid})" for p in still), file=sys.stderr)
            return 2
        plan = build_plan(env, system, choices)
        if plan_is_empty(plan):
            print("没有需要处理的内容，未做任何修改。")
            return 0
        print("\n" + "\n".join(describe_plan(plan, env)))
        if input("\n完全理解后输入 CONFIRM 开始；其他输入取消：").strip() != "CONFIRM":
            print("已取消，没有修改任何内容。")
            return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"停止（未写盘）：{error}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\n已取消，没有修改任何内容。")
        return 1
    try:
        batch_dir = execute(env, system, plan)
    except PartialFailure as failure:
        print(f"\n停止：{failure.cause}\n部分完成。批次目录：{failure.batch_dir}", file=sys.stderr)
        print(f"回滚命令：python \"{sys.argv[0]}\" --rollback \"{failure.batch_dir}\"", file=sys.stderr)
        return 2
    print(f"\n完成。批次目录：{batch_dir}（按类别存放，见其中的 README.txt）")
    print(f"- 挪走 {len(plan.moves)} 项；原地修改 {len(plan.edits)} 个文件；删除系统凭据 {len(plan.keychain) + len(plan.credman)} 项；注册表 {len(plan.registry)} 项")
    if plan.new_ids:
        print(f"- 新 userID：{plan.new_ids['userID'][:12]}…")
    print("- 遥测设置未变。下次启动 Claude Code 需要重新登录。")
    if report["shell_relay_vars"]:
        print("- 提醒：shell/用户环境变量里还有中转站或密钥，需要你自己删除（见上面的审计）。")
    print(f"- 需要还原时：python \"{sys.argv[0]}\" --rollback \"{batch_dir}\"")
    return 0


def interactive_rollback(env: Env, system: System, batch_dir: str, dry_run: bool) -> int:
    try:
        if not dry_run:
            procs = system.processes()
            if under_claude(procs, dict(os.environ), os.getpid()):
                print("拒绝执行：当前脚本由 Claude Code 启动。请在普通终端里回滚。", file=sys.stderr)
                return 2
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                print("拒绝执行：回滚需要在真实终端里确认。", file=sys.stderr)
                return 2
            running = claude_processes(procs, os.getpid())
            if running:
                print("请先退出所有 Claude 进程再回滚：" + "、".join(f"{p.name}({p.pid})" for p in running), file=sys.stderr)
                return 2
            rollback(env, system, pathlib.Path(batch_dir), dry_run=True)
            if input("\n输入 RESTORE 按上面的清单还原；其他输入取消：").strip() != "RESTORE":
                print("已取消。")
                return 1
        problems = rollback(env, system, pathlib.Path(batch_dir), dry_run=dry_run)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"回滚失败：{error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"* {problem}")
    print("dry-run 结束，未修改。" if dry_run else "回滚完成。")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # 中文 Windows 默认 GBK，被 agent 捕获输出时会乱码
    parser = argparse.ArgumentParser(description="审计并清理 Claude Code / Claude Desktop 本机状态（Windows、macOS）")
    parser.add_argument("--audit", action="store_true", help="只读审计（AI 可以运行）")
    parser.add_argument("--json", action="store_true", help="与 --audit 一起用，输出 JSON")
    parser.add_argument("--mode", choices=("basic", "deep"), help="预选模式（仍需在终端里输入 CONFIRM）")
    parser.add_argument("--list-batches", action="store_true", help="列出历史批次")
    parser.add_argument("--rollback", metavar="BATCH_DIR", help="按批次清单还原")
    parser.add_argument("--dry-run", action="store_true", help="与 --rollback 一起用，只显示将要还原的内容")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    platform = current_platform()
    if platform not in ("windows", "darwin"):
        print("目前只支持 Windows 和 macOS。", file=sys.stderr)
        return 2
    env, system = build_env(), System(platform)
    if args.audit:
        try:
            report = audit(env, system)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"审计失败：{error}", file=sys.stderr)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "", end="\n" if args.json else "")
        if not args.json:
            print_audit(report)
        return 0
    if args.list_batches:
        batches = list_batches(env)
        for path, manifest in batches:
            print(f"{path}  模式={manifest.get('mode')}  状态={manifest.get('status')}  挪走={len(manifest.get('moves', []))}  时间={manifest.get('created')}")
        if not batches:
            print(f"{env.batch_root} 下没有批次。")
        return 0
    if args.rollback:
        return interactive_rollback(env, system, args.rollback, args.dry_run)
    return interactive(env, system, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
