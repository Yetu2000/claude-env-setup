#!/usr/bin/env python3
"""claude-network 命令行（Python 3.8+，只用标准库；Windows 与 macOS 通用）。

只读：     doctor / nodes / verify / status / precheck
准备文件： plan / backup / emergency / harden / killswitch / drills（只生成脚本，不改系统）
会断网：   apply（后台跑 switch 脚本，自带失败自动还原）
要管理员的脚本一律只生成、打印命令，由用户本人在终端里运行。
"""

import argparse
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cnetlib import VERSION  # noqa: E402
from cnetlib import controller as ctl  # noqa: E402
from cnetlib import gen, nodes, platform_, render, util, verge  # noqa: E402
from cnetlib import verify as verify_mod  # noqa: E402

OTHER_CLIENT_DIRS = {
    "darwin": {
        "Mihomo Party": "~/Library/Application Support/mihomo-party",
        "ClashX Meta": "~/.config/clash.meta",
        "Surge": "~/Library/Application Support/com.nssurge.surge-mac",
        "sing-box (SFM)": "~/Library/Group Containers/group.io.nekohasekai.sfavt",
    },
    "windows": {
        "Mihomo Party": "%APPDATA%/mihomo-party",
        "Clash for Windows": "~/.config/clash",
        "v2rayN": "%LOCALAPPDATA%/v2rayN",
    },
}


def out(data, as_json):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))


# ====================== doctor ======================

