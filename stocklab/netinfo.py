"""휴대폰 접속용: PC 의 네트워크 주소 찾기 · 휴대폰 판별 · 비밀번호 확인.

Tailscale(무료 개인 VPN)을 PC 와 휴대폰에 설치하면 둘 다 100.64.0.0/10 대역 주소를 받고,
집 밖(LTE·5G)에서도 그 주소로 PC 에 접속할 수 있다. 인터넷에 공개되지 않고 내 기기끼리만 연결된다.
"""
from __future__ import annotations

import hmac
import ipaddress
import re
import socket
import subprocess
import sys

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


# ── 이미 실행 중인 대시보드 정리 (dashboard.bat 을 다시 실행할 때) ─────────────
def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def port_owner_pids(port: int, run=_run) -> list[int]:
    """그 포트에서 접속을 기다리는 프로세스 (Windows). 다른 OS 는 빈 목록."""
    if sys.platform != "win32" and run is _run:
        return []
    out = run(["powershell", "-NoProfile", "-Command",
               f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
               "| Select-Object -ExpandProperty OwningProcess -Unique"])
    return sorted({int(x) for x in out.split() if x.isdigit() and int(x) > 0})


def process_name(pid: int, run=_run) -> str:
    out = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).strip()
    return out.split(",")[0].strip('"') if out.startswith('"') else ""


def stop_dashboard(port: int = 8501, run=_run) -> list[int]:
    """port 를 잡고 있는 예전 대시보드(python)를 끝낸다. 끝낸 PID 목록.

    예전 대시보드가 켜져 있으면 새로 실행한 dashboard.bat 은 포트가 막혀 뜨지 못하고, 브라우저는 옛 코드가
    돌고 있는 예전 대시보드로 열린다 (git pull 뒤 ImportError 의 원인). python 이 아닌 프로그램은 건드리지 않는다.
    """
    stopped = []
    for pid in port_owner_pids(port, run):
        if process_name(pid, run).lower().startswith("python"):
            run(["taskkill", "/PID", str(pid), "/F"])
            stopped.append(pid)
    return stopped
