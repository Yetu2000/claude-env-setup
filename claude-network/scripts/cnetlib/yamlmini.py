"""极简 YAML 行级工具（不引入第三方库）。

只处理 Clash Verge 自己用 serde_yaml 写出的块状 YAML：
  - 顶层标量键      key: value
  - 顶层映射里的子键 parent:\n  child: value
  - 子键是列表       parent:\n  child:\n  - a   （serde 风格，列表项与键同缩进）
不处理流式写法（{...} / [...]）里的嵌套修改——遇到时返回错误，由调用方拒绝写入。
修改时保留其他所有行与注释不变。
"""

import re

_KEY_RE = re.compile(r"^(?P<indent> *)(?P<key>[^\s#:'\"][^:#]*?|'[^']*'|\"[^\"]*\"):(?P<rest>\s.*|)$")


class YamlEditError(ValueError):
    pass


def _split(text):
    return text.replace("\r\n", "\n").split("\n")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _is_blank_or_comment(line):
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def _unquote(key):
    key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        return key[1:-1]
    return key


def scalar_text(value):
    """Python 值 → YAML 标量文本。"""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if re.match(r"^[A-Za-z0-9_./:@+-]+$", text) and text.lower() not in ("true", "false", "null", "yes", "no", "on", "off", "~"):
        return text
    return "'" + text.replace("'", "''") + "'"


def parse_scalar(raw):
    raw = raw.strip()
    if raw == "" or raw in ("~", "null"):
        return None
    if " #" in raw and not raw.startswith(("'", '"')):
        raw = raw.split(" #", 1)[0].strip()
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].encode("utf-8").decode("unicode_escape", errors="ignore") if "\\" in raw else raw[1:-1]
    return raw


def _top_key_index(lines, key):
    for index, line in enumerate(lines):
        if _indent(line) != 0 or _is_blank_or_comment(line):
            continue
        match = _KEY_RE.match(line)
        if match and _unquote(match.group("key")) == key:
            return index
    return -1


def _block_end(lines, start, parent_indent):
    """返回 start 行所属块的结束位置（不含）。块内：缩进更深的行、空行/注释，以及 serde 风格的同级列表项。"""
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if _is_blank_or_comment(line):
            index += 1
            continue
        indent = _indent(line)
        if indent > parent_indent:
            index += 1
            continue
        if indent == parent_indent and line.lstrip().startswith("- "):
            index += 1
            continue
        if indent == parent_indent and line.strip() == "-":
            index += 1
            continue
        break
    # 去掉块尾的空行，让插入位置贴着内容
    while index - 1 > start and lines[index - 1].strip() == "":
        index -= 1
    return index


def get_top_scalar(text, key):
    lines = _split(text)
    index = _top_key_index(lines, key)
    if index < 0:
        return None
    return parse_scalar(_KEY_RE.match(lines[index]).group("rest"))


def set_top_scalar(text, key, value):
    lines = _split(text)
    index = _top_key_index(lines, key)
    new_line = key + ": " + scalar_text(value)
    if index < 0:
        if lines and lines[-1] == "":
            lines.insert(len(lines) - 1, new_line)
        else:
            lines.append(new_line)
        return "\n".join(lines)
    rest = _KEY_RE.match(lines[index]).group("rest").strip()
    end = _block_end(lines, index, 0)
    if rest == "" and end > index + 1:
        # 原来是映射或列表：整块替换成标量
        del lines[index + 1:end]
    lines[index] = new_line
    return "\n".join(lines)


def _child_range(lines, parent_index):
    """返回 (子级缩进, 块结束位置)。父级是流式写法时报错。"""
    rest = _KEY_RE.match(lines[parent_index]).group("rest").strip()
    if rest.startswith("{") or rest.startswith("["):
        raise YamlEditError("顶层键使用了流式写法，拒绝行级修改")
    end = _block_end(lines, parent_index, 0)
    child_indent = None
    for index in range(parent_index + 1, end):
        if not _is_blank_or_comment(lines[index]):
            child_indent = _indent(lines[index])
            break
    return (child_indent or 2), end


def _find_child(lines, parent_index, child_indent, end, key):
    for index in range(parent_index + 1, end):
        line = lines[index]
        if _is_blank_or_comment(line) or _indent(line) != child_indent:
            continue
        match = _KEY_RE.match(line)
        if match and _unquote(match.group("key")) == key:
            return index
    return -1


def get_child_scalar(text, parent, key):
    lines = _split(text)
    parent_index = _top_key_index(lines, parent)
    if parent_index < 0:
        return None
    child_indent, end = _child_range(lines, parent_index)
    index = _find_child(lines, parent_index, child_indent, end, key)
    if index < 0:
        return None
    return parse_scalar(_KEY_RE.match(lines[index]).group("rest"))


