"""网络修复 / 重置：刷 DNS、清 ARP、重置代理、重启网卡协议栈。

全部基于 Windows 自带命令（ipconfig / netsh）和注册表，不装任何东西。
"""
from __future__ import annotations

import ctypes
import http.client
import locale
import re
import socket
import ssl
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

Log = Callable[[str], None]

_iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
_wininet = ctypes.WinDLL("wininet", use_last_error=True)

CREATE_NO_WINDOW = 0x08000000


# ------------------------------------------------------------- 网卡信息

class IP_ADDR_STRING(ctypes.Structure):
    pass


IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", wintypes.DWORD),
]


class IP_ADAPTER_INFO(ctypes.Structure):
    pass


IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(IP_ADAPTER_INFO)),
    ("ComboIndex", wintypes.DWORD),
    ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132),
    ("AddressLength", wintypes.UINT),
    ("Address", ctypes.c_ubyte * 8),
    ("Index", wintypes.DWORD),
    ("Type", wintypes.UINT),
    ("DhcpEnabled", wintypes.UINT),
    ("CurrentIpAddress", ctypes.POINTER(IP_ADDR_STRING)),
    ("IpAddressList", IP_ADDR_STRING),
    ("GatewayList", IP_ADDR_STRING),
    ("DhcpServer", IP_ADDR_STRING),
    ("HaveWins", wintypes.BOOL),
    ("PrimaryWinsServer", IP_ADDR_STRING),
    ("SecondaryWinsServer", IP_ADDR_STRING),
    ("LeaseObtained", wintypes.DWORD),
    ("LeaseExpires", wintypes.DWORD),
]


class FIXED_INFO(ctypes.Structure):
    _fields_ = [
        ("HostName", ctypes.c_char * 132),
        ("DomainName", ctypes.c_char * 132),
        ("CurrentDnsServer", ctypes.POINTER(IP_ADDR_STRING)),
        ("DnsServerList", IP_ADDR_STRING),
        ("NodeType", wintypes.UINT),
        ("ScopeId", ctypes.c_char * 260),
        ("EnableRouting", wintypes.UINT),
        ("EnableProxy", wintypes.UINT),
        ("EnableDns", wintypes.UINT),
    ]


def _decode(raw: bytes) -> str:
    try:
        return raw.split(b"\x00")[0].decode(locale.getpreferredencoding(False), "replace").strip()
    except Exception:
        return ""