def cmd_doctor(args):
    report = {"version": VERSION, "time": util.now_iso(), "platform": util.platform_name(), "user": platform_.current_user(),
              "python": sys.version.split()[0], "checks": []}

    def add(level, key, msg, **extra):
        item = {"level": level, "key": key, "msg": msg}
        item.update(extra)
        report["checks"].append(item)

    if util.platform_name() not in ("windows", "darwin"):
        add("stop", "platform", "只支持 Windows 与 macOS")
        return finish_doctor(report, args)

    state = verge.read_state()
    report["verge_dir"] = state["dir"]
    app = verge.app_path()
    if not state["exists"] or not app:
        add("stop", "client", "没有找到 Clash Verge Rev（配置目录或程序不存在）。本 skill 目前只自动化 Clash Verge Rev；"
            "请先安装官方版本 https://github.com/clash-verge-rev/clash-verge-rev/releases 并导入订阅、至少启动一次")
    else:
        version = verge.version()
        add("ok", "client", "Clash Verge Rev " + str(version) + "：" + app, version=version, running=verge.is_running())
        if version and not re.match(r"^2\.", str(version)):
            add("stop", "client_version", "只支持 Clash Verge Rev 2.x，当前 " + str(version))
    for name, path in OTHER_CLIENT_DIRS.get(util.platform_name(), {}).items():
        if os.path.isdir(os.path.expandvars(os.path.expanduser(path))):
            add("info", "other_client_dir", "发现 " + name + " 的配置目录（本 skill 不改它；只有在它运行时才会冲突）")

    others = [p for p in platform_.other_proxy_processes()]
    if others:
        add("ask", "other_proxy", "机器上还有别的代理/VPN 程序在运行：" + "、".join(others) + "。需要先问用户怎么处理（一般要退出并关掉它的开机自启）")

    network = platform_.network_state()
    report["network"] = network
    if platform_.tailscale_dns_takeover(network):
        add("ask", "tailscale_dns", "Tailscale 接管了系统 DNS（100.100.100.100），查询会绕过代理。需要先问用户：在 Tailscale 里关掉 Use Tailscale DNS，还是退出 Tailscale")

    if state["exists"]:
        svc_ok, svc_msg = verge.service_installed()
        add("ok" if svc_ok else "ask", "service", ("服务模式：" if svc_ok else "服务模式未安装/未运行：") + svc_msg
            + ("" if svc_ok else "。开启虚拟网卡模式必须先装服务模式：请用户在客户端「设置 → 系统设置 → 服务模式」点安装并输入系统密码，装好后回复我"))
        for key, want, msg in (("enable_dns_settings", False, "设置里的「DNS 覆写」开着（会盖掉本 skill 写的 DNS）：plan 会把它关掉"),
                               ("ipv6", False, "客户端的「IPv6」开关开着：plan 会把它关掉"),
                               ("enable_system_proxy", False, "系统代理开着：本方案靠虚拟网卡接管，plan 会把系统代理关掉"),
                               ("enable_tun_mode", True, "虚拟网卡模式（TUN）没开：plan 会打开"),
                               ("enable_auto_launch", True, "开机自启没开：plan 会打开")):
            value = state.get(key)
            if value is not want:
                add("info", key, msg + "（当前 " + str(value) + "）")
        if state.get("profile_dns_override_on"):
            add("info", "profile_dns", "有订阅单独开了 DNS 覆写：plan 会全部关掉")
        current = state.get("current_profile") or {}
        add("ok" if current else "stop", "profile", "当前订阅：" + str(current.get("name") or state.get("profile_current")) + "（" + str(current.get("type")) + "）"
            if current else "客户端里没有选中任何订阅：先导入并选中订阅")
        for finding in verge.extension_findings(state):
            add("ask", "extension", finding["msg"] + "：" + finding["file"])
        registered, how = verge.auto_launch_registered()
        add("info", "auto_launch_registered", "开机启动项：" + how)

    try:
        c = ctl.Controller()
        core = c.version()
        cfg = c.configs()
        add("ok", "controller", "核心 " + str(core.get("version")) + "，控制器 " + c.kind + "：" + c.address + "；模式 " + str(cfg.get("mode"))
            + "，tun.enable=" + str((cfg.get("tun") or {}).get("enable")))
    except Exception as error:  # noqa: BLE001
        add("ask", "controller", "连不上核心控制器（" + str(error) + "）：客户端没开或内核没起来")

    if util.IS_MAC:
        if not platform_.mac_pf_has_apple_anchor():
            add("stop", "pf", '/etc/pf.conf 里没有 anchor "com.apple/*"：断网开关的锚点规则不会生效，先问用户（本 skill 不改 pf.conf）')
        tun = [line for line in util.run(["/sbin/ifconfig"])[1].split("\n") if "inet 198.18." in line]
        add("ok" if tun else "info", "tun", "虚拟网卡：" + ("已就绪" if tun else "未就绪"))
        if os.path.exists("/Library/LaunchDaemons/" + util.MAC_LAUNCHD_LABEL + ".plist"):
            add("info", "killswitch", "断网开关已安装（" + util.MAC_GUARD_DIR + "）：之后每次改配置、重启客户端都会短暂断一下")
    else:
        tun = network.get("tun") or []
        add("ok" if tun else "info", "tun", "虚拟网卡：" + (", ".join(t["alias"] + " " + t["ip"] for t in tun) if tun else "未就绪"))
        if network.get("guard_task") or network.get("guard_rules"):
            add("info", "killswitch", "断网开关已安装（" + util.WIN_GUARD_DIR + "）：之后每次改配置、重启客户端都会短暂断一下")
        off = [p["name"] for p in network.get("firewall", []) if not p.get("enabled")]
        if off:
            add("ask", "firewall", "Windows 防火墙在 " + ",".join(off) + " 上是关闭的（可能被第三方安全软件接管）：断网开关将无法安装")

    code, body, _ = util.curl("https://claude.ai/cdn-cgi/trace", timeout=12)
    if code == 0:
        ip = re.search(r"^ip=(\S+)", body, re.M)
        loc = re.search(r"^loc=(\S+)", body, re.M)
        add("ok", "claude", "现在 claude.ai 能连上：出口 " + (ip.group(1) if ip else "?") + "（" + (loc.group(1) if loc else "?") + "）")
    else:
        add("info", "claude", "现在经虚拟网卡连不上 claude.ai（curl exit " + str(code) + "）")
    code, body, _ = util.curl("http://edns.ip-api.com/json", timeout=12, follow=True)
    if code == 0:
        try:
            geo = json.loads(body).get("dns", {})
            leak = "China" in str(geo.get("geo"))
            add("ask" if leak else "ok", "dns_now", "当前 DNS 出口：" + str(geo.get("ip")) + "（" + str(geo.get("geo")) + "）" + ("——在中国大陆，存在 DNS 泄漏" if leak else ""))
        except ValueError:
            pass
    backups = sorted(glob.glob(os.path.join(util.backup_root(), "*", "BACKUP_OK")))
    report["backups"] = [os.path.dirname(p) for p in backups]
    if backups:
        add("info", "backups", "已有备份 " + str(len(backups)) + " 份；最早：" + os.path.dirname(backups[0]))
    return finish_doctor(report, args)


def finish_doctor(report, args):
    if args.json:
        out(report, True)
    else:
        icon = {"ok": "✓", "info": "·", "ask": "?", "stop": "✗"}
        for item in report["checks"]:
            print(icon.get(item["level"], "-") + " " + item["msg"])
        print("\n图例：✓ 正常  · 说明  ? 需要先问用户  ✗ 必须先解决")
    return 2 if any(i["level"] == "stop" for i in report["checks"]) else 0


