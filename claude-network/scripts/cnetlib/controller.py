"""mihomo 控制器访问：macOS 用 Unix socket，Windows 用命名管道；不额外开放 TCP 端口。

Clash Verge 服务模式下实际管道名可能和 config.yaml 里写的不同
（配置写 verge-mihomo-sidecar-release-<hash>，服务实际是 verge-mihomo-production-<hash>），
所以按前缀枚举，优先同哈希后缀的那个。
"""

import glob
import json
import os
import re
import socket
import threading
import time

from . import util, verge


class ControllerError(RuntimeError):
    pass


def discover(state=None):
    """返回 ("unix"|"pipe"|"tcp", 地址, secret)。"""
    state = state or verge.read_state()
    paths = verge.files(state.get("dir"))
    config_text = util.read_text(paths["config"], "") or ""
    secret_match = re.search(r"^secret:\s*(.*)$", config_text, re.M)
    secret = secret_match.group(1).strip().strip("'\"") if secret_match else ""
    if util.IS_WINDOWS:
        configured = state.get("controller_pipe") or ""
        suffix = configured.rsplit("-", 1)[-1] if configured else ""
        try:
            names = os.listdir("\\\\.\\pipe\\")
        except OSError:
            names = []
        candidates = [name for name in names if name.startswith("verge-mihomo")]
        candidates.sort(key=lambda name: (not name.endswith(suffix) if suffix else False, "production" not in name))
        if candidates:
            return "pipe", "\\\\.\\pipe\\" + candidates[0], secret
        if configured:
            return "pipe", configured, secret
        raise ControllerError("找不到 verge-mihomo 命名管道：客户端或内核没有在运行")
    configured = state.get("controller_unix") or ""
    candidates = []
    if configured:
        candidates.append(configured)
    candidates += sorted(glob.glob("/tmp/verge/verge-mihomo*.sock"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return "unix", candidate, secret
    raise ControllerError("找不到 /tmp/verge/verge-mihomo*.sock：客户端或内核没有在运行")


class Controller:
    def __init__(self, kind=None, address=None, secret=None, timeout=15):
        if kind is None:
            kind, address, secret = discover()
        self.kind, self.address, self.secret, self.timeout = kind, address, secret or "", timeout

    # ---- 传输层 ----
    def _open(self, timeout):
        if self.kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.address)
            return _SocketStream(sock)
        if self.kind == "pipe":
            deadline = time.time() + timeout
            while True:
                try:
                    handle = open(self.address, "r+b", buffering=0)  # noqa: SIM115
                    return _PipeStream(handle)
                except OSError as error:
                    # ERROR_PIPE_BUSY：所有实例都忙，稍后重试
                    if time.time() > deadline:
                        raise ControllerError("连接命名管道失败：" + str(error)) from error
                    time.sleep(0.1)
        raise ControllerError("不支持的控制器类型：" + str(self.kind))

    def _request_bytes(self, method, path, body):
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        head = method + " " + path + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
        if self.secret:
            head += "Authorization: Bearer " + self.secret + "\r\n"
        if body is not None:
            head += "Content-Type: application/json\r\n"
        head += "Content-Length: " + str(len(payload)) + "\r\n\r\n"
        return head.encode("utf-8") + payload

    def request(self, method, path, body=None, timeout=None):
        timeout = timeout or self.timeout
        stream = self._open(timeout)
        try:
            stream.write(self._request_bytes(method, path, body))
            raw = stream.read_all(timeout)
        finally:
            stream.close()
        status, headers, content = _parse_response(raw)
        text = content.decode("utf-8", errors="replace")
        data = None
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError:
                data = text
        return status, data

    def get(self, path, timeout=None):
        status, data = self.request("GET", path, timeout=timeout)
        if status >= 400:
            raise ControllerError("GET " + path + " → HTTP " + str(status) + " " + str(data)[:200])
        return data

    # ---- 常用接口 ----
    def version(self):
        return self.get("/version")

    def configs(self):
        return self.get("/configs")

    def proxies(self):
        return (self.get("/proxies") or {}).get("proxies", {})

    def provider_proxies(self):
        return (self.get("/providers/proxies") or {}).get("providers", {})

    def rules(self):
        return (self.get("/rules") or {}).get("rules", [])

    def rule_providers(self):
        return (self.get("/providers/rules") or {}).get("providers", {})

    def delay(self, name, url="https://www.gstatic.com/generate_204", timeout_ms=5000):
        path = "/proxies/" + util.url_path_escape(name) + "/delay?timeout=" + str(timeout_ms) + "&url=" + util.url_path_escape(url)
        status, data = self.request("GET", path, timeout=timeout_ms / 1000 + 5)
        if status == 200 and isinstance(data, dict) and data.get("delay"):
            return int(data["delay"]), None
        message = data.get("message") if isinstance(data, dict) else str(data)
        return None, "HTTP " + str(status) + " " + str(message)[:120]

    def select(self, group, name):
        status, data = self.request("PUT", "/proxies/" + util.url_path_escape(group), {"name": name})
        if status not in (200, 204):
            raise ControllerError("切换 " + group + " → " + name + " 失败：HTTP " + str(status) + " " + str(data))

    def connections(self):
        return (self.get("/connections") or {}).get("connections", []) or []

    def log_stream(self, level="info"):
        return LogRecorder(self, level)


def _parse_response(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    try:
        status = int(lines[0].split(" ")[1])
    except (IndexError, ValueError) as error:
        raise ControllerError("控制器返回了无法解析的响应：" + repr(raw[:80])) from error
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _dechunk(body)
    return status, headers, body


def _dechunk(body):
    out = bytearray()
    while body:
        size_line, _, rest = body.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out += rest[:size]
        body = rest[size + 2:]
    return bytes(out)


class _SocketStream:
    def __init__(self, sock):
        self.sock = sock

    def write(self, data):
        self.sock.sendall(data)

    def read_some(self):
        return self.sock.recv(65536)

    def read_all(self, timeout):
        self.sock.settimeout(timeout)
        chunks = []
        while True:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _PipeStream:
    def __init__(self, handle):
        self.handle = handle

    def write(self, data):
        self.handle.write(data)

    def read_some(self):
        return self.handle.read(65536)

    def read_all(self, timeout):
        chunks = []
        while True:
            try:
                data = self.handle.read(65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)

    def close(self):
        try:
            self.handle.close()
        except OSError:
            pass


LOG_RE = re.compile(r"\[(?P<net>TCP|UDP)\]\s+(?P<src>\S+)\s+-->\s+(?P<dst>\S+)\s+match\s+(?P<rule>.+?)\s+using\s+(?P<chain>.+)$")


class LogRecorder:
    """后台线程录 /logs 流（核心的连接日志），同时每 300ms 采一次 /connections 兜底。"""

    def __init__(self, controller, level="info"):
        self.controller = controller
        self.level = level
        self.lines = []
        self.connections = {}
        self._stop = threading.Event()
        self._threads = []
        self.errors = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        for target in (self._logs, self._poll):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        time.sleep(0.8)

    def stop(self):
        time.sleep(1.0)
        self._stop.set()

    def _logs(self):
        try:
            stream = self.controller._open(10)
            request = "GET /logs?level=" + self.level + " HTTP/1.1\r\nHost: localhost\r\n"
            if self.controller.secret:
                request += "Authorization: Bearer " + self.controller.secret + "\r\n"
            stream.write((request + "\r\n").encode("utf-8"))
            buffer = b""
            while not self._stop.is_set():
                data = stream.read_some()
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if text.startswith("{"):
                        try:
                            item = json.loads(text)
                            self.lines.append(str(item.get("payload", "")))
                        except ValueError:
                            pass
            stream.close()
        except Exception as error:  # noqa: BLE001
            self.errors.append("logs: " + str(error))

    def _poll(self):
        while not self._stop.is_set():
            try:
                for item in self.controller.connections():
                    meta = item.get("metadata", {})
                    host = meta.get("host") or meta.get("sniffHost") or meta.get("destinationIP")
                    key = item.get("id")
                    self.connections[key] = {
                        "host": host,
                        "dst_ip": meta.get("destinationIP"),
                        "port": meta.get("destinationPort"),
                        "network": meta.get("network"),
                        "process": meta.get("process"),
                        "rule": item.get("rule"),
                        "payload": item.get("rulePayload"),
                        "chains": item.get("chains"),
                    }
            except Exception as error:  # noqa: BLE001
                if len(self.errors) < 5:
                    self.errors.append("connections: " + str(error))
            self._stop.wait(0.3)

    def evidence(self, host):
        """返回这个主机名（或 IP）在日志/连接里的命中记录列表。"""
        hits = []
        needle = host.lower()
        for line in self.lines:
            match = LOG_RE.search(line)
            if match and match.group("dst").lower().rsplit(":", 1)[0] == needle:
                hits.append({"source": "log", "rule": match.group("rule"), "chain": match.group("chain"), "line": line})
        for conn in self.connections.values():
            if str(conn.get("host") or "").lower() == needle or str(conn.get("dst_ip") or "") == host:
                chains = conn.get("chains") or []
                hits.append({"source": "connections", "rule": str(conn.get("rule")) + "(" + str(conn.get("payload")) + ")",
                             "chain": " ← ".join(chains), "process": conn.get("process")})
        return hits
