"""生成 Clash Verge 全局扩展脚本 Script.js。

为什么全部放在全局 Script 里：
  - 订阅文件不动，订阅更新冲不掉；
  - 全局 Merge 是 deep merge，写 proxies 会把订阅节点整段替换掉，住宅节点只能靠脚本前插；
  - 规则、DNS、代理组整段由脚本赋值，结果确定，不受订阅自带规则影响。
脚本只用 ES5 语法（客户端内置 JS 引擎）。所有数据由 Python 生成为 JSON 字面量，
同一份数据也用来拼 mihomo -t 的测试配置，保证「校验的」就是「写进去的」。
"""

import ipaddress
import json
import re

from . import util

BEGIN = "// >>> claude-network begin"
END = "// <<< claude-network end"

CLAUDE_DOMAINS = ["claude.ai", "claude.com", "anthropic.com", "claudeusercontent.com", "claudemcpclient.com"]
LAN_V4 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10", "224.0.0.0/4", "255.255.255.255/32"]
LAN_V6 = ["::1/128", "fc00::/7", "fe80::/10", "ff00::/8"]
LAN_SUFFIX = ["lan", "local", "localdomain", "home.arpa"]
BILIBILI = [
    "bilibili.com", "bilibili.cn", "biliapi.com", "biliapi.net", "bilivideo.com", "bilivideo.cn",
    "hdslb.com", "b23.tv", "acgvideo.com", "biliimg.com", "bilicdn1.com", "bilicdn2.com", "biligame.com",
    "biligame.net", "bilibiligame.net", "biliintl.com",
]
WECHAT = [
    "weixin.qq.com", "wx.qq.com", "weixin.com", "wechat.com", "servicewechat.com", "weixinbridge.com",
    "wechatpay.com", "wechatapp.com", "wechatlegal.net", "qpic.cn", "qlogo.cn", "tenpay.com",
]
WECHAT_PROCESSES = {"windows": ["WeChat.exe", "Weixin.exe", "WeChatAppEx.exe"], "darwin": ["WeChat"]}
DIRECT_TXT = "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/direct.txt"
CNCIDR_TXT = "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/cncidr.txt"
NCSI = ["+.msftconnecttest.com", "+.msftncsi.com"]


class PlanError(ValueError):
    pass


def fake_ip_filter():
    return [
        "*.lan", "*.local", "*.localdomain", "*.home.arpa", "localhost.ptlogin2.qq.com",
        "time.*.com", "time.*.gov", "time.*.edu.cn", "time.*.apple.com", "time1.cloud.tencent.com",
        "ntp.*.com", "ntp1.aliyun.com", "ntp.aliyun.com", "pool.ntp.org", "*.pool.ntp.org",
        "stun.*.*", "stun.*.*.*", "+.stun.*.*",
        "+.msftconnecttest.com", "+.msftncsi.com", "connectivitycheck.gstatic.com", "captive.apple.com",
    ]


def residential_target_rule(server):
    """住宅代理自己的地址 → 跳板。不写的话：访问住宅地址本身会被兜底规则送进住宅出口，绕成死循环。"""
    try:
        ip = ipaddress.ip_address(server)
    except ValueError:
        return "DOMAIN," + server + "," + util.GROUP_JUMP
    if ip.version == 4:
        return "IP-CIDR," + str(ip) + "/32," + util.GROUP_JUMP + ",no-resolve"
    return "IP-CIDR6," + str(ip) + "/128," + util.GROUP_JUMP + ",no-resolve"


