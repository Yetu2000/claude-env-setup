"""验收 1~8 项（只发请求、不改设置），并读取演练 9~11 的结果。

全部以当前（普通）用户身份运行；每条 curl 清掉代理变量并加 --noproxy '*'，测的是虚拟网卡而不是代理端口。
同时录下代理核心的连接日志（/logs 流 + /connections 采样），用它证明每个请求走了哪条规则、哪个出口。
结论只有三种：pass（有直接证据）、fail（明确不满足）、unknown（证据不足，不能当通过）。
"""

import ipaddress
import json
import os
import random
import re
import socket
import string
import time

from . import controller as ctl
from . import platform_, render, util, verge, yamlmini

CHINA_WORDS = ("中国", "China", "CN")


def _item(no, title):
    return {"no": no, "title": title, "status": "unknown", "evidence": [], "problems": []}


def _set(item, ok, problem=None):
    if ok is True and not item["problems"]:
        item["status"] = "pass"
    elif ok is False or item["problems"]:
        item["status"] = "fail"
    if problem:
        item["problems"].append(problem)
        item["status"] = "fail"


def _rand_label(n=12):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def _chain_is_overseas(chain):
    # 日志形如「海外出口[节点]」；连接快照形如「节点 ← 海外出口」。必须是这个组名本身，不是包含它的别的组名
    return re.search(r"(^|←\s*)" + re.escape(util.GROUP_OVERSEAS) + r"(\[|\s*$)", (chain or "").strip()) is not None


def _chain_is_direct(chain):
    return (chain or "").strip().startswith("DIRECT") or (chain or "").strip().endswith("DIRECT")


def _evidence_summary(rec, host):
    hits = rec.evidence(host)
    if not hits:
        return None, "核心日志里没有 " + host + " 的记录"
    hit = hits[0]
    return hit, host + " → 规则 " + hit["rule"] + " → " + hit["chain"] + "（" + hit["source"] + "）"


def check_1(c, state, item):
    cfg = c.configs()
    mode = cfg.get("mode")
    tun = (cfg.get("tun") or {}).get("enable")
    item["evidence"].append("运行模式=" + str(mode) + "；核心 tun.enable=" + str(tun) + "；ipv6=" + str(cfg.get("ipv6")))
    if mode != "rule":
        _set(item, False, "运行模式不是规则模式")
    if not tun:
        _set(item, False, "虚拟网卡没开")
    runtime_text = util.read_text(verge.files(state["dir"])["runtime"], "") or ""
    dns_ipv6 = yamlmini.get_child_scalar(runtime_text, "dns", "ipv6")
    item["evidence"].append("运行配置 dns.ipv6=" + str(dns_ipv6))
    if dns_ipv6 is not False:
        _set(item, False, "DNS 的 IPv6 没有关")
    if state.get("enable_dns_settings") or state.get("profile_dns_override_on"):
        _set(item, False, "客户端的「DNS 覆写」开着，会盖掉脚本里的 DNS")
    registered, how = verge.auto_launch_registered()
    item["evidence"].append("verge.yaml enable_auto_launch=" + str(state.get("enable_auto_launch")) + "；系统登记：" + how)
    if not state.get("enable_auto_launch"):
        _set(item, False, "客户端开机自启没开")
    elif registered is False:
        _set(item, False, "开关开了但系统里没有登记开机启动项：请在客户端设置里把「开机自启」关掉再打开一次")
    elif registered is None:
        item["problems"].append("开机启动项未能自动确认（" + how + "）")
        item["status"] = "unknown"
        return
    _set(item, True)


ALLOWED_DIRECT = set()


def _allowed_direct_payloads(platform):
    allowed = set()
    plan = {"mode": "A", "platform": platform, "exit": {"name": "x"}}
    for rule in render.build_rules(plan):
        parts = rule.split(",")
        if len(parts) >= 3 and parts[2] == "DIRECT":
            allowed.add(parts[1].lower())
    return allowed