# ====================== nodes ======================

def cmd_nodes(args):
    c = ctl.Controller()
    found, skipped = nodes.collect(c)
    if not args.no_test:
        nodes.test_delays(c, found, timeout_ms=args.timeout)
    if args.json:
        out({"nodes": found, "skipped": skipped}, True)
    else:
        print(nodes.format_table(found, skipped))
        print("\n说明：香港 / 澳门 / 大陆节点 Claude 不提供服务，只能在 B 方案里当跳板；地区靠节点名推断，最终以验收第 4 项实测为准。")
    return 0


# ====================== plan ======================

def _find_node(found, name):
    for node in found:
        if node["name"] == name:
            return node
    return None


def _core_check(plan, found, state):
    cores = verge.core_paths()
    if not cores:
        return None, "找不到客户端自带内核，跳过 mihomo -t"
    core = cores[0]
    for path in cores:
        if (state.get("clash_core") or "verge-mihomo") in os.path.basename(path) and "alpha" not in os.path.basename(path):
            core = path
    temp = tempfile.mkdtemp(prefix="cnet-check-")
    try:
        for name in ("geosite.dat", "geoip.dat", "Country.mmdb", "ASN.mmdb", "GeoSite.dat", "GeoIP.dat"):
            src = os.path.join(state["dir"], name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(temp, name))
        sub, providers = [], {}
        for i, node in enumerate(found):
            placeholder = {"name": node["name"], "type": "socks5", "server": "198.51.100." + str(i % 250 + 1), "port": 1080}
            if node.get("provider"):
                providers.setdefault(node["provider"], {"type": "inline", "payload": []})["payload"].append(placeholder)
            else:
                sub.append(placeholder)
        config = render.simulate(plan, sub, providers=providers)
        path = os.path.join(temp, "check.yaml")
        util.write_json(path, config)
        code, stdout, stderr = util.run([core, "-t", "-d", temp, "-f", path], timeout=120)
        text = (stdout + stderr).strip()
        ok = code == 0 and "test is successful" in text
        return ok, ("mihomo -t 通过（" + os.path.basename(core) + "）" if ok else "mihomo -t 未通过：" + text[-600:])
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def cmd_plan(args):
    state = verge.read_state()
    if not state["exists"]:
        util.eprint("没有找到 Clash Verge 配置目录")
        return 2
    c = ctl.Controller()
    found, _ = nodes.collect(c)
    plan = {"mode": args.mode, "platform": util.platform_name(), "generated": util.now_iso(), "version": VERSION}
    if args.mode == "A":
        node = _find_node(found, args.exit or "")
        if not node:
            util.eprint("找不到节点：" + str(args.exit) + "（先运行 nodes 看准确名字）")
            return 2
        if node["jump_only"] and not args.force_region:
            util.eprint("节点 " + node["name"] + " 推断地区为 " + node["region"] + "：香港、澳门、大陆不能当海外出口")
            return 2
        plan["exit"] = {"name": node["name"], "provider": node.get("provider"), "region": node.get("region")}
    else:
        jump = _find_node(found, args.jump or "")
        if not jump:
            util.eprint("找不到跳板节点：" + str(args.jump))
            return 2
        if jump.get("dialer_proxy"):
            util.eprint("节点 " + jump["name"] + " 本身已经是链式节点（dialer-proxy），不能当跳板")
            return 2
        members = [jump]
        if not args.only_jump:
            delays = {}
            if args.keep_reachable:
                nodes.test_delays(c, found)
                delays = {n["name"]: n.get("delay") for n in found}
            for node in found:
                # 已经带 dialer-proxy 的链式节点（例如旧的住宅出口）不放进跳板组，避免套娃
                if node.get("dialer_proxy"):
                    continue
                if node["name"] != jump["name"] and (not args.keep_reachable or delays.get(node["name"])):
                    members.append(node)
        plan["jump"] = [{"name": n["name"], "provider": n.get("provider")} for n in members]
        plan["jump_default"] = jump["name"]
        raw = "{}"
        if args.residential_stdin:
            data = sys.stdin.buffer.read()
            try:
                raw = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raw = data.decode("gbk", errors="replace")
        try:
            residential = json.loads(raw or "{}")
        except ValueError:
            util.eprint("住宅代理信息不是合法 JSON（标准输入：{\"server\":..,\"port\":..,\"username\":..,\"password\":..}）")
            return 2
        plan["residential"] = {k: residential.get(k) for k in ("server", "port", "username", "password")}
    try:
        script_text = render.render_script(plan)
    except render.PlanError as error:
        util.eprint("计划不成立：" + str(error))
        return 2

    plan_dir = os.path.join(util.work_root(), "plans", util.now_stamp())
    suffix = 1
    while os.path.exists(plan_dir):
        suffix += 1
        plan_dir = os.path.join(util.work_root(), "plans", util.now_stamp() + "-" + str(suffix))
    util.ensure_private_dir(plan_dir)
    stage = util.ensure_private_dir(os.path.join(plan_dir, "stage"))
    os.makedirs(os.path.join(stage, "profiles"), exist_ok=True)
    paths = verge.files(state["dir"])
    old_verge = util.read_text(paths["verge"], "") or ""
    old_config = util.read_text(paths["config"], "") or ""
    new_verge = verge.patch_verge_yaml(old_verge)
    new_config = verge.patch_config_yaml(old_config, util.platform_name())
    util.write_text(os.path.join(stage, "verge.yaml"), new_verge, private=True)
    util.write_text(os.path.join(stage, "config.yaml"), new_config, private=True)
    util.write_text(os.path.join(stage, "profiles", "Script.js"), script_text, private=True)

    public_plan = json.loads(json.dumps(plan))
    if public_plan.get("residential"):
        if public_plan["residential"].get("password"):
            public_plan["residential"]["password"] = "***（只在 stage/profiles/Script.js 里，0600）"
    util.write_json(os.path.join(plan_dir, "plan.json"), public_plan, private=True)

    check_ok, check_msg = (None, "已跳过 mihomo -t（--no-core-check）") if args.no_core_check else _core_check(plan, found, state)
    diff = []
    for label, old, new in (("verge.yaml", old_verge, new_verge), ("config.yaml", old_config, new_config)):
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), label + "（现在）", label + "（改后）", lineterm="", n=0):
            if not line.startswith("@@"):
                diff.append(line)
    summary = {
        "plan_dir": plan_dir,
        "mode": plan["mode"],
        "exit": plan.get("exit", {}).get("name") if plan["mode"] == "A" else util.RESIDENTIAL_NODE + "（经跳板 " + plan["jump_default"] + "）",
        "jump_group": [j["name"] for j in plan.get("jump", [])],
        "rules": len(render.build_rules(plan)),
        "core_check": check_msg,
        "core_check_ok": check_ok,
        "changes": diff,
        "files_to_replace": [paths["verge"], paths["config"], paths["script"]],
    }
    util.write_json(os.path.join(plan_dir, "summary.json"), summary, private=True)
    if args.json:
        out(summary, True)
    else:
        print("计划目录：" + plan_dir)
        print("方案 " + plan["mode"] + "；海外出口 → " + summary["exit"])
        if plan["mode"] == "B":
            print("跳板组：" + "、".join(summary["jump_group"]))
        print("规则 " + str(summary["rules"]) + " 条（Claude → 局域网 → B站/微信 → 海外分类 → 国内名单 → 国内IP → 兜底海外出口）")
        print("内核校验：" + check_msg)
        print("将替换的文件（订阅文件不动）：\n  " + "\n  ".join(summary["files_to_replace"]))
        print("客户端开关的改动：")
        for line in diff:
            print("  " + line)
    return 0 if check_ok is not False else 3


