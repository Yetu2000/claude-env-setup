"""claude_browser_env 的单元测试：临时目录模拟浏览器数据，替身代替系统命令，不触碰真实设置、不联网。"""

import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import claude_browser_env as m  # noqa: E402


class FakeSystem(m.System):
    def __init__(self, env, curl_map=None, ps_out=None, reg=None):
        super().__init__(env)
        self.calls = []
        self.curl_map = curl_map or {}
        self.ps_out = ps_out
        self.reg = reg or {}

    def run(self, args, timeout=30, env=None):
        self.calls.append(("run", list(args)))
        if args[:2] == ["tzutil.exe", "/g"]:
            last = [c for c in self.calls if c[0] == "run" and c[1][:2] == ["tzutil.exe", "/s"]]
            return 0, (last[-1][1][2] if last else "China Standard Time"), ""
        return 0, "", ""

    def powershell(self, script, timeout=60):
        self.calls.append(("ps", script))
        if self.ps_out is not None and "Get-TimeZone" in script:
            return 0, self.ps_out, ""
        return 0, "", ""

    def reg_get(self, root, path, name):
        return self.reg.get((root, path, name))

    def reg_subkeys(self, root, path, wow64_32=False):
        return []

    def curl(self, url, proxy=None, timeout=10):
        key = (url.split("/")[2], proxy)
        if key in self.curl_map:
            return 0, json.dumps(self.curl_map[key]), ""
        return 7, "", "connect failed"


def make_cookie_db(path, hosts):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    for host in hosts:
        con.execute("INSERT INTO cookies VALUES (?, 'x', 'secret')", (host,))
    con.commit()
    con.close()


def make_history_db(path, urls):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE urls (url TEXT, last_visit_time INTEGER)")
    for url in urls:
        con.execute("INSERT INTO urls VALUES (?, ?)", (url, 13380000000000000))
    con.commit()
    con.close()


class TagTests(unittest.TestCase):
    def test_base_tag(self):
        self.assertEqual(m.base_tag("zh-Hans-CN"), "zh-CN")
        self.assertEqual(m.base_tag("en_us"), "en-US")
        self.assertTrue(m.same_tag("zh-Hans-CN", "zh-CN"))

    def test_target_languages_chinese_first(self):
        self.assertEqual(m.target_languages(["zh-Hans-CN"], "SG", False), ["zh-Hans-CN", "en-SG"])
        self.assertEqual(m.target_languages(["zh-Hans-CN", "en-US"], "US", False), ["zh-Hans-CN", "en-US"])
        self.assertEqual(m.target_languages(["zh-Hans-CN", "ja-JP"], "US", True), ["en-US", "zh-Hans-CN", "ja-JP"])

    def test_target_languages_generic_english(self):
        # 日本没有专属英语标签：已有任何英语即可
        self.assertEqual(m.target_languages(["zh-CN", "en-GB"], "JP", False), ["zh-CN", "en-GB"])
        self.assertEqual(m.target_languages([], "JP", False), ["zh-CN", "en-US"])

    def test_browser_languages(self):
        self.assertEqual(m.browser_accept_languages(["zh-Hans-CN", "en-SG"]), ["zh-CN", "zh", "en-SG", "en"])
        self.assertTrue(m.languages_match(["zh-CN", "zh", "en-US", "en"], ["zh-Hans-CN", "en-US"]))
        self.assertFalse(m.languages_match(["zh-CN", "zh"], ["zh-Hans-CN", "en-US"]))
        self.assertFalse(m.languages_match([], ["zh-CN", "en-US"]))
        self.assertTrue(m.languages_match(["en-US", "en"], ["en-US", "zh-CN"]))