def check_2(c, state, item):
    rules = c.rules()
    if not rules:
        _set(item, False, "读不到规则")
        return
    last = rules[-1]
    match_rules = [r for r in rules if r.get("type") == "Match"]
    first_match = match_rules[0] if match_rules else {}
    item["evidence"].append("共 " + str(len(rules)) + " 条规则；兜底：" + " → ".join(r.get("proxy", "") for r in match_rules)
                            + "（MATCH,REJECT 只接住出口不支持的 UDP，防止落到 DIRECT）")
    if first_match.get("proxy") != util.GROUP_OVERSEAS:
        _set(item, False, "兜底规则没有指向海外出口")
    if last.get("type") != "Match" or last.get("proxy") not in (util.GROUP_OVERSEAS, "REJECT"):
        _set(item, False, "最后一条规则不是兜底")
    overseas_rules = [r for r in rules if r.get("proxy") == util.GROUP_OVERSEAS]
    rejects = [r for r in rules if r.get("proxy") == "REJECT"]
    if len(rejects) < len(overseas_rules):
        _set(item, False, "有走海外出口的规则后面缺 REJECT 兜底：出口不支持 UDP 时，UDP 会落到 DIRECT")
    allowed = _allowed_direct_payloads(util.platform_name())
    bad = [r for r in rules if r.get("proxy") == "DIRECT" and str(r.get("payload", "")).lower() not in allowed]
    direct_count = len([r for r in rules if r.get("proxy") == "DIRECT"])
    item["evidence"].append("指向 DIRECT 的规则 " + str(direct_count) + " 条，均属于：局域网/保留地址、B 站、微信、国内域名名单、国内 IP 名单")
    if bad:
        _set(item, False, "有不在允许范围内的直连规则：" + ", ".join(r.get("type", "") + "," + str(r.get("payload")) for r in bad[:5]))
    group = c.proxies().get(util.GROUP_OVERSEAS) or {}
    members = group.get("all") or []
    item["evidence"].append(util.GROUP_OVERSEAS + " 组成员：" + "、".join(members) + "；当前：" + str(group.get("now")))
    if not members:
        _set(item, False, "找不到「海外出口」组")
    if "DIRECT" in members:
        _set(item, False, "海外出口组里有 DIRECT")
    if group.get("type") not in ("Selector",):
        _set(item, False, "海外出口组不是手动选择（select）")
    providers = c.rule_providers()
    for name in ("direct-domain", "china-ip"):
        info = providers.get(name) or {}
        count = info.get("ruleCount")
        item["evidence"].append("规则集 " + name + "：" + str(count) + " 条，更新于 " + str(info.get("updatedAt")))
        if not count:
            _set(item, False, "规则集 " + name + " 没有加载成功（为空）")
    _set(item, True)


def _curl_ip(url, timeout=15):
    code, body, err = util.curl(url, timeout=timeout)
    if code != 0:
        return None, "curl exit " + str(code) + " " + err.strip()[:120]
    text = body.strip()
    if url.endswith("/cdn-cgi/trace"):
        match = re.search(r"^ip=(\S+)", text, re.M)
        loc = re.search(r"^loc=(\S+)", text, re.M)
        return (match.group(1) if match else None), ("loc=" + loc.group(1) if loc else "")
    if "httpbin" in url:
        try:
            return json.loads(text).get("origin", "").split(",")[0].strip(), ""
        except ValueError:
            return None, text[:80]
    first = text.splitlines()[0].strip() if text else ""
    try:
        ipaddress.ip_address(first)
        return first, ""
    except ValueError:
        return None, text[:80]


def check_3(rec, item):
    for host, url in (("www.baidu.com", "https://www.baidu.com/"), ("www.bilibili.com", "https://www.bilibili.com/")):
        code, _, err = util.curl(url, timeout=15, extra=["-o", os.devnull])
        item["evidence"].append(host + "：curl exit " + str(code))
        if code != 0:
            _set(item, False, host + " 打不开：" + err.strip()[:100])
    code, body, err = util.curl("https://myip.ipip.net/", timeout=15)
    item["evidence"].append("myip.ipip.net：" + (body.strip()[:120] if code == 0 else "失败 " + err.strip()[:80]))
    if code != 0:
        _set(item, False, "myip.ipip.net 打不开")
    elif "中国" not in body:
        _set(item, False, "myip.ipip.net 显示的不是国内 IP（直连名单没生效）")
    time.sleep(1.5)
    for host in ("www.baidu.com", "www.bilibili.com", "myip.ipip.net"):
        hit, text = _evidence_summary(rec, host)
        item["evidence"].append(text)
        if hit is None:
            item["problems"].append("缺日志证据：" + host)
        elif not _chain_is_direct(hit["chain"]):
            _set(item, False, host + " 没有走直连：" + hit["chain"])
    if item["problems"] and item["status"] != "fail":
        item["status"] = "unknown"
        return
    _set(item, True)