# ====================== backup ======================

def cmd_backup(args):
    state = verge.read_state()
    if not state["exists"]:
        util.eprint("没有找到 Clash Verge 配置目录")
        return 2
    stamp = util.now_stamp()
    backup_dir = os.path.join(util.backup_root(), stamp)
    counter = 1
    while os.path.exists(backup_dir):
        counter += 1
        stamp = util.now_stamp() + "-" + str(counter)
        backup_dir = os.path.join(util.backup_root(), stamp)
    util.ensure_private_dir(util.backup_root())
    util.ensure_private_dir(backup_dir)
    target = os.path.join(backup_dir, "verge")
    errors = []

    def copy(src, dst):
        try:
            shutil.copy2(src, dst)
        except OSError as error:
            errors.append(os.path.relpath(src, state["dir"]) + "：" + str(error))
    shutil.copytree(state["dir"], target, copy_function=copy, ignore=shutil.ignore_patterns("*.lock"), dirs_exist_ok=True)
    critical = [e for e in errors if not e.startswith(("logs", "update_cache", "cache.db"))]
    manifest = {}
    for root, _, names in os.walk(state["dir"]):
        for name in names:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, state["dir"])
            dst = os.path.join(target, rel)
            if rel.startswith("logs") or name.endswith(".lock"):
                continue
            if not os.path.exists(dst):
                critical.append(rel + "：未复制")
                continue
            try:
                same = util.sha256_file(src) == util.sha256_file(dst)
            except OSError:
                same = False
            if not same and not rel.startswith(("cache.db", "update_cache")) and rel != "window_state.json":
                critical.append(rel + "：校验不一致（复制期间被修改？）")
            manifest[rel] = util.sha256_file(dst) if os.path.exists(dst) else None
    for must in ("verge.yaml", "config.yaml", "profiles.yaml"):
        if not os.path.exists(os.path.join(target, must)):
            critical.append(must + "：缺失")
    network = platform_.network_state()
    backup_state = {
        "time": util.now_iso(), "platform": util.platform_name(), "user": platform_.current_user(),
        "verge_dir": state["dir"], "app": verge.app_path(), "verge_version": verge.version(),
        "verge_state": {k: v for k, v in state.items() if k not in ("profile_items", "current_profile")},
        "network": network, "copy_errors": errors,
    }
    util.write_json(os.path.join(backup_dir, "state.json"), backup_state, private=True)
    util.write_json(os.path.join(backup_dir, "manifest.json"), manifest, private=True)
    ctx = gen.context()
    restore_path = gen.gen_restore(ctx, backup_dir, network if util.IS_WINDOWS else network, stamp)
    if critical:
        print("备份未完成（不会标记 BACKUP_OK，后续步骤会拒绝修改）：")
        for line in critical:
            print("  " + line)
        return 3
    util.write_text(os.path.join(backup_dir, "BACKUP_OK"), util.now_iso() + "\n")
    result = {"backup_dir": backup_dir, "restore": restore_path, "restore_command": gen.admin_command(ctx, restore_path),
              "files": len(manifest), "noncritical_errors": errors}
    if args.json:
        out(result, True)
    else:
        print("备份完成：" + backup_dir + "（" + str(len(manifest)) + " 个文件，已逐个校验）")
        print("一键还原：" + result["restore_command"])
        if util.IS_WINDOWS:
            print("  或右键 " + os.path.join(backup_dir, "restore.cmd") + " →「以管理员身份运行」")
    return 0


