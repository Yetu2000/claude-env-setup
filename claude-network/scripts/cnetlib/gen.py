"""从 templates/ 生成要执行的脚本。所有动态值先转成 shell / PowerShell 单引号字面量再填进模板。"""

import json
import os
import re

from . import platform_, util, verge

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
PLACEHOLDER = re.compile(r"@@[A-Z0-9_]+@@")
LOOPBACK = ("127.0.0.1", "localhost", "::1", "")


class GenError(RuntimeError):
    pass


def template(platform, name):
    path = os.path.join(TEMPLATE_DIR, "mac" if platform == "darwin" else "win", name)
    text = util.read_text(path)
    if text is None:
        raise GenError("缺少模板：" + path)
    return text


def fill(text, values):
    # 单遍替换：替换进来的值不会再被当作占位符解析
    keys = sorted(values, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape("@@" + key + "@@") for key in keys)) if keys else None
    if pattern:
        text = pattern.sub(lambda m: values[m.group(0)[2:-2]], text)
    left = PLACEHOLDER.findall(text)
    # 被嵌入的守护脚本里不会出现 @@，出现即生成错误
    if left:
        raise GenError("模板占位符未填：" + ", ".join(sorted(set(left))))
    return text


def context():
    """生成脚本所需的本机事实。"""
    state = verge.read_state()
    app = verge.app_path()
    if not app:
        raise GenError("找不到 Clash Verge 客户端程序")
    return {
        "platform": util.platform_name(),
        "user": platform_.current_user(),
        "uid": platform_.user_uid(),
        "home": util.home(),
        "verge_dir": state["dir"],
        "app": app,
        "port": int(state.get("mixed_port") or 7897),
    }


def _q(ctx, value):
    return util.sh_quote(value) if ctx["platform"] == "darwin" else util.ps_quote(value)


def common(ctx):
    if ctx["platform"] == "darwin":
        values = {
            "USER": util.sh_quote(ctx["user"]), "UID": str(int(ctx["uid"])), "HOME": util.sh_quote(ctx["home"]),
            "VERGE_DIR": util.sh_quote(ctx["verge_dir"]), "APP": util.sh_quote(ctx["app"]), "PORT": str(ctx["port"]),
            "GUARD_DIR": util.sh_quote(util.MAC_GUARD_DIR), "ANCHOR": util.sh_quote(util.MAC_PF_ANCHOR),
            "LABEL": util.sh_quote(util.MAC_LAUNCHD_LABEL),
        }
        return fill(template("darwin", "common.sh"), values)
    values = {
        "USER": util.ps_quote(ctx["user"]), "VERGE_DIR": util.ps_quote(ctx["verge_dir"]), "APP": util.ps_quote(ctx["app"]),
        "PORT": str(ctx["port"]), "GUARD_DIR": util.ps_quote(util.WIN_GUARD_DIR), "FW_GROUP": util.ps_quote(util.WIN_FW_GROUP),
        "TASK_NAME": util.ps_quote(util.WIN_TASK_NAME),
    }
    return fill(template("windows", "common.ps1"), values)


def _write_script(ctx, path, text):
    if ctx["platform"] == "darwin":
        util.write_text(path, text, private=True, executable=True)
    else:
        # PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI 解析中文，必须带 BOM
        util.write_text(path, text, newline="\r\n", bom=True)
    return path


def ext(ctx):
    return ".sh" if ctx["platform"] == "darwin" else ".ps1"


# ---------------- 节点名在脚本里的三种形态 ----------------

def node_values(ctx, plan):
    def url(name):
        return _q(ctx, util.url_path_escape(name)) if name else _q(ctx, "")

    def js(name):
        return _q(ctx, json.dumps(name, ensure_ascii=False)) if name else _q(ctx, "")

    exit_name = (plan.get("exit") or {}).get("name", "")
    jump_name = plan.get("jump_default") or ((plan.get("jump") or [{}])[0].get("name", ""))
    return {
        "MODE": _q(ctx, plan["mode"]),
        "OVERSEAS_URL": url(util.GROUP_OVERSEAS),
        "JUMPGROUP_URL": url(util.GROUP_JUMP),
        "EXIT_URL": url(exit_name),
        "EXIT_JSON": js(exit_name),
        "JUMP_URL": url(jump_name),
        "JUMP_JSON": js(jump_name),
        "RES_URL": url(util.RESIDENTIAL_NODE if plan["mode"] == "B" else ""),
        "RES_JSON": js(util.RESIDENTIAL_NODE if plan["mode"] == "B" else ""),
        "DRILL_JSON": js(util.DRILL_NODE),
        "NORMAL_JSON": js(exit_name if plan["mode"] == "A" else util.RESIDENTIAL_NODE),
    }


# ---------------- 各脚本 ----------------