def build_rules(plan):
    """规则顺序即优先级。

    UDP 兜底：mihomo 匹配时，若命中规则的出口不支持 UDP（住宅 SOCKS5 关了 UDP、演练节点、选中的节点本身不支持），
    会「跳过这条规则继续往下找」，找不到就落到 DIRECT——海外 UDP（QUIC、WebRTC）会从本地 IP 直接出去。
    所以每条走海外出口的规则后面紧跟一条同条件的 REJECT：TCP 永远停在前一条，只有被跳过的 UDP 会落到 REJECT。
    浏览器的 QUIC 被拒后会自动回落到 TCP。
    """
    overseas = util.GROUP_OVERSEAS
    rules = []
    # 1. Claude 自家域名 → 海外出口（排最前，任何名单都不能把它们分到直连）
    for d in CLAUDE_DOMAINS:
        rules.append("DOMAIN-SUFFIX," + d + "," + overseas)
        rules.append("DOMAIN-SUFFIX," + d + ",REJECT")
    if plan["mode"] == "B":
        rules.append(residential_target_rule(plan["residential"]["server"]))
    # 2. 局域网与保留地址 → DIRECT
    rules += ["IP-CIDR," + c + ",DIRECT,no-resolve" for c in LAN_V4]
    rules += ["IP-CIDR6," + c + ",DIRECT,no-resolve" for c in LAN_V6]
    rules += ["DOMAIN-SUFFIX," + d + ",DIRECT" for d in LAN_SUFFIX]
    # 3. B 站与微信 → DIRECT；B 站海外 CDN 域名按关键词 mirrorakam 匹配（后缀匹配不上）
    rules += ["DOMAIN-SUFFIX," + d + ",DIRECT" for d in BILIBILI]
    rules.append("DOMAIN-KEYWORD,mirrorakam,DIRECT")
    rules += ["DOMAIN-SUFFIX," + d + ",DIRECT" for d in WECHAT]
    rules += ["PROCESS-NAME," + p + ",DIRECT" for p in WECHAT_PROCESSES.get(plan["platform"], [])]
    # 4. 海外域名分类 → 海外出口：必须在国内名单前面（direct.txt 里混着 googletagmanager、doubleclick 这类海外域名）
    rules.append("GEOSITE,geolocation-!cn," + overseas)
    rules.append("GEOSITE,geolocation-!cn,REJECT")
    # 5. 国内域名名单、国内 IP 名单 → DIRECT；IP 规则 no-resolve，不为匹配去本地解析域名
    rules.append("RULE-SET,direct-domain,DIRECT")
    rules.append("RULE-SET,china-ip,DIRECT,no-resolve")
    # 6. 其余全部 → 海外出口；出口不支持的 UDP → REJECT（绝不落到 DIRECT）
    rules.append("MATCH," + overseas)
    rules.append("MATCH,REJECT")
    return rules


def build_dns():
    cn = list(util.CN_DNS)
    direct_policy_domains = ",".join(["+." + d for d in BILIBILI + WECHAT])
    return {
        "enable": True,
        "listen": "127.0.0.1:1053",
        "ipv6": False,
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter-mode": "blacklist",
        "fake-ip-filter": fake_ip_filter(),
        # DNS 连接本身也按规则走：1.1.1.1 / 8.8.8.8 命中兜底 → 经海外出口；223.5.5.5 命中国内 IP → 直连
        "respect-rules": True,
        "use-hosts": True,
        "use-system-hosts": False,
        "cache-algorithm": "arc",
        "default-nameserver": cn,
        "nameserver": list(util.OVERSEAS_DOH),
        "proxy-server-nameserver": cn,
        "direct-nameserver": cn,
        "direct-nameserver-follow-policy": False,
        "nameserver-policy": {
            "rule-set:direct-domain": cn,
            direct_policy_domains: cn,
            ",".join(NCSI): cn,
        },
    }


def build_sniffer():
    return {
        "enable": True,
        "force-dns-mapping": True,
        "parse-pure-ip": True,
        "override-destination": False,
        "sniff": {"HTTP": {"ports": [80, "8080-8880"]}, "TLS": {"ports": [443, 8443]}, "QUIC": {"ports": [443, 8443]}},
        "skip-domain": ["Mijia Cloud", "+.push.apple.com"],
    }


def build_rule_providers(plan):
    download = util.GROUP_JUMP if plan["mode"] == "B" else util.GROUP_OVERSEAS
    return {
        "direct-domain": {"type": "http", "behavior": "domain", "format": "yaml", "url": DIRECT_TXT,
                          "path": "./ruleset/claude-network-direct.yaml", "interval": 86400, "proxy": download},
        "china-ip": {"type": "http", "behavior": "ipcidr", "format": "yaml", "url": CNCIDR_TXT,
                     "path": "./ruleset/claude-network-cncidr.yaml", "interval": 86400, "proxy": download},
    }


def drill_node():
    # TEST-NET-1 地址，永远连不上：只用于第 9 项演练「出口挂了必须断网」。
    return {"name": util.DRILL_NODE, "type": "socks5", "server": "192.0.2.1", "port": 9, "udp": False}


def residential_node(plan, with_credentials=True):
    res = plan["residential"]
    node = {"name": util.RESIDENTIAL_NODE, "type": "socks5", "server": res["server"], "port": int(res["port"]),
            "udp": False, "dialer-proxy": util.GROUP_JUMP}
    if res.get("username"):
        node["username"] = res["username"] if with_credentials else "CLAUDE_NET_PLACEHOLDER"
        node["password"] = res.get("password", "") if with_credentials else "CLAUDE_NET_PLACEHOLDER"
    return node