def check_4(rec, item, report):
    ips = {}
    for url in ("https://api.ipify.org", "https://ipinfo.io/ip", "https://claude.ai/cdn-cgi/trace", "https://httpbin.org/ip"):
        ip, note = _curl_ip(url)
        ips[url] = ip
        item["evidence"].append(url + " → " + str(ip) + (" " + note if note else ""))
        if ip is None:
            _set(item, False, url + " 取不到出口 IP")
        if "cdn-cgi" in url and note.startswith("loc="):
            loc = note[4:]
            report["exit_loc"] = loc
            if loc in ("HK", "MO", "CN"):
                _set(item, False, "Claude 看到的出口地区是 " + loc + "（不提供服务的地区）")
    for url in ("https://statsig.anthropic.com/", "https://www.googletagmanager.com/gtm.js?id=GTM-TEST"):
        code, _, _ = util.curl(url, timeout=15, extra=["-o", os.devnull])
        item["evidence"].append(url + "：curl exit " + str(code))
    distinct = sorted(set(ip for ip in ips.values() if ip))
    report["exit_ips"] = distinct
    if len(distinct) > 1:
        _set(item, False, "出口 IP 不唯一：" + "、".join(distinct) + "（节点出口不固定：建议改用 B 方案，买静态住宅 IP 做链式代理）")
    code, body, _ = util.curl("http://ip-api.com/json/?fields=status,country,countryCode,isp,org,as,hosting,proxy,query", timeout=15)
    if code == 0:
        try:
            info = json.loads(body)
            report["exit_info"] = info
            item["evidence"].append("出口 IP 信息（ip-api.com，仅参考）：" + info.get("countryCode", "?") + " " + info.get("isp", "") + " / " + info.get("as", "")
                                    + "；hosting=" + str(info.get("hosting")) + " proxy=" + str(info.get("proxy")))
            if info.get("countryCode") in ("HK", "MO", "CN"):
                _set(item, False, "出口 IP 归属地是 " + info.get("countryCode"))
        except ValueError:
            pass
    time.sleep(1.5)
    for host in ("api.ipify.org", "ipinfo.io", "claude.ai", "statsig.anthropic.com", "httpbin.org", "www.googletagmanager.com"):
        hit, text = _evidence_summary(rec, host)
        item["evidence"].append(text)
        if hit is None:
            item["problems"].append("缺日志证据：" + host)
        elif not _chain_is_overseas(hit["chain"]):
            _set(item, False, host + " 没有走海外出口：" + hit["chain"])
    if item["problems"] and item["status"] != "fail":
        item["status"] = "unknown"
        return
    _set(item, True)