def _earliest_backup():
    oks = sorted(glob.glob(os.path.join(util.backup_root(), "*", "BACKUP_OK")))
    return os.path.dirname(oks[0]) if oks else None


def _latest_backup():
    oks = sorted(glob.glob(os.path.join(util.backup_root(), "*", "BACKUP_OK")))
    return os.path.dirname(oks[-1]) if oks else None


# ====================== emergency ======================

def emergency_text(backup_dir):
    network = platform_.network_state()
    if util.IS_MAC:
        services = [s["name"] for s in network.get("services", []) if s.get("enabled")]
        restore = os.path.join(backup_dir, "restore.sh")
        lines = [
            "断网急救（macOS）—— 照抄就能敲。先拍照留存。",
            "",
            "① 先打开代理客户端 Clash Verge（启动台 / 应用程序里点开），等一分钟，看网页能不能打开。",
            "",
            "② 还不通：打开「终端」App（启动台搜索“终端”），依次输入下面两条，每条回车后输入开机密码（输入时不显示）：",
            "   解除断网开关：",
            "   sudo sh \"" + util.MAC_GUARD_DIR + "/off.sh\"",
            "   一键还原到改动之前（多个备份时用的是最早那个）：",
            "   sudo sh " + util.sh_quote(restore),
            "",
            "③ 上面两个文件不存在（提示 No such file）时的兜底：",
            "   sudo launchctl bootout system/" + util.MAC_LAUNCHD_LABEL,
            "   sudo pfctl -a " + util.MAC_PF_ANCHOR + " -F all",
            "",
            "④ 关掉系统代理（客户端被强行退出时，系统代理可能还指着它的端口）：",
        ]
        for name in services:
            q = util.sh_quote(name)
            lines.append("   sudo networksetup -setwebproxystate " + q + " off; sudo networksetup -setsecurewebproxystate " + q
                         + " off; sudo networksetup -setsocksfirewallproxystate " + q + " off")
        lines.append("   如果 DNS 也不通，再把 DNS 改回自动：" + "; ".join("sudo networksetup -setdnsservers " + util.sh_quote(n) + " empty" for n in services[:2]))
    else:
        restore = os.path.join(backup_dir, "restore.cmd")
        lines = [
            "断网急救（Windows）—— 照抄就能做。先拍照留存。",
            "",
            "① 先打开代理客户端 Clash Verge（开始菜单里点开），等一分钟，看网页能不能打开。",
            "",
            "② 还不通：在文件管理器里找到下面两个文件，分别右键 →「以管理员身份运行」：",
            "   解除断网开关：" + os.path.join(util.WIN_GUARD_DIR, "off.cmd"),
            "   一键还原到改动之前（多个备份时用的是最早那个）：" + restore,
            "",
            "③ 上面两个文件不存在时的兜底：开始菜单搜索 PowerShell → 右键「以管理员身份运行」，依次输入：",
            "   Unregister-ScheduledTask -TaskName '" + util.WIN_TASK_NAME + "' -Confirm:$false",
            "   Get-NetFirewallRule -Group '" + util.WIN_FW_GROUP + "' | Remove-NetFirewallRule",
            "   Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction NotConfigured",
            "",
            "④ 关掉系统代理（在同一个 PowerShell 窗口里输入）：",
            "   Set-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' -Name ProxyEnable -Value 0",
        ]
        physical = set(network.get("physical") or [])
        ipv6 = [i["name"] for i in network.get("ipv6", []) if i.get("enabled") and (not physical or i["name"] in physical)]
        if ipv6:
            lines.append("   如果改过 IPv6：" + "; ".join("Enable-NetAdapterBinding -Name '" + n + "' -ComponentID ms_tcpip6" for n in ipv6))
    lines += [
        "",
        "⑤ 网通了以后，回到原来那个 AI 对话，说「网断过，先查现状再继续」。不要新开对话从头来。",
        "",
        "（生成时间 " + util.now_iso() + "；备份目录 " + backup_dir + "）",
    ]
    return "\n".join(lines) + "\n"