def group_specs(plan):
    """代理组规格：members 里 provider 非空的节点来自 proxy-providers，用 use + filter 引用。"""
    drill = {"name": util.DRILL_NODE, "provider": None}
    if plan["mode"] == "A":
        overseas_members = [{"name": plan["exit"]["name"], "provider": plan["exit"].get("provider")}, drill]
        return [{"name": util.GROUP_OVERSEAS, "members": overseas_members}]
    overseas_members = [{"name": util.RESIDENTIAL_NODE, "provider": None}, drill]
    # 跳板组末尾也放演练节点：跳板节点全都不在了（改名、来自订阅的 filter 匹配不到）时，组里只剩它 → 断网，
    # 而不是变成空组（mihomo 会用 COMPATIBLE 顶上，住宅代理就被从国内直接拨出去）
    jump_members = [{"name": j["name"], "provider": j.get("provider")} for j in plan["jump"]] + [drill]
    return [
        {"name": util.GROUP_OVERSEAS, "members": overseas_members},
        {"name": util.GROUP_JUMP, "members": jump_members},
    ]


def validate_plan(plan):
    if plan.get("mode") not in ("A", "B"):
        raise PlanError("mode 只能是 A 或 B")
    if plan.get("platform") not in ("windows", "darwin"):
        raise PlanError("platform 只能是 windows 或 darwin")
    reserved = {util.GROUP_OVERSEAS, util.GROUP_JUMP, util.RESIDENTIAL_NODE, util.DRILL_NODE, "DIRECT", "REJECT", "GLOBAL"}
    if plan["mode"] == "A":
        exit_node = plan.get("exit") or {}
        if not exit_node.get("name"):
            raise PlanError("A 方案需要指定海外出口节点")
        if exit_node["name"] in reserved:
            raise PlanError("出口节点名与保留名冲突：" + exit_node["name"])
        if exit_node.get("region") in ("HK", "MO", "CN"):
            raise PlanError("香港、澳门、大陆节点不能当海外出口（Claude 在这些地区不提供服务），只能用 B 方案当跳板")
    else:
        if not plan.get("jump"):
            raise PlanError("B 方案至少需要一个跳板节点")
        for item in plan["jump"]:
            if item["name"] in reserved:
                raise PlanError("跳板节点名与保留名冲突：" + item["name"])
        res = plan.get("residential") or {}
        if not res.get("server") or not res.get("port"):
            raise PlanError("B 方案需要住宅代理的 IP 和端口")
        try:
            port = int(res["port"])
        except (TypeError, ValueError) as error:
            raise PlanError("住宅代理端口不是数字") from error
        if not 1 <= port <= 65535:
            raise PlanError("住宅代理端口超出 1-65535")
        if not re.match(r"^[A-Za-z0-9.:-]+$", str(res["server"])):
            raise PlanError("住宅代理地址只能是 IP 或域名")
        if bool(res.get("username")) != bool(res.get("password")):
            raise PlanError("住宅代理账号和密码要么都填，要么都不填")
        for value in (res.get("username") or "", res.get("password") or ""):
            if re.search(r"[\x00-\x1f\u2028\u2029]", value):
                raise PlanError("住宅代理账号或密码含控制字符")


def build_data(plan, with_credentials=True):
    validate_plan(plan)
    data = {
        "generated": plan.get("generated", util.now_iso()),
        "mode": plan["mode"],
        "overseas": util.GROUP_OVERSEAS,
        "jump": util.GROUP_JUMP,
        "extraProxies": ([residential_node(plan, with_credentials)] if plan["mode"] == "B" else []) + [drill_node()],
        "groups": group_specs(plan),
        "rules": build_rules(plan),
        "ruleProviders": build_rule_providers(plan),
        "dns": build_dns(),
        "sniffer": build_sniffer(),
    }
    return data