class DefaultBrowserTests(unittest.TestCase):
    def test_progid(self):
        self.assertEqual(m.classify_progid("ChromeHTML"), "chrome")
        self.assertEqual(m.classify_progid("MSEdgeHTM"), "edge")
        self.assertEqual(m.classify_progid("FirefoxURL-308046B0AF4A39CB"), "firefox")
        self.assertEqual(m.classify_progid("IE.HTTPS"), "ie")
        self.assertEqual(m.classify_progid("Weird", r'"C:\x\brave.exe" %1'), "brave")
        self.assertEqual(m.classify_progid("Weird", "x.exe"), "unknown")

    def test_mac_plist(self):
        data = {"LSHandlers": [{"LSHandlerURLScheme": "mailto", "LSHandlerRoleAll": "com.apple.mail"},
                               {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.google.chrome"}]}
        self.assertEqual(m.mac_default_from_plist(data), "com.google.chrome")
        self.assertIsNone(m.mac_default_from_plist({}))


class ChromiumScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.env = m.build_env(environ={"LOCALAPPDATA": str(self.home / "Local"), "APPDATA": str(self.home / "Roaming")},
                               platform="windows", home=self.home)
        ud = self.home / "Local" / "Google" / "Chrome" / "User Data"
        (ud / "Default").mkdir(parents=True)
        (ud / "Profile 1").mkdir()
        (ud / "Local State").write_text(json.dumps({"intl": {"app_locale": "zh-CN"},
                                                    "profile": {"info_cache": {"Default": {"name": "个人"}, "Profile 1": {"name": "Claude"}}}}), encoding="utf-8")
        (ud / "Default" / "Preferences").write_text(json.dumps({"intl": {"accept_languages": "zh-CN,zh"}}), encoding="utf-8")
        (ud / "Profile 1" / "Preferences").write_text(json.dumps({
            "intl": {"selected_languages": "zh-CN,zh,en-US,en"},
            "extensions": {"settings": {m.WEBRTC_PROTECT_ID: {"state": 1, "preferences": {"webrtc.ip_handling_policy": "disable_non_proxied_udp"}}}},
        }), encoding="utf-8")
        make_cookie_db(ud / "Default" / "Network" / "Cookies", [".claude.ai", "claude.ai", ".anthropic.com", ".google.com", "evilclaude.ai"])
        make_history_db(ud / "Default" / "History", ["https://claude.ai/new", "https://example.com/claude.ai"])
        self.ud = ud

    def tearDown(self):
        self.tmp.cleanup()

    def test_profiles_and_traces(self):
        browsers = m.scan_browsers(self.env, FakeSystem(self.env))
        chrome = next(b for b in browsers if b["id"] == "chrome")
        self.assertEqual(chrome["ui_locale"], "zh-CN")
        default, claude = chrome["profiles"]
        self.assertEqual(default["name"], "个人")
        self.assertEqual(default["claude"]["cookies"], {"claude.ai": 2, "anthropic.com": 1})
        self.assertEqual(default["claude"]["visits"], 1)
        self.assertTrue(default["claude"]["used_claude"])
        self.assertEqual(default["webrtc"], "missing")
        self.assertEqual(claude["languages"], ["zh-CN", "zh", "en-US", "en"])
        self.assertEqual(claude["webrtc"], "protected")
        self.assertIs(claude["claude"]["used_claude"], None)  # 没有 Cookie 库：未知

    def test_policy_wins(self):
        reg = {("HKLM", r"SOFTWARE\Policies\Google\Chrome", "WebRtcIPHandling"): "disable_non_proxied_udp"}
        browsers = m.scan_browsers(self.env, FakeSystem(self.env, reg=reg))
        chrome = next(b for b in browsers if b["id"] == "chrome")
        self.assertEqual(chrome["profiles"][0]["webrtc"], "protected")

    def test_disabled_extension(self):
        prefs = self.ud / "Default" / "Preferences"
        prefs.write_text(json.dumps({"extensions": {"settings": {m.WEBRTC_PROTECT_ID: {"disable_reasons": [1]}}}}), encoding="utf-8")
        report = m.chromium_profile_report(self.ud / "Default", read_traces=False)
        self.assertEqual(m.webrtc_verdict_chromium({}, report)[0], "missing")
        self.assertIn("停用", m.webrtc_verdict_chromium({}, report)[1])


class FirefoxTests(unittest.TestCase):
    def test_prefs_parse_and_verdict(self):
        text = '''user_pref("intl.accept_languages", "zh-CN, en-US");
user_pref("media.peerconnection.ice.default_address_only", true);
user_pref("media.peerconnection.ice.no_host", true);
user_pref("browser.x", 3);'''
        prefs = m.parse_prefs_js(text)
        self.assertEqual(prefs["intl.accept_languages"], "zh-CN, en-US")
        self.assertEqual(prefs["browser.x"], 3)
        self.assertEqual(m.webrtc_verdict_firefox(prefs)[0], "protected")
        self.assertEqual(m.webrtc_verdict_firefox({})[0], "missing")
        self.assertEqual(m.webrtc_verdict_firefox({"media.peerconnection.enabled": False})[0], "protected")

    def test_profiles_ini(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "Profiles" / "abc.default").mkdir(parents=True)
            (root / "profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=Profiles/abc.default\nDefault=1\n", encoding="utf-8")
            profiles = m.firefox_profiles(root)
            self.assertEqual(profiles[0][0], "default")
            self.assertTrue(profiles[0][2])


class ProxyParseTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(m.parse_windows_proxy("127.0.0.1:7897"), "http://127.0.0.1:7897")
        self.assertEqual(m.parse_windows_proxy("http=127.0.0.1:7890;https=127.0.0.1:7891"), "http://127.0.0.1:7891")
        self.assertIsNone(m.parse_windows_proxy(""))

    def test_scutil(self):
        text = "<dictionary> {\n  HTTPSEnable : 1\n  HTTPSPort : 7897\n  HTTPSProxy : 127.0.0.1\n}"
        self.assertEqual(m.parse_scutil_proxy(text), "http://127.0.0.1:7897")
        self.assertIsNone(m.parse_scutil_proxy("HTTPSEnable : 0"))


WIN_STATE = json.dumps({
    "timezone": "China Standard Time", "timezone_display": "(UTC+08:00)", "culture": "zh-CN", "ui_culture": "zh-CN",
    "ui_override": "", "languages": {"tag": "zh-Hans-CN", "tips": "0804:{A}{B}"}, "geo_id": 45, "geo_name": "中国",
    "system_locale": "zh-CN",
})


class RecommendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        self.env = m.build_env(environ={"LOCALAPPDATA": str(self.home / "L"), "APPDATA": str(self.home / "R")}, platform="windows", home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def system(self, exit_info):
        return FakeSystem(self.env, curl_map={("ipinfo.io", None): exit_info}, ps_out=WIN_STATE)

    def test_state_single_language_object(self):
        state = m.system_state(self.env, self.system({}))
        self.assertEqual(state["languages"], ["zh-Hans-CN"])
        self.assertEqual(state["language_entries"][0]["tips"], ["0804:{A}{B}"])

    def test_singapore(self):
        sysm = self.system({"ip": "1.2.3.4", "country": "SG", "city": "Singapore", "timezone": "Asia/Singapore"})
        report = m.audit(self.env, sysm)
        rec = report["recommendation"]
        self.assertTrue(rec["ready"])
        self.assertEqual(rec["timezone_windows"], "Singapore Standard Time")
        kinds = {op["kind"]: op for op in rec["ops"]}
        self.assertEqual(kinds["home_location"]["to"], 215)
        self.assertEqual(kinds["languages"]["to"], ["zh-Hans-CN", "en-SG"])
        self.assertIn("中文", rec["language_advice"])

    def test_los_angeles(self):
        sysm = self.system({"ip": "1.2.3.4", "country": "US", "city": "Los Angeles", "timezone": "America/Los_Angeles"})
        rec = m.audit(self.env, sysm)["recommendation"]
        self.assertEqual(rec["timezone_windows"], "Pacific Standard Time")
        self.assertEqual(rec["browser_languages"], ["zh-CN", "zh", "en-US", "en"])

    def test_english_first_pins_display(self):
        sysm = self.system({"ip": "1.2.3.4", "country": "US", "timezone": "America/Los_Angeles"})
        rec = m.audit(self.env, sysm, overrides={"english_first": True})["recommendation"]
        kinds = [op["kind"] for op in rec["ops"]]
        self.assertIn("ui_override", kinds)

    def test_cn_exit_not_ready(self):
        sysm = self.system({"ip": "1.2.3.4", "country": "CN", "timezone": "Asia/Shanghai"})
        rec = m.audit(self.env, sysm)["recommendation"]
        self.assertFalse(rec["ready"])
        self.assertTrue(any("TUN" in note for note in rec["notes"]))

    def test_override_without_network(self):
        rec = m.audit(self.env, self.system({}), network=False, overrides={"country": "US", "timezone": "America/Los_Angeles"})["recommendation"]
        self.assertTrue(rec["ready"])

    def test_execute_and_rollback(self):
        sysm = self.system({"ip": "1.2.3.4", "country": "US", "timezone": "America/Los_Angeles"})
        rec = m.audit(self.env, sysm)["recommendation"]
        batch = m.execute(self.env, sysm, rec, log=lambda *_: None)
        manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "done")
        self.assertTrue(all(op["applied"] for op in manifest["ops"]))
        self.assertIn(("run", ["tzutil.exe", "/s", "Pacific Standard Time"]), sysm.calls)
        failures = m.rollback(self.env, sysm, batch, log=lambda *_: None)
        self.assertEqual(failures, [])
        self.assertIn(("run", ["tzutil.exe", "/s", "China Standard Time"]), sysm.calls)
        restore = [c[1] for c in sysm.calls if c[0] == "ps" and "InputMethodTips.Add('0804:{A}{B}')" in c[1]]
        self.assertTrue(restore)
        self.assertEqual(json.loads((batch / "manifest.json").read_text(encoding="utf-8"))["status"], "rolled_back")

    def test_rollback_rejects_unknown_kind(self):
        batch = self.home / "ClaudeBrowserEnv" / "x"
        m.write_json_atomic(batch / "manifest.json", {"platform": "windows", "ops": [{"kind": "rm", "applied": True, "from": "", "to": ""}]})
        failures = m.rollback(self.env, self.system({}), batch, log=lambda *_: None)
        self.assertTrue(failures)

    def test_tag_validation(self):
        with self.assertRaises(ValueError):
            m.ps_list(["en-US'; Remove-Item x"])


if __name__ == "__main__":
    unittest.main()