def cmd_emergency(args):
    backup_dir = args.backup or _earliest_backup()
    if not backup_dir:
        util.eprint("还没有完成的备份：先运行 backup")
        return 2
    text = emergency_text(backup_dir)
    path = os.path.join(util.desktop_dir(), util.EMERGENCY_FILE_NAME)
    util.write_text(path, text, newline="\r\n" if util.IS_WINDOWS else "\n", bom=util.IS_WINDOWS)
    util.write_text(os.path.join(util.work_root(), util.EMERGENCY_FILE_NAME), text)
    print("已写到桌面：" + path + "\n")
    print(text)
    return 0


# ====================== apply（后台切换） ======================

def _launch_detached(ctx, script):
    if ctx["platform"] == "darwin":
        with open(os.devnull, "rb") as devnull_in, open(os.devnull, "wb") as devnull_out:
            subprocess.Popen(["/usr/bin/nohup", "/bin/sh", script], stdin=devnull_in, stdout=devnull_out,
                             stderr=devnull_out, start_new_session=True, close_fds=True)
        return "nohup（独立会话）"
    task = "claude-network-switch"
    ps = (
        "$u = [Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + "
        + util.ps_quote(script) + " + '\"');"
        "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited;"
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20);"
        "Register-ScheduledTask -TaskName " + util.ps_quote(task) + " -Action $a -Principal $p -Settings $s -Force | Out-Null;"
        "Start-ScheduledTask -TaskName " + util.ps_quote(task) + "; 'ok'"
    )
    code, stdout, stderr = util.powershell(ps)
    if code != 0 or "ok" not in stdout:
        raise RuntimeError("创建后台计划任务失败：" + (stderr or stdout)[:300])
    return "一次性计划任务 " + task + "（在你的桌面会话里运行，结束后自删）"


def cmd_apply(args):
    plan_dir = args.plan
    plan = util.read_json(os.path.join(plan_dir, "plan.json"))
    if not plan:
        util.eprint("读不到 " + plan_dir + "/plan.json")
        return 2
    summary = util.read_json(os.path.join(plan_dir, "summary.json"), {})
    if summary.get("core_check_ok") is False:
        util.eprint("这个计划没通过 mihomo -t，拒绝执行")
        return 2
    backup_dir = args.backup or _latest_backup()
    if not backup_dir or not os.path.exists(os.path.join(backup_dir, "BACKUP_OK")):
        util.eprint("没有完成的备份：先运行 backup。备份没做完，不改任何东西")
        return 2
    if not os.path.exists(os.path.join(util.desktop_dir(), util.EMERGENCY_FILE_NAME)) and not args.no_emergency_check:
        util.eprint("桌面上还没有「断网急救.txt」：先运行 emergency，并把全文发给用户拍照")
        return 2
    ctx = gen.context()
    script = gen.gen_switch(ctx, plan, plan_dir, backup_dir)
    problems = precheck_paths([script])
    if problems:
        util.eprint("切换脚本预检没通过，没有启动：\n  " + "\n  ".join(problems))
        return 3
    for name in ("switch.result", "switch.log"):
        try:
            os.remove(os.path.join(plan_dir, name))
        except OSError:
            pass
    how = _launch_detached(ctx, script)
    print("已在后台启动切换脚本：" + script)
    print("运行方式：" + how)
    print("日志：" + os.path.join(plan_dir, "switch.log"))
    print("结果文件：" + os.path.join(plan_dir, "switch.result") + "（RUNNING / OK / ROLLED_BACK / ABORTED）")
    print("接下来网络会断开几十秒到两分钟；稍后用 status --plan 读结果，不要凭记忆汇报。")
    return 0