SCRIPT_BODY = r"""
function claudeNetClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function claudeNetEscape(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
}

function main(config, profileName) {
  var C = CLAUDE_NET;
  var i, j;
  if (!config || typeof config !== "object") {
    config = {};
  }
  // 1. 节点：去掉同名旧项，把住宅出口与演练节点前插；订阅节点原样保留
  var reserved = {};
  for (i = 0; i < C.extraProxies.length; i++) {
    reserved[C.extraProxies[i].name] = true;
  }
  var kept = [];
  var source = Array.isArray(config.proxies) ? config.proxies : [];
  for (i = 0; i < source.length; i++) {
    if (source[i] && !reserved[source[i].name]) {
      kept.push(source[i]);
    }
  }
  config.proxies = claudeNetClone(C.extraProxies).concat(kept);
  var have = {};
  for (i = 0; i < config.proxies.length; i++) {
    have[config.proxies[i].name] = true;
  }
  var providers = config["proxy-providers"] || {};

  // 2. 代理组：手动选择，没有 DIRECT；引用的节点不存在时只留演练节点 → 断网而不是回落直连
  var ours = [];
  var ourNames = {};
  for (i = 0; i < C.groups.length; i++) {
    var spec = C.groups[i];
    var group = { name: spec.name, type: "select", proxies: [] };
    var use = [];
    var filters = [];
    for (j = 0; j < spec.members.length; j++) {
      var m = spec.members[j];
      if (m.provider) {
        if (providers[m.provider]) {
          if (use.indexOf(m.provider) < 0) {
            use.push(m.provider);
          }
          filters.push(claudeNetEscape(m.name));
        }
      } else if (have[m.name]) {
        group.proxies.push(m.name);
      }
    }
    if (use.length > 0) {
      group.use = use;
      group.filter = "^(" + filters.join("|") + ")$";
    }
    var drillName = C.extraProxies[C.extraProxies.length - 1].name;
    var real = 0;
    for (j = 0; j < group.proxies.length; j++) {
      if (group.proxies[j] !== drillName) {
        real++;
      }
    }
    if (real === 0 && use.length === 0) {
      group.proxies = [drillName];
    }
    ours.push(group);
    ourNames[spec.name] = true;
  }
  var others = [];
  var oldGroups = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];
  for (i = 0; i < oldGroups.length; i++) {
    if (oldGroups[i] && !ourNames[oldGroups[i].name]) {
      others.push(oldGroups[i]);
    }
  }
  config["proxy-groups"] = ours.concat(others);

  // 3. 规则、规则集、DNS、嗅探：整段赋值；订阅自带的规则集不再使用，一并移除
  config.rules = C.rules.slice(0);
  config["rule-providers"] = claudeNetClone(C.ruleProviders);
  config.dns = claudeNetClone(C.dns);
  config.sniffer = claudeNetClone(C.sniffer);

  // 4. 基础开关（客户端 config.yaml 里的同名键会再盖一次，那边也已改成一致的值）
  config.mode = "rule";
  config.ipv6 = false;
  config["log-level"] = "info";
  config["allow-lan"] = false;
  config["find-process-mode"] = "strict";
  config["unified-delay"] = true;
  config.profile = { "store-selected": true, "store-fake-ip": true };
  return config;
}
"""


def render_script(plan, with_credentials=True):
    data = build_data(plan, with_credentials)
    blob = json.dumps(data, ensure_ascii=False, indent=2)
    blob = blob.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    header = [
        BEGIN,
        "// 本文件由 claude-network skill 生成，整份替换了原来的全局 Script.js（原文件在备份目录里）。",
        "// 方案：" + ("A（单个海外节点直接当出口）" if plan["mode"] == "A" else "B（跳板 → 静态住宅 SOCKS5 链式出口）"),
        "// 生成时间：" + data["generated"],
        "// 想改规则请重新运行 cnet.py plan，不要手工编辑；文件含住宅代理凭据，权限 0600，勿外发。",
    ]
    return "\n".join(header) + "\nvar CLAUDE_NET = " + blob + ";\n" + SCRIPT_BODY.strip("\n") + "\n" + END + "\n"


def simulate(plan, subscription_proxies, providers=None, with_credentials=False):
    """用 Python 复现脚本效果，生成 mihomo -t 的测试配置（JSON 即合法 YAML）。

    subscription_proxies：订阅节点（测试时用占位 socks5 代替，只校验结构）。
    """
    data = build_data(plan, with_credentials)
    reserved = {p["name"] for p in data["extraProxies"]}
    proxies = [dict(p) for p in data["extraProxies"]] + [p for p in subscription_proxies if p.get("name") not in reserved]
    have = {p["name"] for p in proxies}
    providers = providers or {}
    groups = []
    drill = util.DRILL_NODE
    for spec in data["groups"]:
        group = {"name": spec["name"], "type": "select", "proxies": []}
        use, filters = [], []
        for member in spec["members"]:
            if member.get("provider"):
                if member["provider"] in providers:
                    if member["provider"] not in use:
                        use.append(member["provider"])
                    filters.append(re.escape(member["name"]))
            elif member["name"] in have:
                group["proxies"].append(member["name"])
        if use:
            group["use"] = use
            group["filter"] = "^(" + "|".join(filters) + ")$"
        if not [p for p in group["proxies"] if p != drill] and not use:
            group["proxies"] = [drill]
        groups.append(group)
    return {
        "mixed-port": 17897,
        "mode": "rule",
        "ipv6": False,
        "log-level": "info",
        "allow-lan": False,
        "find-process-mode": "strict",
        "proxies": proxies,
        "proxy-providers": providers,
        "proxy-groups": groups,
        "rules": data["rules"],
        "rule-providers": data["ruleProviders"],
        "dns": data["dns"],
        "sniffer": data["sniffer"],
    }