def gen_switch(ctx, plan, plan_dir, backup_dir):
    values = node_values(ctx, plan)
    log = os.path.join(plan_dir, "switch.log")
    values.update({
        "COMMON": common(ctx), "LOG": _q(ctx, log), "BACKUP": _q(ctx, backup_dir),
        "STAGE": _q(ctx, os.path.join(plan_dir, "stage")), "RESULT": _q(ctx, os.path.join(plan_dir, "switch.result")),
    })
    if ctx["platform"] == "windows":
        values["SELF_TASK"] = util.ps_quote("claude-network-switch")
    path = os.path.join(plan_dir, "switch" + ext(ctx))
    return _write_script(ctx, path, fill(template(ctx["platform"], "switch" + ext(ctx)), values))


def _mac_network_restore(services, indent=""):
    lines = []
    q = util.sh_quote
    for svc in services:
        name = svc["name"]
        dns = svc.get("dns") or []
        lines.append("/usr/sbin/networksetup -setdnsservers " + q(name) + " " + (" ".join(q(d) for d in dns) if dns else "empty")
                     + " >/dev/null 2>&1 && log " + q("DNS 已还原：" + name) + " || log " + q("DNS 还原失败：" + name))
        mode = svc.get("ipv6")
        if mode == "automatic":
            lines.append("/usr/sbin/networksetup -setv6automatic " + q(name) + " >/dev/null 2>&1 || true")
        elif mode == "off":
            lines.append("/usr/sbin/networksetup -setv6off " + q(name) + " >/dev/null 2>&1 || true")
        elif mode == "linklocal":
            lines.append("/usr/sbin/networksetup -setv6LinkLocal " + q(name) + " >/dev/null 2>&1 || true")
        for kind, setter in (("web", "-setwebproxy"), ("secureweb", "-setsecurewebproxy"), ("socks", "-setsocksfirewallproxy")):
            proxy = svc.get(kind) or {}
            if proxy.get("enabled") and proxy.get("server") not in LOOPBACK and proxy.get("port"):
                lines.append("/usr/sbin/networksetup " + setter + " " + q(name) + " " + q(proxy["server"]) + " " + q(proxy["port"]) + " >/dev/null 2>&1 || true")
        auto = svc.get("autoproxy") or {}
        if auto.get("enabled") and auto.get("url") and "127.0.0.1" not in auto["url"]:
            lines.append("/usr/sbin/networksetup -setautoproxyurl " + q(name) + " " + q(auto["url"]) + " >/dev/null 2>&1 || true")
    return "\n".join(indent + line for line in lines) or (indent + ":")


def _win_network_restore(state, include_proxy=True, indent=""):
    q = util.ps_quote
    lines = []
    physical = set(state.get("physical") or [])
    for item in state.get("ipv6") or []:
        if item.get("enabled") and (not physical or item.get("name") in physical):
            lines.append("Enable-NetAdapterBinding -Name " + q(item["name"]) + " -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue")
    pol = "'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient'"
    dnscache = "'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Dnscache\\Parameters'"
    for key, path, name in (("smart_name_resolution", pol, "DisableSmartNameResolution"), ("parallel_a_aaaa", dnscache, "DisableParallelAandAAAA")):
        value = state.get(key)
        if value is None:
            lines.append("Remove-ItemProperty -Path " + path + " -Name " + name + " -ErrorAction SilentlyContinue")
        else:
            lines.append("Set-ItemProperty -Path " + path + " -Name " + name + " -Value " + str(int(value)) + " -Type DWord -ErrorAction SilentlyContinue")
    lines.append("Write-Log 'IPv6 绑定与 DNS 策略已按备份还原'")
    if include_proxy:
        proxy = state.get("proxy") or {}
        server = proxy.get("server") or ""
        key = "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'"
        if proxy.get("enable") == 1 and server and not server.startswith(("127.0.0.1", "localhost")):
            lines.append("Set-ItemProperty -Path " + key + " -Name ProxyServer -Value " + q(server) + " -ErrorAction SilentlyContinue")
            lines.append("Set-ItemProperty -Path " + key + " -Name ProxyEnable -Value 1 -Type DWord -ErrorAction SilentlyContinue")
            lines.append("Write-Log '系统代理已按备份还原（非本机代理）'")
        auto = proxy.get("autoconfig") or ""
        if auto and "127.0.0.1" not in auto:
            lines.append("Set-ItemProperty -Path " + key + " -Name AutoConfigURL -Value " + q(auto) + " -ErrorAction SilentlyContinue")
    return "\n".join(indent + line for line in lines)


def gen_restore(ctx, backup_dir, network_state, stamp):
    if ctx["platform"] == "darwin":
        values = {"COMMON": common(ctx), "BACKUP": util.sh_quote(backup_dir), "STAMP": stamp,
                  "NETWORK_RESTORE": _mac_network_restore(network_state.get("services", []))}
        path = os.path.join(backup_dir, "restore.sh")
        return _write_script(ctx, path, fill(template("darwin", "restore.sh"), values))
    values = {"COMMON": common(ctx), "BACKUP": util.ps_quote(backup_dir), "STAMP": stamp,
              "NETWORK_RESTORE": _win_network_restore(network_state)}
    path = os.path.join(backup_dir, "restore.ps1")
    _write_script(ctx, path, fill(template("windows", "restore.ps1"), values))
    util.write_text(os.path.join(backup_dir, "restore.cmd"),
                    "@echo off\r\nchcp 65001 >nul\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%~dp0restore.ps1\"\r\npause\r\n",
                    newline="\r\n")
    return path