def cmd_status(args):
    plan_dir = args.plan
    result = util.read_text(os.path.join(plan_dir, "switch.result"), "（还没有结果文件）")
    log = util.read_text(os.path.join(plan_dir, "switch.log"), "") or ""
    print("结果：" + result.strip())
    print("日志末尾：")
    for line in log.strip().splitlines()[-args.lines:]:
        print("  " + line)
    return 0 if result.startswith("OK") else 1


# ====================== 需要管理员的脚本：只生成 ======================

def _print_admin(ctx, path, what, then):
    errors = precheck_paths([path])
    print(what + "：" + path)
    print("预检：" + ("通过" if not errors else "；".join(errors)))
    print("请用户在" + ("「终端」App" if ctx["platform"] == "darwin" else "「以管理员身份运行」的 PowerShell") + "里执行：")
    print("  " + gen.admin_command(ctx, path))
    print(then)
    return 0 if not errors else 3


def cmd_harden(args):
    backup_dir = args.backup or _earliest_backup()
    if not backup_dir:
        util.eprint("没有完成的备份")
        return 2
    state = util.read_json(os.path.join(backup_dir, "state.json"), {})
    ctx = gen.context()
    util.ensure_private_dir(util.work_root())
    path = gen.gen_harden(ctx, util.work_root(), state)
    return _print_admin(ctx, path, "DNS / IPv6 加固脚本", "跑完让用户回复「好了」，再用 verify 第 5 项读结果；日志 " + os.path.join(util.work_root(), "harden.log"))


def cmd_killswitch(args):
    ctx = gen.context()
    util.ensure_private_dir(util.work_root())
    path = gen.gen_killswitch(ctx, util.work_root())
    return _print_admin(ctx, path, "断网开关安装脚本", "跑完让用户回复「好了」，再用 verify --items 7,8 读结果")


def cmd_drills(args):
    plan = util.read_json(os.path.join(args.plan, "plan.json"))
    if not plan:
        util.eprint("读不到 plan.json")
        return 2
    ctx = gen.context()
    path = gen.gen_drills(ctx, plan, util.work_root())
    return _print_admin(ctx, path, "故障演练脚本（9~11）", "跑完让用户回复「好了」，结果在 " + os.path.join(util.work_root(), "drills.result"))


# ====================== verify / precheck ======================

def cmd_verify(args):
    items = [int(x) for x in args.items.split(",")] if args.items else None
    report = verify_mod.run(items)
    path = os.path.join(util.work_root(), "verify-" + util.now_stamp() + ".json")
    util.ensure_private_dir(util.work_root())
    util.write_json(path, report)
    if args.json:
        out(report, True)
    else:
        print(verify_mod.format_report(report))
        print("报告：" + path)
    return 0 if all(i["status"] == "pass" for i in report.get("items", [])) and not report.get("error") else 1


MAC_CMD_RE = re.compile(r"(?<![\w/])(/(?:usr/)?s?bin/[A-Za-z0-9_.+-]+)")