def check_5(rec, item):
    # (a) 系统解析器拿到的必须是 fake-ip（说明查询被核心接管，没有发给路由器/运营商）
    for _ in range(3):
        name = _rand_label() + ".example.com"
        try:
            addrs = sorted(set(info[4][0] for info in socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)))
        except socket.gaierror as error:
            addrs = ["解析失败 " + str(error)]
        fake = all(a.startswith("198.18.") or a.startswith("198.19.") for a in addrs)
        item["evidence"].append("系统解析 " + name + " → " + ", ".join(addrs) + ("（fake-ip，查询被核心接管）" if fake else ""))
        if not fake:
            _set(item, False, "系统 DNS 没有被核心接管：" + name + " 得到真实地址")
    # (b) 3 个随机子域名：请求后在核心日志里必须能看到、且走海外出口；解析者（DNS 出口）不能在中国大陆
    hosts = []
    for _ in range(3):
        # edns.ip-api.com 会 302 到它自己发的随机子域名；跟随跳转并记下最终主机名
        code, body, err = util.curl("http://edns.ip-api.com/json", timeout=20, follow=True, extra=["-w", "\n@@URL@@%{url_effective}"])
        if code != 0:
            _set(item, False, "edns.ip-api.com 请求失败：" + err.strip()[:80])
            continue
        payload, _, effective = body.rpartition("\n@@URL@@")
        host_match = re.match(r"^https?://([^/]+)", effective.strip())
        host = host_match.group(1) if host_match else "edns.ip-api.com"
        hosts.append(host)
        try:
            dns = json.loads(payload).get("dns", {})
        except ValueError:
            dns = {}
        geo = str(dns.get("geo", ""))
        item["evidence"].append(host + " 的解析者：" + str(dns.get("ip")) + "（" + geo + "）")
        if any(word in geo for word in ("China", "中国")) and "Hong Kong" not in geo and "Taiwan" not in geo:
            _set(item, False, "DNS 出口在中国大陆：" + geo + "（DNS 泄漏）")
    # (c) DoH：向 Cloudflare DoH 查随机域名，核心日志里这条 DoH 连接必须走海外出口
    doh_name = _rand_label() + ".example.org"
    code, body, _ = util.curl("https://cloudflare-dns.com/dns-query?name=" + doh_name + "&type=A", timeout=15,
                              extra=["-H", "accept: application/dns-json"])
    item["evidence"].append("DoH 查询 " + doh_name + "：curl exit " + str(code))
    # (d) isp.ac DNS 泄漏测试接口（第三方，结构可能变化；拿不到就如实记为未验证）
    code, body, err = util.curl("https://isp.ac/dns-leak-test", timeout=20, extra=["-H", "Accept: application/json"])
    if code == 0 and body.strip().startswith(("{", "[")):
        text = body
        china = re.search(r'"(country|country_code|countryCode)"\s*:\s*"(CN|China|中国)"', text)
        item["evidence"].append("isp.ac：" + re.sub(r"\s+", " ", text)[:300])
        if china:
            _set(item, False, "isp.ac 报告有中国大陆 DNS 服务器")
    else:
        item["evidence"].append("isp.ac 接口不可用或返回非 JSON（未验证此子项）：exit " + str(code) + " " + err.strip()[:80])
    time.sleep(1.5)
    for host in hosts + ["cloudflare-dns.com"]:
        hit, text = _evidence_summary(rec, host)
        item["evidence"].append(text)
        if hit is None:
            item["problems"].append("缺日志证据：" + host)
        elif not _chain_is_overseas(hit["chain"]):
            _set(item, False, host + " 没有走海外出口：" + hit["chain"])
    if item["problems"] and item["status"] != "fail":
        item["status"] = "unknown"
        return
    _set(item, True)


def check_6(rec, item):
    code, body, err = util.curl("https://1.1.1.1/cdn-cgi/trace", timeout=15)
    loc = re.search(r"^loc=(\S+)", body or "", re.M)
    item["evidence"].append("curl https://1.1.1.1/cdn-cgi/trace：exit " + str(code) + (" loc=" + loc.group(1) if loc else ""))
    if code != 0:
        _set(item, False, "直连 IP 请求失败：" + err.strip()[:80])
    time.sleep(1.5)
    hit, text = _evidence_summary(rec, "1.1.1.1")
    item["evidence"].append(text)
    if hit is None:
        item["status"] = "unknown"
        item["problems"].append("核心日志里没有 1.1.1.1：可能没有经过虚拟网卡")
        return
    if not _chain_is_overseas(hit["chain"]):
        _set(item, False, "1.1.1.1 没有走海外出口：" + hit["chain"])
    _set(item, True)


def check_7(item):
    log = os.path.join(util.work_root(), "killswitch-install.log")
    text = util.read_text(log)
    if not text:
        item["evidence"].append("没有找到 " + log + "（断网开关还没装）")
        return
    match = re.findall(r"断网开关安装完成，错误数：(\d+)", text)
    if match:
        item["evidence"].append("安装日志：错误数 " + match[-1])
        _set(item, match[-1] == "0", None if match[-1] == "0" else "安装脚本报告了错误")
    else:
        tail = text.strip().splitlines()[-3:]
        _set(item, False, "安装没有完成：" + " / ".join(tail))


