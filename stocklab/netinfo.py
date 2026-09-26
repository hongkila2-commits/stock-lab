"""휴대폰 접속용: PC 의 네트워크 주소 찾기 · 휴대폰 판별 · 비밀번호 확인.

Tailscale(무료 개인 VPN)을 PC 와 휴대폰에 설치하면 둘 다 100.64.0.0/10 대역 주소를 받고,
집 밖(LTE·5G)에서도 그 주소로 PC 에 접속할 수 있다. 인터넷에 공개되지 않고 내 기기끼리만 연결된다.
"""
from __future__ import annotations

import hmac
import ipaddress
import re
import socket

from .config import env

PORT = 8501
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_MOBILE = re.compile(r"Mobile|Android|iPhone|iPad|iPod|SamsungBrowser", re.I)


def classify(ip: str) -> str:
    """'tailscale' · 'lan'(집 와이파이 등 사설망) · 'local'(PC 자신) · 'other'."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "other"
    if a.is_loopback:
        return "local"
    if a.version == 4 and a in TAILSCALE_NET:
        return "tailscale"
    if a.is_link_local:
        return "other"
    if a.is_private:
        return "lan"
    return "other"


def local_ips() -> list[str]:
    """이 PC 의 IPv4 주소들 (Tailscale 먼저, 그다음 사설망)."""
    ips: set[str] = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    for probe in ("8.8.8.8", "100.100.100.100"):     # 실제로 보내지 않고 경로만 확인하는 UDP 요령
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 80))
                ips.add(s.getsockname()[0])
        except OSError:
            pass
    order = {"tailscale": 0, "lan": 1}
    return sorted((i for i in ips if classify(i) in order), key=lambda i: (order[classify(i)], i))


def urls() -> list[tuple[str, str]]:
    """[(설명, 주소)] — 휴대폰에서 열 주소."""
    label = {"tailscale": "집 밖에서도 (Tailscale)", "lan": "같은 와이파이에서"}
    return [(label[classify(i)], f"http://{i}:{PORT}") for i in local_ips()]


def dashboard_url() -> str:
    """카카오 알림 버튼 링크: .env 의 DASHBOARD_URL > Tailscale 주소 > localhost."""
    if env("DASHBOARD_URL"):
        return env("DASHBOARD_URL")
    for ip in local_ips():
        if classify(ip) == "tailscale":
            return f"http://{ip}:{PORT}"
    return f"http://localhost:{PORT}"


def is_local_client(ip: str | None) -> bool:
    """대시보드에 접속한 쪽이 PC 자신인가 (Streamlit 은 localhost 접속이면 None 을 줄 수 있음)."""
    return ip in (None, "", "localhost") or classify(ip) == "local"


def is_mobile(user_agent: str | None) -> bool:
    return bool(user_agent and _MOBILE.search(user_agent))


def password_ok(given: str) -> bool:
    want = env("DASHBOARD_PASSWORD")
    return bool(want) and hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8"))