def precheck_paths(paths):
    errors = []
    for path in paths:
        text = util.read_text(path, "") or ""
        if gen.PLACEHOLDER.search(text):
            errors.append(path + "：有未填的占位符")
        if path.endswith(".sh"):
            shell = "/bin/sh" if os.path.exists("/bin/sh") else shutil.which("sh")
            if shell and not util.IS_WINDOWS:
                code, _, err = util.run([shell, "-n", path])
                if code != 0:
                    errors.append(path + "：sh -n 语法错误 " + err.strip()[:200])
            if util.IS_MAC:
                missing = sorted(set(p for p in MAC_CMD_RE.findall(text) if not os.path.exists(p)))
                if missing:
                    errors.append(path + "：命令不存在 " + ", ".join(missing))
                for plist in re.findall(r"<\?xml[\s\S]*?</plist>", text):
                    temp = tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False, encoding="utf-8")
                    temp.write(plist)
                    temp.close()
                    code, _, err = util.run(["/usr/bin/plutil", "-lint", temp.name])
                    os.unlink(temp.name)
                    if code != 0:
                        errors.append(path + "：plist 校验失败 " + err.strip()[:200])
        elif path.endswith(".ps1") and util.IS_WINDOWS:
            ps = ("$e=$null;$t=$null;[void][Management.Automation.Language.Parser]::ParseFile(" + util.ps_quote(path)
                  + ",[ref]$t,[ref]$e);if($e.Count){$e|%{'L'+$_.Extent.StartLineNumber+': '+$_.Message}}else{'PARSE_OK'}")
            code, stdout, _ = util.powershell(ps)
            if "PARSE_OK" not in stdout:
                errors.append(path + "：PowerShell 语法错误 " + stdout.strip()[:300])
            # 被内嵌的守护脚本也要单独解析
            for block in re.findall(r"@'\r?\n([\s\S]*?)\r?\n'@", text):
                temp = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig")
                temp.write(block)
                temp.close()
                code, stdout, _ = util.powershell(ps.replace(util.ps_quote(path), util.ps_quote(temp.name)))
                os.unlink(temp.name)
                if "PARSE_OK" not in stdout:
                    errors.append(path + "：内嵌脚本语法错误 " + stdout.strip()[:300])
    return errors


def cmd_precheck(args):
    errors = precheck_paths(args.files)
    if errors:
        for line in errors:
            print("✗ " + line)
        return 3
    print("预检通过：" + "、".join(args.files))
    return 0


# ====================== main ======================

def main(argv=None):
    util.configure_stdout()
    parser = argparse.ArgumentParser(prog="cnet.py", description="claude-network：国内直连、其余走固定海外出口、断了也不漏")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="只读体检")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("nodes", help="列出真节点并测延迟（只读）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-test", action="store_true")
    p.add_argument("--timeout", type=int, default=5000)
    p.set_defaults(func=cmd_nodes)

    p = sub.add_parser("plan", help="生成新配置到计划目录（不改客户端）")
    p.add_argument("--mode", choices=["A", "B"], required=True)
    p.add_argument("--exit", help="A 方案：海外出口节点名")
    p.add_argument("--jump", help="B 方案：默认跳板节点名")
    p.add_argument("--only-jump", action="store_true", help="B 方案：跳板组只放这一个节点")
    p.add_argument("--keep-reachable", action="store_true", help="B 方案：跳板组只保留测速能通的节点")
    p.add_argument("--residential-stdin", action="store_true", help="B 方案：从标准输入读住宅代理 JSON")
    p.add_argument("--force-region", action="store_true", help="节点名推断地区有误时强制使用（验收第 4 项仍会实测）")
    p.add_argument("--no-core-check", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("backup", help="完整备份客户端配置目录并生成一键还原脚本")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("emergency", help="在桌面写「断网急救.txt」并打印全文")
    p.add_argument("--backup")
    p.set_defaults(func=cmd_emergency)

    p = sub.add_parser("apply", help="后台运行切换脚本（会短暂断网；失败自动还原）")
    p.add_argument("--plan", required=True)
    p.add_argument("--backup")
    p.add_argument("--no-emergency-check", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("status", help="读切换脚本的结果与日志")
    p.add_argument("--plan", required=True)
    p.add_argument("--lines", type=int, default=25)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("harden", help="生成 DNS / IPv6 加固脚本（需管理员，由用户运行）")
    p.add_argument("--backup")
    p.set_defaults(func=cmd_harden)

    p = sub.add_parser("killswitch", help="生成断网开关安装脚本（需管理员，由用户运行）")
    p.set_defaults(func=cmd_killswitch)

    p = sub.add_parser("drills", help="生成故障演练 9~11 脚本（需管理员，由用户运行）")
    p.add_argument("--plan", required=True)
    p.set_defaults(func=cmd_drills)

    p = sub.add_parser("verify", help="验收 1~8 项（只发请求）")
    p.add_argument("--items", help="例如 1,2,3")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("precheck", help="脚本预检：语法、占位符、命令路径")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_precheck)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ctl.ControllerError as error:
        util.eprint("控制器错误：" + str(error))
        return 4
    except gen.GenError as error:
        util.eprint("生成脚本失败：" + str(error))
        return 5


if __name__ == "__main__":
    sys.exit(main())
