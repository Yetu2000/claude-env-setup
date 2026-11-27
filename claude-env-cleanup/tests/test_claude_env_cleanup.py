"""用临时目录模拟 Windows / macOS 的用户主目录，不读写任何真实用户数据，也不调用系统命令。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_env_cleanup.py"
SPEC = importlib.util.spec_from_file_location("claude_env_cleanup", SCRIPT)
cec = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cec
SPEC.loader.exec_module(cec)

QUIET = {"log": lambda *_: None}


class FakeSystem:
    def __init__(self, platform: str):
        self.platform = platform
        self.procs: list = []
        self.keychain: set[str] = set()
        self.credman: list[str] = []
        self.registry: dict[str, str] = {}
        self.calls: list = []

    def processes(self):
        return list(self.procs)

    def keychain_exists(self, service):
        return service in self.keychain

    def keychain_delete(self, service):
        self.calls.append(("keychain_delete", service))
        self.keychain.discard(service)

    def credman_targets(self):
        return list(self.credman)

    def credman_delete(self, target):
        self.calls.append(("credman_delete", target))
        self.credman.remove(target)

    def reg_exists(self, key):
        return key in self.registry

    def reg_export(self, key, file):
        file.write_text(self.registry[key], encoding="utf-8")

    def reg_delete(self, key):
        self.calls.append(("reg_delete", key))
        del self.registry[key]

    def reg_import(self, file):
        self.calls.append(("reg_import", file.read_text(encoding="utf-8")))

    def launchd_loaded(self, label):
        return False


def write(path: pathlib.Path, content: str = "x") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


OLD_ID = "a" * 64
SETTINGS = {
    "env": {"DISABLE_TELEMETRY": "1", "ANTHROPIC_BASE_URL": "https://relay.example", "ANTHROPIC_AUTH_TOKEN": "sk-relay",
            "HTTPS_PROXY": "http://127.0.0.1:7890", "MY_OWN_VAR": "keep"},
    "apiKeyHelper": "/usr/local/bin/key.sh", "model": "opus", "hooks": {"PreToolUse": []},
}


class Base(unittest.TestCase):
    def root(self) -> pathlib.Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return pathlib.Path(temp.name).resolve()

    def windows_env(self, root, **extra):
        environ = {"APPDATA": str(root / "AppData" / "Roaming"), "LOCALAPPDATA": str(root / "AppData" / "Local"),
                   "TEMP": str(root / "AppData" / "Local" / "Temp"), **extra}
        return cec.build_env(environ, "windows", root)

    def darwin_env(self, root, **extra):
        env = cec.build_env({"TMPDIR": str(root / "tmp"), **extra}, "darwin", root)
        env.applications = root / "Applications"
        return env

    def seed(self, env, project: pathlib.Path | None = None) -> None:
        c = env.config_dir
        write(c / "settings.json", json.dumps(SETTINGS))
        for rel in ("CLAUDE.md", "skills/demo/SKILL.md", "plugins/p.json", "hooks/h.sh", "agents/a.md", "jobs/j.json",
                    "daemon/roster.json", "cache/a.bin", "debug/latest", "shell-snapshots/s.sh", "usage.jsonl",
                    "projects/E--work/1.jsonl", "projects/E--work/memory/MEMORY.md", "history.jsonl", "file-history/x/1",
                    "todos/t.json", "memory/m.md", "agent-memory/a.md", ".credentials.json", "weird-new-thing/x"):
            write(c / rel)
        write(env.home / ".claude" / ".device-keys.json", "{}")
        projects = {str(project): {"allowedTools": [], "mcpServers": {"p": {}}}} if project else {"E:/work": {}}
        config = {"userID": OLD_ID, "machineID": "b" * 64, "oauthAccount": {"emailAddress": "u@example.com"},
                  "primaryApiKey": "sk-ant-xxx", "cachedGrowthBookFeatures": {"x": 1}, "mcpServers": {"m": {}},
                  "projects": projects, "theme": "dark"}
        write(env.global_dir / ".claude.json", json.dumps(config))
        write(c / "backups" / ".claude.json.backup.1700000000000", json.dumps(config))

    def cats(self, env):
        out: dict[str, set[str]] = {}
        for target in cec.all_targets(env):
            out.setdefault(target.category, set()).add(target.path.name)
        return out


class InventoryTests(Base):
    def test_config_dir_classification(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        cats = self.cats(env)
        self.assertTrue({"cache", "debug", "shell-snapshots", "usage.jsonl"} <= cats["cache"])
        self.assertTrue({".credentials.json", ".device-keys.json"} <= cats["login"])
        self.assertTrue({"projects", "history.jsonl", "file-history", "todos"} <= cats["sessions"])
        self.assertEqual(cats["memory"], {"memory", "agent-memory"})
        self.assertIn("plugins", cats["mcp"])
        self.assertTrue({"skills", "hooks", "agents", "CLAUDE.md"} <= cats["custom"])
        self.assertIn("settings.json", cats["settings"])
        self.assertTrue({"jobs", "daemon"} <= cats["state"])
        self.assertIn("backups", cats["global"])
        self.assertIn("weird-new-thing", cats["other"])

    def test_windows_external(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        write(env.localappdata / "claude-cli-nodejs" / "Cache" / "x")
        write(env.appdata / "Claude" / "logs" / "main.log")
        write(env.appdata / "Claude" / "Local State", "{}")
        write(env.localappdata / "AnthropicClaude" / "claude.exe")
        write(env.appdata / "Claude Code" / "ChromeNativeHost" / "host.json")
        write(root / ".local" / "share" / "claude" / "versions" / "1")
        cats = self.cats(env)
        self.assertIn("claude-cli-nodejs", cats["cache"])
        self.assertIn("logs", cats["cache"])
        self.assertIn("Claude", cats["desktop"])
        self.assertIn("AnthropicClaude", cats["desktop-app"])
        self.assertIn("ChromeNativeHost", cats["registrations"])
        self.assertIn("claude", cats["uninstall"])

    def test_darwin_external(self):
        root = self.root()
        env = self.darwin_env(root)
        self.seed(env)
        lib = root / "Library"
        write(lib / "Caches" / "claude-cli-nodejs" / "x")
        write(lib / "Application Support" / "Claude" / "config.json")
        write(lib / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / f"{cec.NATIVE_HOST}.json")
        write(root / "Applications" / "Claude.app" / "Contents" / "Info.plist")
        write(root / "Applications" / "Claude Code URL Handler.app" / "Contents" / "Info.plist")
        cats = self.cats(env)
        self.assertIn("claude-cli-nodejs", cats["cache"])
        self.assertIn("Claude", cats["desktop"])
        self.assertIn("Claude.app", cats["desktop-app"])
        self.assertTrue({"Claude Code URL Handler.app", f"{cec.NATIVE_HOST}.json"} <= cats["registrations"])

    def test_claude_config_dir(self):
        root = self.root()
        custom = root / "alt"
        env = self.darwin_env(root, CLAUDE_CONFIG_DIR=str(custom))
        self.seed(env)
        self.assertEqual(env.global_dir, custom)
        self.assertTrue((custom / ".claude.json").is_file())
        cats = self.cats(env)
        self.assertIn(".claude.json", cats["global"])
        self.assertIn(".device-keys.json", cats["login"])  # 主目录下的那份
        digest = hashlib.sha256(str(custom).encode()).hexdigest()[:8]
        self.assertIn(f"Claude Code-credentials-{digest}", cec.keychain_services(env))
        self.assertNotIn("Claude Code-credentials", cec.keychain_services(env))

    def test_legacy_config_json(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        write(env.config_dir / ".config.json", json.dumps({"userID": "c" * 64}))
        info = cec.global_config_files(env)
        self.assertTrue(info["legacy_active"])
        self.assertIn(env.config_dir / ".config.json", cec.identity_files(env))

    def test_relay_keys(self):
        self.assertEqual(set(cec.relay_keys(SETTINGS)),
                         {"env.ANTHROPIC_BASE_URL", "env.ANTHROPIC_AUTH_TOKEN", "env.HTTPS_PROXY", "apiKeyHelper"})
        scrubbed = cec.scrub_settings(SETTINGS)
        self.assertEqual(scrubbed["env"], {"DISABLE_TELEMETRY": "1", "MY_OWN_VAR": "keep"})
        self.assertEqual(scrubbed["hooks"], SETTINGS["hooks"])
        self.assertNotIn("apiKeyHelper", scrubbed)


class BasicModeTests(Base):
    def test_basic_execute_and_rollback(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        system = FakeSystem("windows")
        system.credman = ["Claude Code-credentials"]
        before_config = (root / ".claude.json").read_text(encoding="utf-8")
        plan = cec.build_plan(env, system, {"mode": "basic"})
        moved = {t.path.name for t in plan.moves}
        self.assertTrue({"projects", "history.jsonl", "memory", "agent-memory", ".credentials.json", ".device-keys.json", "cache"} <= moved)
        self.assertFalse({"skills", "plugins", "settings.json", "jobs", "backups", "weird-new-thing"} & moved)

        batch = cec.execute(env, system, plan, **QUIET)
        c = env.config_dir
        # 分类存放
        self.assertTrue((batch / "moved" / "03-会话与项目" / "claude-config" / "projects" / "E--work" / "1.jsonl").is_file())
        self.assertTrue((batch / "moved" / "04-记忆" / "claude-config" / "memory" / "m.md").is_file())
        self.assertTrue((batch / "moved" / "02-登录凭据" / "claude-config" / ".credentials.json").is_file())
        self.assertTrue((batch / "README.txt").is_file())
        # userID 轮换，并同步到 backups
        active = json.loads((root / ".claude.json").read_text(encoding="utf-8"))
        backup = json.loads((c / "backups" / ".claude.json.backup.1700000000000").read_text(encoding="utf-8"))
        self.assertNotEqual(active["userID"], OLD_ID)
        self.assertEqual(active["userID"], backup["userID"])
        for gone in ("oauthAccount", "primaryApiKey", "projects", "cachedGrowthBookFeatures"):
            self.assertNotIn(gone, active)
            self.assertNotIn(gone, backup)
        self.assertEqual(active["mcpServers"], {"m": {}})  # MCP 属于深度清理
        self.assertEqual(active["theme"], "dark")
        # 中转站被清，其余设置和遥测键保留
        settings = json.loads((c / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["env"], {"DISABLE_TELEMETRY": "1", "MY_OWN_VAR": "keep"})
        self.assertEqual(settings["model"], "opus")
        for kept in ("skills", "plugins", "hooks", "jobs", "weird-new-thing"):
            self.assertTrue((c / kept).exists(), kept)
        self.assertIn(("credman_delete", "Claude Code-credentials"), system.calls)

        problems = cec.rollback(env, system, batch, **QUIET)
        self.assertEqual((root / ".claude.json").read_text(encoding="utf-8"), before_config)
        self.assertTrue((c / "projects" / "E--work" / "1.jsonl").is_file())
        self.assertEqual(json.loads((c / "settings.json").read_text(encoding="utf-8")), SETTINGS)
        self.assertTrue(any("重新登录" in p for p in problems))

    def test_basic_darwin_keychain(self):
        root = self.root()
        env = self.darwin_env(root)
        self.seed(env)
        system = FakeSystem("darwin")
        system.keychain = {"Claude Code-credentials", "Claude Code-device-keys", "Unrelated"}
        plan = cec.build_plan(env, system, {"mode": "basic"})
        cec.execute(env, system, plan, **QUIET)
        self.assertEqual(system.keychain, {"Unrelated"})

    def test_partial_failure(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        system = FakeSystem("windows")
        plan = cec.build_plan(env, system, {"mode": "basic"})
        real_move, count = shutil.move, {"n": 0}

        def flaky(src, dst):
            count["n"] += 1
            if count["n"] == 2:
                raise PermissionError("文件被占用")
            return real_move(src, dst)

        first = plan.moves[0].path
        with mock.patch.object(cec.shutil, "move", side_effect=flaky):
            with self.assertRaises(cec.PartialFailure) as caught:
                cec.execute(env, system, plan, **QUIET)
        manifest = json.loads((caught.exception.batch_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "partial")
        self.assertFalse(first.exists())
        cec.rollback(env, system, caught.exception.batch_dir, **QUIET)
        self.assertTrue(first.exists())
        self.assertEqual(json.loads((root / ".claude.json").read_text(encoding="utf-8"))["userID"], OLD_ID)


class DeepModeTests(Base):
    def test_deep_moves_everything_and_keeps_telemetry(self):
        root = self.root()
        env = self.windows_env(root)
        project = root / "code" / "app"
        write(project / "src" / "main.py", "print(1)")
        write(project / ".claude" / "settings.local.json", json.dumps({"env": {"ANTHROPIC_BASE_URL": "x"}}))
        write(project / ".mcp.json", "{}")
        self.seed(env, project=project)
        system = FakeSystem("windows")
        system.registry = {r"HKCU\Software\Classes\claude-cli": "REG"}
        plan = cec.build_plan(env, system, {"mode": "deep", "registrations": True, "repo_local": True})
        batch = cec.execute(env, system, plan, **QUIET)
        c = env.config_dir
        self.assertEqual(sorted(os.listdir(c)), ["settings.json"])
        self.assertEqual(json.loads((c / "settings.json").read_text(encoding="utf-8")), {"env": {"DISABLE_TELEMETRY": "1"}})
        self.assertFalse((root / ".claude.json").exists())
        self.assertTrue((batch / "moved" / "09-全局配置" / "home" / ".claude.json").is_file())
        self.assertTrue((batch / "moved" / "05-MCP与插件" / "claude-config" / "plugins").is_dir())
        self.assertTrue((batch / "moved" / "06-自定义配置" / "claude-config" / "skills").is_dir())
        self.assertTrue((batch / "moved" / "99-其他" / "claude-config" / "weird-new-thing").is_dir())
        self.assertFalse((project / ".claude" / "settings.local.json").exists())
        self.assertTrue((project / "src" / "main.py").exists())  # 源码不动
        self.assertTrue((project / ".mcp.json").exists())  # 共享文件不动
        self.assertIn(("reg_delete", r"HKCU\Software\Classes\claude-cli"), system.calls)

        cec.rollback(env, system, batch, **QUIET)
        self.assertEqual(json.loads((c / "settings.json").read_text(encoding="utf-8")), SETTINGS)
        self.assertTrue((root / ".claude.json").is_file())
        self.assertTrue((project / ".claude" / "settings.local.json").is_file())
        self.assertIn(("reg_import", "REG"), system.calls)

    def test_deep_without_telemetry_creates_nothing(self):
        root = self.root()
        env = self.darwin_env(root)
        self.seed(env)
        write(env.config_dir / "settings.json", json.dumps({"model": "opus"}))
        plan = cec.build_plan(env, FakeSystem("darwin"), {"mode": "deep"})
        self.assertIsNone(plan.recreate_settings)
        cec.execute(env, FakeSystem("darwin"), plan, **QUIET)
        self.assertEqual(os.listdir(env.config_dir), [])


class SafetyTests(Base):
    def test_check_target_rejections(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        c = env.config_dir
        protected = [c / "skills", c / "backups"]
        for path in (c / "skills", c / "skills" / "demo", c, root, env.appdata, root.parent / "outside", env.batch_root / "x",
                     c / "backups" / "x"):
            with self.assertRaises(RuntimeError, msg=str(path)):
                cec.check_target(path, env, protected)
        cec.check_target(c / "cache", env, protected)

    def test_symlink_skipped(self):
        root = self.root()
        env = self.darwin_env(root)
        self.seed(env)
        real = write(root / "real" / "x")
        try:
            os.symlink(real.parent, env.config_dir / "image-cache", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("当前系统不允许创建符号链接")
        plan = cec.build_plan(env, FakeSystem("darwin"), {"mode": "basic"})
        self.assertNotIn("image-cache", {t.path.name for t in plan.moves})
        with self.assertRaises(RuntimeError):
            cec.check_target(env.config_dir / "image-cache", env, [])

    def test_rollback_rejects_tampered(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        system = FakeSystem("windows")
        batch = cec.execute(env, system, cec.build_plan(env, system, {"mode": "basic"}), **QUIET)
        manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
        manifest["moves"][0]["dst"] = str(root / "outside")
        (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            cec.rollback(env, system, batch, **QUIET)
        with self.assertRaises(RuntimeError):
            cec.rollback(env, system, root / "not-a-batch", **QUIET)

    def test_invalid_config_refused(self):
        root = self.root()
        env = self.windows_env(root)
        write(root / ".claude.json", "[1, 2]")
        with self.assertRaises(ValueError):
            cec.build_plan(env, FakeSystem("windows"), {"mode": "basic"})

    def test_under_claude_detection(self):
        me = os.getpid()
        procs = [cec.Proc(me, 50, "python.exe", "", ""), cec.Proc(50, 40, "pwsh.exe", "", ""),
                 cec.Proc(40, 1, "claude.exe", r"C:\Users\u\.local\bin\claude.exe", "")]
        self.assertTrue(cec.under_claude(procs, {}, me))
        self.assertTrue(cec.under_claude([], {"CLAUDECODE": "1"}, me))
        procs[2] = cec.Proc(40, 1, "WindowsTerminal.exe", "", "")
        self.assertFalse(cec.under_claude(procs, {}, me))
        node = cec.Proc(9, 1, "node.exe", "", r"node C:\npm\node_modules\@anthropic-ai\claude-code\cli.js")
        self.assertEqual(cec.claude_processes([node], me), [node])

    def test_interactive_refuses(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        with mock.patch.dict(os.environ, {}), mock.patch.object(sys.stdin, "isatty", return_value=False), mock.patch("builtins.print"):
            os.environ.pop("CLAUDECODE", None)
            self.assertEqual(cec.interactive(env, FakeSystem("windows")), 2)
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), mock.patch("builtins.print"):
            self.assertEqual(cec.interactive(env, FakeSystem("windows")), 2)
        self.assertFalse(env.batch_root.exists())

    def test_audit_json(self):
        root = self.root()
        env = self.windows_env(root)
        self.seed(env)
        report = cec.audit(env, FakeSystem("windows"), {"ANTHROPIC_BASE_URL": "x"})
        json.dumps(report, ensure_ascii=False)
        self.assertIn("env.ANTHROPIC_BASE_URL", report["settings"]["settings.json"]["relay_keys"])
        self.assertEqual(report["mcp_servers"]["user"], ["m"])
        self.assertTrue(any("ANTHROPIC_BASE_URL" in v for v in report["shell_relay_vars"]))
        self.assertNotIn("sk-relay", json.dumps(report))  # 不输出密钥值


if __name__ == "__main__":
    unittest.main()