def set_child(text, parent, key, value):
    """设置 parent 映射下的子键。value 为 list 时写成 serde 风格列表。父键不存在时追加整块。"""
    lines = _split(text)
    parent_index = _top_key_index(lines, parent)
    if isinstance(value, (list, tuple)):
        new_lines_for = lambda pad: [pad + key + ":"] + [pad + "- " + scalar_text(item) for item in value]  # noqa: E731
    else:
        new_lines_for = lambda pad: [pad + key + ": " + scalar_text(value)]  # noqa: E731
    if parent_index < 0:
        block = [parent + ":"] + new_lines_for("  ")
        while lines and lines[-1] == "":
            lines.pop()
        lines.extend(block)
        lines.append("")
        return "\n".join(lines)
    rest = _KEY_RE.match(lines[parent_index]).group("rest").strip()
    if rest not in ("",) and not rest.startswith("#"):
        if rest.startswith("{"):
            raise YamlEditError(parent + " 使用了流式写法，拒绝行级修改")
        raise YamlEditError(parent + " 不是映射")
    child_indent, end = _child_range(lines, parent_index)
    pad = " " * child_indent
    index = _find_child(lines, parent_index, child_indent, end, key)
    replacement = new_lines_for(pad)
    if index < 0:
        lines[end:end] = replacement
        return "\n".join(lines)
    child_end = index + 1
    while child_end < end:
        line = lines[child_end]
        if _is_blank_or_comment(line):
            child_end += 1
            continue
        indent = _indent(line)
        if indent > child_indent or (indent == child_indent and line.lstrip().startswith("-")):
            child_end += 1
            continue
        break
    lines[index:child_end] = replacement
    return "\n".join(lines)


def get_child_list(text, parent, key):
    lines = _split(text)
    parent_index = _top_key_index(lines, parent)
    if parent_index < 0:
        return None
    child_indent, end = _child_range(lines, parent_index)
    index = _find_child(lines, parent_index, child_indent, end, key)
    if index < 0:
        return None
    rest = _KEY_RE.match(lines[index]).group("rest").strip()
    if rest.startswith("["):
        inner = rest.strip("[]")
        return [parse_scalar(item) for item in inner.split(",") if item.strip()]
    items = []
    for line in lines[index + 1:end]:
        if _is_blank_or_comment(line):
            continue
        stripped = line.strip()
        if _indent(line) >= child_indent and stripped.startswith("- "):
            items.append(parse_scalar(stripped[2:]))
        else:
            break
    return items


def map_block_set_all(text, parent, child_key, value):
    """在 parent 映射下的每个子映射里，把 child_key 设成 value（用于 profile_dns_settings.*.enabled）。"""
    lines = _split(text)
    parent_index = _top_key_index(lines, parent)
    if parent_index < 0:
        return text
    rest = _KEY_RE.match(lines[parent_index]).group("rest").strip()
    if rest.startswith("{"):
        if rest in ("{}",):
            return text
        raise YamlEditError(parent + " 使用了流式写法，拒绝行级修改")
    end = _block_end(lines, parent_index, 0)
    for index in range(parent_index + 1, end):
        match = _KEY_RE.match(lines[index])
        if match and _unquote(match.group("key")) == child_key and _indent(lines[index]) >= 4:
            lines[index] = match.group("indent") + child_key + ": " + scalar_text(value)
    return "\n".join(lines)


def parse_verge_profiles(text):
    """解析 profiles.yaml：返回 {"current": uid, "items": [{uid,type,name,file,option:{...}}]}。"""
    lines = _split(text or "")
    result = {"current": None, "items": []}
    current_item = None
    in_option = False
    for line in lines:
        if _is_blank_or_comment(line):
            continue
        indent = _indent(line)
        stripped = line.strip()
        if indent == 0 and not stripped.startswith("- "):
            match = _KEY_RE.match(line)
            in_option = False
            if match and _unquote(match.group("key")) == "current":
                result["current"] = parse_scalar(match.group("rest"))
            continue
        if indent == 0 and stripped.startswith("- "):
            current_item = {"option": {}}
            result["items"].append(current_item)
            in_option = False
            stripped = stripped[2:]
            match = _KEY_RE.match("  " + stripped)
            if match:
                current_item[_unquote(match.group("key"))] = parse_scalar(match.group("rest"))
            continue
        if current_item is None:
            continue
        match = _KEY_RE.match(line)
        if not match:
            continue
        key = _unquote(match.group("key"))
        if indent == 2:
            in_option = key == "option"
            if not in_option:
                current_item[key] = parse_scalar(match.group("rest"))
        elif indent == 4 and in_option:
            current_item["option"][key] = parse_scalar(match.group("rest"))
    return result


def is_effectively_empty_yaml(text):
    """扩展文件可能只有注释——读出来是空，不能当字典用。"""
    for line in _split(text or ""):
        if not _is_blank_or_comment(line):
            return False
    return True


def top_level_keys(text):
    keys = []
    for line in _split(text or ""):
        if _indent(line) == 0 and not _is_blank_or_comment(line) and not line.startswith("-"):
            match = _KEY_RE.match(line)
            if match:
                keys.append(_unquote(match.group("key")))
    return keys