def check_8(item, network):
    if util.IS_MAC:
        code, out, _ = util.run(["/bin/launchctl", "print", "system/" + util.MAC_LAUNCHD_LABEL])
        running = code == 0 and "state = running" in out
        item["evidence"].append("launchd " + util.MAC_LAUNCHD_LABEL + "：" + ("running" if running else ("已加载" if code == 0 else "未加载")))
        log = util.read_text(os.path.join(util.work_root(), "killswitch-install.log"), "") or ""
        rules = [line for line in log.splitlines() if "block return" in line]
        item["evidence"].append("安装时锚点 " + util.MAC_PF_ANCHOR + " 的拦截规则：" + str(len(rules)) + " 条（查看实时规则需 sudo pfctl -a " + util.MAC_PF_ANCHOR + " -sr）")
        if not os.path.exists("/Library/LaunchDaemons/" + util.MAC_LAUNCHD_LABEL + ".plist"):
            _set(item, False, "launchd plist 不存在")
        elif not running:
            _set(item, False, "开机自启服务没有在运行")
        elif not rules:
            item["status"] = "unknown"
            item["problems"].append("安装日志里没有锚点规则证据")
        else:
            _set(item, True)
        return
    count = network.get("guard_rules") or 0
    task = network.get("guard_task")
    blocks = [p for p in network.get("firewall", []) if p.get("default_outbound") == "Block"]
    item["evidence"].append("防火墙规则组 clash-guard：" + str(count) + " 条；计划任务 " + util.WIN_TASK_NAME + "：" + ("在" if task else "不在")
                            + "；默认出站 Block 的配置文件：" + ",".join(p["name"] for p in blocks))
    _set(item, count >= 9 and bool(task) and len(blocks) == 3, None if (count >= 9 and task and len(blocks) == 3) else "断网开关不完整")


def read_drills():
    path = os.path.join(util.work_root(), "drills.result")
    text = util.read_text(path)
    result = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def run(items=None):
    items = items or [1, 2, 3, 4, 5, 6, 7, 8]
    state = verge.read_state()
    report = {"time": util.now_iso(), "platform": util.platform_name(), "items": []}
    try:
        c = ctl.Controller()
        c.version()
    except Exception as error:  # noqa: BLE001
        report["error"] = "连不上核心控制器：" + str(error)
        return report
    network = platform_.network_state() if util.IS_WINDOWS else {}
    rec = ctl.LogRecorder(c)
    rec.start()
    try:
        titles = {1: "规则模式 / 虚拟网卡 / 开机自启 / DNS IPv6 关", 2: "规则兜底与直连范围、海外出口无 DIRECT",
                  3: "国内站直连，myip 显示本地国内 IP", 4: "海外站同一海外出口 IP（非港澳大陆）",
                  5: "DNS 泄漏测试", 6: "直连 IP 也走虚拟网卡", 7: "断网开关安装 0 错误", 8: "防火墙规则在、开机服务在运行"}
        for no in items:
            item = _item(no, titles[no])
            try:
                if no == 1:
                    check_1(c, state, item)
                elif no == 2:
                    check_2(c, state, item)
                elif no == 3:
                    check_3(rec, item)
                elif no == 4:
                    check_4(rec, item, report)
                elif no == 5:
                    check_5(rec, item)
                elif no == 6:
                    check_6(rec, item)
                elif no == 7:
                    check_7(item)
                elif no == 8:
                    check_8(item, network)
            except Exception as error:  # noqa: BLE001
                item["status"] = "unknown"
                item["problems"].append("检查过程出错：" + str(error))
            report["items"].append(item)
    finally:
        rec.stop()
    report["log_lines_captured"] = len(rec.lines)
    report["connections_captured"] = len(rec.connections)
    if rec.errors:
        report["recorder_errors"] = rec.errors[:5]
    drills = read_drills()
    if drills:
        report["drills"] = drills
    return report


def format_report(report):
    mark = {"pass": "通过", "fail": "不通过", "unknown": "未验证"}
    lines = []
    if report.get("error"):
        return "验收无法进行：" + report["error"]
    for item in report["items"]:
        lines.append("第 %d 项 %s：%s" % (item["no"], item["title"], mark[item["status"]]))
        for ev in item["evidence"]:
            lines.append("    · " + ev)
        for problem in item["problems"]:
            lines.append("    ✗ " + problem)
    drills = report.get("drills")
    if drills:
        for no in (9, 10, 11):
            lines.append("第 %d 项 演练：%s" % (no, {"PASS": "通过", "FAIL": "不通过"}.get(drills.get("drill" + str(no)), "未执行")))
        lines.append("    · 演练结束时状态：" + drills.get("final", "?"))
    lines.append("（核心日志采到 %d 行、连接快照 %d 条）" % (report.get("log_lines_captured", 0), report.get("connections_captured", 0)))
    return "\n".join(lines)