def gen_harden(ctx, work_dir, backup_state):
    log = os.path.join(work_dir, "harden.log")
    network = backup_state.get("network") or {}
    if ctx["platform"] == "darwin":
        apply_lines, revert_services = [], []
        for svc in network.get("services", []):
            if not svc.get("enabled"):
                continue
            q = util.sh_quote(svc["name"])
            apply_lines.append("/usr/sbin/networksetup -setdnsservers " + q + " " + " ".join(util.CN_DNS)
                               + " >/dev/null 2>&1 && log " + util.sh_quote("DNS → 223.5.5.5 119.29.29.29：" + svc["name"])
                               + " || { log " + util.sh_quote("改 DNS 失败：" + svc["name"]) + "; errors=$((errors + 1)); }")
            if svc.get("ipv6") in ("automatic", "linklocal"):
                apply_lines.append("/usr/sbin/networksetup -setv6off " + q + " >/dev/null 2>&1 && log " + util.sh_quote("IPv6 → 关：" + svc["name"])
                                   + " || { log " + util.sh_quote("关 IPv6 失败：" + svc["name"]) + "; errors=$((errors + 1)); }")
            elif svc.get("ipv6") == "manual":
                apply_lines.append("log " + util.sh_quote("IPv6 是手动配置，未改动：" + svc["name"]))
            revert_services.append(svc)
        values = {"COMMON": common(ctx), "LOG": util.sh_quote(log), "SERVICES_APPLY": "\n".join(apply_lines) or ":",
                  "SERVICES_REVERT": _mac_network_restore(revert_services, indent="  ")}
        path = os.path.join(work_dir, "harden.sh")
        return _write_script(ctx, path, fill(template("darwin", "harden.sh"), values))
    physical = set(network.get("physical") or [])
    adapters = [i["name"] for i in network.get("ipv6") or [] if i.get("enabled") and i.get("name") in physical]
    values = {"COMMON": common(ctx), "LOG": util.ps_quote(log),
              "ADAPTERS": ", ".join(util.ps_quote(a) for a in adapters) or "",
              "REVERT": _win_network_restore(network, include_proxy=False, indent="  ")}
    path = os.path.join(work_dir, "harden.ps1")
    return _write_script(ctx, path, fill(template("windows", "harden.ps1"), values))


def gen_killswitch(ctx, work_dir):
    log = os.path.join(work_dir, "killswitch-install.log")
    if ctx["platform"] == "darwin":
        values = {"COMMON": common(ctx), "LOG": util.sh_quote(log),
                  "GUARD_APPLY": template("darwin", "guard_apply.sh").rstrip("\n"),
                  "GUARD_WATCH": template("darwin", "guard_watch.sh").rstrip("\n"),
                  "GUARD_OFF": template("darwin", "guard_off.sh").rstrip("\n"),
                  "GUARD_PLIST": template("darwin", "guard.plist").rstrip("\n")}
        path = os.path.join(work_dir, "killswitch.sh")
        return _write_script(ctx, path, fill(template("darwin", "killswitch.sh"), values))
    for name in ("guard_apply.ps1", "guard_off.ps1"):
        if re.search(r"^'@", template("windows", name), re.M):
            raise GenError(name + " 含有 here-string 结束标记")
    values = {"COMMON": common(ctx), "LOG": util.ps_quote(log),
              "GUARD_APPLY": template("windows", "guard_apply.ps1").rstrip("\n"),
              "GUARD_OFF": template("windows", "guard_off.ps1").rstrip("\n")}
    path = os.path.join(work_dir, "killswitch.ps1")
    return _write_script(ctx, path, fill(template("windows", "killswitch.ps1"), values))


def gen_drills(ctx, plan, work_dir):
    values = node_values(ctx, plan)
    values = {k: values[k] for k in ("OVERSEAS_URL", "DRILL_JSON", "NORMAL_JSON")}
    values.update({"COMMON": common(ctx), "LOG": _q(ctx, os.path.join(work_dir, "drills.log")),
                   "RESULT": _q(ctx, os.path.join(work_dir, "drills.result"))})
    path = os.path.join(work_dir, "drills" + ext(ctx))
    return _write_script(ctx, path, fill(template(ctx["platform"], "drills" + ext(ctx)), values))


def admin_command(ctx, script_path):
    """交给用户在终端里跑的那一条命令。"""
    if ctx["platform"] == "darwin":
        return "sudo sh " + util.sh_quote(script_path)
    return "powershell -NoProfile -ExecutionPolicy Bypass -File " + '"' + script_path + '"'