def network_status() -> dict[str, str]:
    """读取本机 IPv4 / 网关 / DNS / DHCP 状态。"""
    info: dict[str, str] = {}
    size = wintypes.ULONG(0)
    try:
        _iphlp.GetAdaptersInfo(None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if _iphlp.GetAdaptersInfo(buf, ctypes.byref(size)) != 0:
            return {"状态": "读取网卡信息失败"}
        adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
        first = True
        while adapter:
            a = adapter.contents
            ip = _decode(a.IpAddressList.IpAddress)
            if ip and ip != "0.0.0.0":
                prefix = "" if first else f"#{a.Index} "
                info[f"{prefix}网卡"] = _decode(a.Description)
                info[f"{prefix}IPv4"] = ip
                info[f"{prefix}网关"] = _decode(a.GatewayList.IpAddress) or "（无）"
                info[f"{prefix}IP 获取方式"] = "DHCP 自动" if a.DhcpEnabled else "手动设置"
                first = False
            adapter = a.Next
    except Exception as exc:
        info["状态"] = f"读取失败：{exc}"

    try:
        fsize = wintypes.ULONG(0)
        _iphlp.GetNetworkParams(None, ctypes.byref(fsize))
        fbuf = ctypes.create_string_buffer(fsize.value)
        if _iphlp.GetNetworkParams(fbuf, ctypes.byref(fsize)) == 0:
            fixed = ctypes.cast(fbuf, ctypes.POINTER(FIXED_INFO)).contents
            dns: list[str] = []
            node = ctypes.pointer(fixed.DnsServerList)
            while node and len(dns) < 4:
                value = _decode(node.contents.IpAddress)
                if value and value not in ("0.0.0.0", "::1"):
                    dns.append(value)
                node = node.contents.Next
            info["DNS 服务器"] = "、".join(dns) if dns else "（未配置）"
            info["主机名"] = _decode(fixed.HostName)
    except Exception:
        pass

    info["系统代理"] = _proxy_state()
    return info


def _proxy_state() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as k:
            try:
                server = str(winreg.QueryValueEx(k, "ProxyServer")[0])
            except OSError:
                server = ""
            try:
                enabled = int(winreg.QueryValueEx(k, "ProxyEnable")[0])
            except OSError:
                enabled = 0
            try:
                auto = str(winreg.QueryValueEx(k, "AutoConfigURL")[0])
            except OSError:
                auto = ""
        if auto:
            return f"自动配置脚本 {auto}"
        if enabled and server:
            return f"手动代理 {server}"
        return "直连（未启用代理）"
    except OSError:
        return "读取失败"


def test_connectivity(timeout: float = 2.5) -> list[str]:
    """简单连通性自检：DNS 解析 + 直连 IP。"""
    lines: list[str] = []
    try:
        ip = socket.gethostbyname("www.baidu.com")
        lines.append(f"DNS 解析：正常（www.baidu.com → {ip}）")
    except Exception as exc:
        lines.append(f"DNS 解析：失败（{exc}）")
    try:
        with socket.create_connection(("223.5.5.5", 53), timeout=timeout):
            lines.append("外网连通：正常（223.5.5.5:53 可达）")
    except Exception as exc:
        lines.append(f"外网连通：失败（{exc}）")
    try:
        with socket.create_connection(("127.0.0.1", 80), timeout=0.2):
            pass
    except Exception:
        pass
    return lines


# ------------------------------------------------------------- 修复动作


@dataclass
class Action:
    key: str
    title: str
    desc: str
    command: list[str] | None = None
    needs_admin: bool = True
    needs_reboot: bool = False
    safe: bool = True


ACTIONS: list[Action] = [
    Action("flushdns", "刷新 DNS 缓存", "网页改了地址后还打不开、域名解析错乱时用，最常用也最安全",
           ["ipconfig", "/flushdns"], needs_admin=False),
    Action("arp", "清空 ARP 缓存", "局域网 IP 冲突、换了路由器后上不了网时用",
           ["netsh", "interface", "ip", "delete", "arpcache"], needs_admin=True),
    Action("winhttp", "重置 WinHTTP 代理", "系统级代理被软件改乱、更新/应用商店连不上时用",
           ["netsh", "winhttp", "reset", "proxy"], needs_admin=True),
    Action("ieproxy", "关闭手动代理（恢复直连）", "浏览器所有网站都打不开，但 QQ/微信能上网，通常是代理设置被劫持",
           None, needs_admin=False),
    Action("renew", "重新获取 IP 地址", "IP 冲突、DHCP 租约失效时用；执行中会短暂断网 5~15 秒",
           ["ipconfig", "/renew"], needs_admin=True),
    Action("dnsauto", "DNS 改为自动获取", "DNS 被设成奇怪的地址导致解析失败；会恢复成 DHCP 下发（对每个有网关的网卡尝试）",
           None, needs_admin=True),
    Action("winsock", "重置 Winsock 目录", "能连 Wi-Fi 但完全上不了网、IE/浏览器报 socket 错误时的终极手段；需要重启电脑",
           ["netsh", "winsock", "reset"], needs_admin=True, needs_reboot=True),
    Action("tcpip", "重置 TCP/IP 协议栈", "网络时断时续、连不上任何网站时用；需要重启电脑",
           ["netsh", "int", "ip", "reset"], needs_admin=True, needs_reboot=True),
]

QUICK_KEYS = ["flushdns", "arp", "winhttp", "ieproxy"]


def by_key(key: str) -> Action | None:
    for a in ACTIONS:
        if a.key == key:
            return a
    return None


def _decode_out(raw: bytes) -> str:
    """netsh / ipconfig 的输出用的是控制台代码页，优先按 OEM 解码。"""
    for enc in ("oem", "mbcs", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("oem", "replace")


def _run(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        raw = (proc.stdout or b"") + (proc.stderr or b"")
        return proc.returncode, _decode_out(raw).strip()
    except FileNotFoundError:
        return 1, f"找不到命令：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, "命令执行超时"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _reset_ie_proxy(log: Log) -> tuple[bool, str]:
    """把系统/IE 代理关掉，并通知 WinINet 立即生效。"""
    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            for name in ("ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"
    # 通知系统代理设置已变化
    INTERNET_OPTION_SETTINGS_CHANGED = 39
    INTERNET_OPTION_REFRESH = 37
    for opt in (INTERNET_OPTION_SETTINGS_CHANGED, INTERNET_OPTION_REFRESH):
        try:
            _wininet.InternetSetOptionW(None, opt, None, 0)
        except Exception:
            pass
    log("  已关闭手动代理并删除自动配置脚本")
    return True, "代理已恢复为直连"


def _adapter_names() -> list[str]:
    """拿到有网关的网卡「友好名称」候选（GetAdaptersInfo 给的是描述）。"""
    names: list[str] = []
    size = wintypes.ULONG(0)
    try:
        _iphlp.GetAdaptersInfo(None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if _iphlp.GetAdaptersInfo(buf, ctypes.byref(size)) != 0:
            return names
        adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
        while adapter:
            a = adapter.contents
            ip = _decode(a.IpAddressList.IpAddress)
            desc = _decode(a.Description)
            if ip and ip != "0.0.0.0" and desc:
                names.append(desc)
            adapter = a.Next
    except Exception:
        pass
    return names


def _set_dns_auto(log: Log) -> tuple[bool, str]:
    """把网卡 DNS 恢复成自动获取；接口名在中文系统里可能是「以太网」或显卡驱动的描述。"""
    candidates = _adapter_names() + ["以太网", "WLAN", "Ethernet", "Wi-Fi", "本地连接"]
    seen: set[str] = set()
    done: list[str] = []
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        code, out = _run(["netsh", "interface", "ipv4", "set", "dnsservers", f"name={name}", "source=dhcp"], 30)
        if code == 0:
            done.append(name)
            log(f"  已把「{name}」的 DNS 设为自动获取")
    if done:
        return True, "、".join(done)
    return False, "没有找到可设置的网卡（可能名称不匹配，需手动在适配器设置里改）"


def run_action(key: str, log: Log = print) -> tuple[bool, str, bool]:
    """执行一个修复动作，返回 (成功, 输出摘要, 是否需重启)。"""
    action = by_key(key)
    if action is None:
        return False, "未知动作", False

    log(f"◆ {action.title}")
    if action.key == "ieproxy":
        ok, msg = _reset_ie_proxy(log)
        return ok, msg, False
    if action.key == "dnsauto":
        ok, msg = _set_dns_auto(log)
        return ok, msg, False

    assert action.command is not None
    code, out = _run(action.command)
    first_lines = [ln for ln in out.splitlines() if ln.strip()][:6]
    for line in first_lines:
        log("  " + line)
    ok = code == 0
    if not ok and first_lines:
        log("  ! 返回码 " + str(code))
    return ok, (first_lines[-1] if first_lines else "完成"), action.needs_reboot


def run_many(keys: list[str], log: Log = print) -> tuple[int, int, bool]:
    """批量执行，返回 (成功数, 失败数, 是否需要重启)。"""
    alive = [k for k in keys if by_key(k)]
    ok_count = fail_count = 0
    reboot = False
    for key in alive:
        ok, _msg, need_reboot = run_action(key, log)
        reboot = reboot or (need_reboot and ok)
        ok_count += 1 if ok else 0
        fail_count += 0 if ok else 1
    log(f"—— 完成：成功 {ok_count} 项，失败 {fail_count} 项" + ("，需要重启电脑才能生效" if reboot else ""))
    return ok_count, fail_count, reboot


# ------------------------------------------------------------- ICMP 延迟测试

class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


_iphlp.IcmpCreateFile.restype = wintypes.HANDLE
_iphlp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
_iphlp.IcmpSendEcho.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.USHORT,
    ctypes.POINTER(IP_OPTION_INFORMATION),
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
]
_iphlp.IcmpSendEcho.restype = wintypes.DWORD

ICMP_STATUS = {
    0: "成功",
    11001: "目标网络不可达",
    11002: "缓冲区太小",
    11003: "目标地址不可达",
    11004: "目标地址无效",
    11005: "目标端口不可达",
    11006: "请求失败",
    11010: "请求超时（被防火墙拦截或主机不在线）",
    11012: "IP 头错误",
    11013: "IP 选项错误",
    11014: "错误请求",
    11015: "坏路由",
    11016: "TTL 过期",
    11017: "报文重组失败",
    11018: "参数错误",
    11050: "一般性失败",
}


@dataclass
class PingResult:
    host: str
    ip: str = ""
    sent: int = 0
    recv: int = 0
    samples: list[int] = None  # type: ignore[assignment]
    error: str = ""

    def __post_init__(self) -> None:
        if self.samples is None:
            self.samples = []

    @property
    def loss(self) -> float:
        return 0.0 if not self.sent else round((self.sent - self.recv) * 100.0 / self.sent, 1)

    @property
    def min_ms(self) -> int | None:
        return min(self.samples) if self.samples else None

    @property
    def max_ms(self) -> int | None:
        return max(self.samples) if self.samples else None

    @property
    def avg_ms(self) -> int | None:
        return round(sum(self.samples) / len(self.samples)) if self.samples else None


class Pinger:
    """一个 ICMP 句柄的封装，可以反复 ping（不需要管理员权限）。"""

    def __init__(self) -> None:
        self._handle = _iphlp.IcmpCreateFile()

    def close(self) -> None:
        try:
            if self._handle:
                _iphlp.IcmpCloseHandle(self._handle)
                self._handle = None
        except Exception:
            pass

    def __enter__(self) -> "Pinger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ping_once(self, ip: str, timeout_ms: int = 1500) -> tuple[int, int, str]:
        """返回 (状态码, 往返毫秒, 状态文字)。"""
        if not self._handle:
            self._handle = _iphlp.IcmpCreateFile()
        try:
            packed = socket.inet_aton(ip)
        except OSError:
            return -1, 0, "IP 地址格式错误"
        addr = wintypes.ULONG(int.from_bytes(packed, "little"))
        payload = b"0123456789abcdef"
        size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(payload) + 40
        buf = ctypes.create_string_buffer(size)
        count = _iphlp.IcmpSendEcho(
            self._handle, addr, payload, len(payload), None, buf, size, timeout_ms
        )
        if not count:
            code = ctypes.get_last_error() or -1
            return code, 0, f"发送失败（错误码 {code}）"
        reply = ctypes.cast(buf, ctypes.POINTER(ICMP_ECHO_REPLY)).contents
        status = int(reply.Status)
        rtt = int(reply.RoundTripTime)
        return status, rtt, ICMP_STATUS.get(status, f"状态码 {status}")

    def resolve(self, host: str) -> str:
        return socket.gethostbyname(host)


def ping_host(host: str, count: int = 4, timeout_ms: int = 1500, interval: float = 0.25) -> PingResult:
    host = host.strip()
    result = PingResult(host=host)
    if not host:
        result.error = "空目标"
        return result
    try:
        result.ip = socket.gethostbyname(host)
    except Exception as exc:  # noqa: BLE001
        result.error = f"域名解析失败：{exc}"
        return result

    with Pinger() as pinger:
        if not pinger._handle:
            result.error = "无法创建 ICMP 句柄"
            return result
        last_status = 0
        for i in range(max(1, count)):
            status, rtt, text = pinger.ping_once(result.ip, timeout_ms)
            result.sent += 1
            if status == 0:
                result.recv += 1
                result.samples.append(rtt)
            else:
                last_status = status
                result.error = text
            if i < count - 1:
                time.sleep(interval)
    if result.recv == 0 and not result.error:
        result.error = ICMP_STATUS.get(last_status, "无响应")
    return result


def gateways() -> list[str]:
    """所有有 IPv4 地址的网卡的网关。"""
    out: list[str] = []
    size = wintypes.ULONG(0)
    try:
        _iphlp.GetAdaptersInfo(None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if _iphlp.GetAdaptersInfo(buf, ctypes.byref(size)) != 0:
            return out
        adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_INFO))
        while adapter:
            a = adapter.contents
            ip = _decode(a.IpAddressList.IpAddress)
            gw = _decode(a.GatewayList.IpAddress)
            if ip and ip != "0.0.0.0" and gw and gw != "0.0.0.0":
                out.append(gw)
            adapter = a.Next
    except Exception:
        pass
    return out


def default_targets() -> list[str]:
    """默认测试目标：网关 + 公共 DNS + 常见站点。"""
    targets: list[str] = []
    for gw in gateways():
        if gw not in targets:
            targets.append(gw)
    for extra in ("223.5.5.5", "119.29.29.29", "www.baidu.com"):
        if extra not in targets:
            targets.append(extra)
    return targets


def tcp_probe(host: str, port: int = 443, timeout: float = 1.5) -> int | None:
    """TCP 连接探测，返回毫秒延迟；失败返回 None。"""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.perf_counter() - start) * 1000)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------- 目标解析（URL 也认）

@dataclass
class Target:
    raw: str
    host: str
    scheme: str = ""
    port: int | None = None
    path: str = ""

    @property
    def has_url(self) -> bool:
        return bool(self.scheme)

    @property
    def default_port(self) -> int:
        if self.port:
            return self.port
        return 443 if self.scheme == "https" else 80

    def __str__(self) -> str:
        return self.host


_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def parse_target(text: str) -> Target:
    """把「https://host/v1」「host:8080」「1.2.3.4」统一解析成主机+端口+路径。

    用户经常把整条接口地址粘进来，这里必须容错，否则 getaddrinfo 会直接报
    Errno 11001（这正是旧版本的表现）。
    """
    raw = text.strip()
    work = raw
    if not _URL_RE.match(work):
        if "/" in work or "?" in work:
            work = "http://" + work
        elif work.count(":") == 1 and work.rsplit(":", 1)[1].isdigit():
            work = "http://" + work

    if _URL_RE.match(work):
        parsed = urlparse(work)
        host = parsed.hostname or ""
        try:
            port = parsed.port
        except ValueError:
            port = None
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return Target(raw=raw, host=host, scheme=parsed.scheme.lower(), port=port, path=path)

    return Target(raw=raw, host=raw)


def http_probe(target: Target, timeout: float = 5.0) -> tuple[bool, int | None, int | None, str]:
    """对完整 URL 发一次 GET，返回 (是否拿到响应, 状态码, 毫秒, 错误信息)。

    401/403/404 都算「通」——服务端回了话，说明链路、端口、TLS 都没问题。
    """
    if not target.has_url or not target.host:
        return False, None, None, "不是完整 URL"
    scheme = target.scheme if target.scheme in ("http", "https") else "https"
    port = target.port
    path = target.path or "/"
    context = ssl._create_unverified_context() if scheme == "https" else None
    start = time.perf_counter()
    try:
        if scheme == "https":
            conn = http.client.HTTPSConnection(target.host, port, timeout=timeout, context=context)
        else:
            conn = http.client.HTTPConnection(target.host, port, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0 BrowserCleaner"})
            response = conn.getresponse()
            code = response.status
            try:
                response.read(256)
            except Exception:
                pass
            return True, code, round((time.perf_counter() - start) * 1000), ""
        finally:
            conn.close()
    except ssl.SSLError as exc:
        return False, None, None, f"TLS 握手失败：{exc}"
    except socket.timeout:
        return False, None, None, "请求超时"
    except Exception as exc:  # noqa: BLE001
        return False, None, None, str(exc)
