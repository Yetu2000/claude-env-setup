"""节点筛选：剔除信息条目、标出只能当跳板的地区（香港 / 澳门 / 大陆），并发测延迟。"""

import concurrent.futures
import re
import unicodedata

from . import util

# 不是节点的内置项与组类型
NON_NODE_TYPES = {
    "Direct", "Reject", "RejectDrop", "Compatible", "Pass", "PassRule", "Dns",
    "Selector", "URLTest", "Fallback", "LoadBalance", "Relay",
}
BUILTIN_NAMES = {"DIRECT", "REJECT", "REJECT-DROP", "GLOBAL", "COMPATIBLE", "PASS", "PASS-RULE"}

# 订阅里「剩余流量、到期时间、官网」一类的信息条目
INFO_KEYWORDS = [
    "剩余", "流量", "到期", "过期", "官网", "套餐", "重置", "订阅", "续费", "购买", "客服",
    "群组", "网址", "试用", "邀请", "通知", "距离下次", "有效期", "已用", "总量", "公告", "频道",
    "expire", "traffic", "remaining", "website", "official", "subscribe", "telegram",
]

REGIONS = [
    ("HK", ["香港", "港", "hk", "hongkong", "hong kong", "🇭🇰"]),
    ("MO", ["澳门", "macau", "macao", "mo", "🇲🇴"]),
    ("CN", ["中国", "大陆", "国内", "回国", "上海", "北京", "深圳", "广州", "杭州", "成都", "cn", "china", "🇨🇳"]),
    ("TW", ["台湾", "台北", "tw", "taiwan", "🇹🇼"]),
    ("JP", ["日本", "东京", "大阪", "jp", "japan", "tokyo", "osaka", "🇯🇵"]),
    ("SG", ["新加坡", "狮城", "sg", "singapore", "🇸🇬"]),
    ("US", ["美国", "洛杉矶", "圣何塞", "西雅图", "纽约", "硅谷", "us", "usa", "america", "los angeles", "san jose", "🇺🇸"]),
    ("KR", ["韩国", "首尔", "kr", "korea", "seoul", "🇰🇷"]),
    ("GB", ["英国", "伦敦", "uk", "gb", "london", "britain", "🇬🇧"]),
    ("DE", ["德国", "法兰克福", "de", "germany", "frankfurt", "🇩🇪"]),
    ("FR", ["法国", "巴黎", "fr", "france", "paris", "🇫🇷"]),
    ("NL", ["荷兰", "阿姆斯特丹", "nl", "netherlands", "🇳🇱"]),
    ("CA", ["加拿大", "多伦多", "ca", "canada", "🇨🇦"]),
    ("AU", ["澳大利亚", "澳洲", "悉尼", "au", "australia", "🇦🇺"]),
    ("RU", ["俄罗斯", "莫斯科", "ru", "russia", "🇷🇺"]),
    ("IN", ["印度", "孟买", "india", "🇮🇳"]),
    ("MY", ["马来西亚", "吉隆坡", "malaysia", "🇲🇾"]),
    ("TH", ["泰国", "曼谷", "thailand", "🇹🇭"]),
    ("VN", ["越南", "vietnam", "🇻🇳"]),
    ("PH", ["菲律宾", "philippines", "🇵🇭"]),
    ("TR", ["土耳其", "turkey", "🇹🇷"]),
    ("AR", ["阿根廷", "argentina", "🇦🇷"]),
]

JUMP_ONLY = {"HK", "MO", "CN"}
# Claude 不提供服务或高风险的出口地区（只能当跳板）
UNSUPPORTED_EXIT = {"HK", "MO", "CN", "RU", "KP", "IR", "SY", "CU", "BY"}


def info_reason(name):
    lower = name.lower()
    for keyword in INFO_KEYWORDS:
        if keyword.lower() in lower:
            return "信息条目（含「" + keyword + "」）"
    return None


def _tokens(name):
    return [token for token in re.split(r"[^0-9a-z]+", name.lower()) if token]


def infer_region(name):
    lower = name.lower()
    tokens = _tokens(name)
    for region, words in REGIONS:
        for word in words:
            if any(unicodedata.category(ch) == "Lo" for ch in word) or not word.isascii():
                if word in lower:
                    return region
            elif " " in word:
                if word in lower:
                    return region
            elif word in tokens:
                return region
    return ""


def collect(controller):
    """返回节点列表：订阅里的 proxies 与 proxy-providers 里的节点都算，组与内置项不算。"""
    proxies = controller.proxies()
    provider_of = {}
    try:
        for provider_name, provider in controller.provider_proxies().items():
            if provider_name == "default" or provider.get("vehicleType") == "Compatible":
                continue
            for item in provider.get("proxies", []) or []:
                provider_of[item.get("name")] = provider_name
    except Exception:  # noqa: BLE001
        pass
    nodes, skipped = [], []
    for name, info in proxies.items():
        kind = info.get("type", "")
        if name in BUILTIN_NAMES or kind in NON_NODE_TYPES:
            continue
        reason = info_reason(name)
        if reason:
            skipped.append({"name": name, "reason": reason})
            continue
        if name in (util.RESIDENTIAL_NODE, util.DRILL_NODE):
            continue
        region = infer_region(name)
        nodes.append({
            "name": name,
            "type": kind,
            "udp": info.get("udp"),
            "provider": provider_of.get(name) or (info.get("provider-name") or None),
            "region": region,
            "jump_only": region in UNSUPPORTED_EXIT,
            "dialer_proxy": info.get("dialer-proxy") or None,
        })
    return nodes, skipped


def test_delays(controller, nodes, url="https://www.gstatic.com/generate_204", timeout_ms=5000, workers=8):
    def probe(node):
        delay, error = controller.delay(node["name"], url=url, timeout_ms=timeout_ms)
        return node["name"], delay, error

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for name, delay, error in pool.map(probe, nodes):
            results[name] = (delay, error)
    for node in nodes:
        node["delay"], node["error"] = results.get(node["name"], (None, "未测"))
    nodes.sort(key=lambda node: (node["delay"] is None, node["delay"] or 0))
    return nodes


def format_table(nodes, skipped):
    lines = ["#   延迟     地区  可当海外出口  类型        节点名"]
    for index, node in enumerate(nodes, 1):
        delay = (str(node["delay"]) + "ms") if node.get("delay") else "不通"
        exit_ok = "否(只能跳板)" if node["jump_only"] else ("是" if node.get("delay") else "—")
        if not node["region"] and node.get("delay"):
            exit_ok = "待实测地区"
        if node.get("dialer_proxy"):
            exit_ok = "已是链式节点"
        lines.append("%-3d %-8s %-5s %-12s %-11s %s%s" % (
            index, delay, node["region"] or "?", exit_ok, node["type"], node["name"],
            "  [来自 " + node["provider"] + "]" if node.get("provider") else ""))
    if skipped:
        lines.append("")
        lines.append("已排除的非节点条目：")
        for item in skipped:
            lines.append("  - " + item["name"] + "：" + item["reason"])
    return "\n".join(lines)
