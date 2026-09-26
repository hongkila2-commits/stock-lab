"""휴대폰 접속: 주소 분류 · 휴대폰 판별 · 비밀번호."""
import pytest

from stocklab import netinfo


@pytest.mark.parametrize("ip,kind", [
    ("100.101.102.103", "tailscale"), ("100.64.0.1", "tailscale"), ("100.128.0.1", "other"),
    ("192.168.0.12", "lan"), ("10.0.0.5", "lan"), ("172.16.3.4", "lan"),
    ("127.0.0.1", "local"), ("::1", "local"), ("169.254.1.1", "other"), ("8.8.8.8", "other"),
    ("not-an-ip", "other"),
])
def test_classify(ip, kind):
    assert netinfo.classify(ip) == kind


def test_local_client():
    assert netinfo.is_local_client(None) and netinfo.is_local_client("127.0.0.1")
    assert netinfo.is_local_client("::1")
    assert not netinfo.is_local_client("100.101.102.103")
    assert not netinfo.is_local_client("192.168.0.20")


def test_is_mobile():
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
    galaxy = "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
    desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    assert netinfo.is_mobile(iphone) and netinfo.is_mobile(galaxy)
    assert not netinfo.is_mobile(desktop) and not netinfo.is_mobile(None)


def test_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "주식123")
    assert netinfo.password_ok("주식123")
    assert not netinfo.password_ok("주식12") and not netinfo.password_ok("")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    assert not netinfo.password_ok("")          # 비밀번호 미설정이면 어떤 입력도 통과 못 함


def test_dashboard_url_prefers_setting_then_tailscale(monkeypatch):
    monkeypatch.setenv("DASHBOARD_URL", "")
    monkeypatch.setattr(netinfo, "local_ips", lambda: ["100.90.1.2", "192.168.0.5"])
    assert netinfo.dashboard_url() == "http://100.90.1.2:8501"
    monkeypatch.setattr(netinfo, "local_ips", lambda: ["192.168.0.5"])
    assert netinfo.dashboard_url() == "http://localhost:8501"
    monkeypatch.setenv("DASHBOARD_URL", "http://my-pc:8501")
    assert netinfo.dashboard_url() == "http://my-pc:8501"


def test_local_ips_only_reachable_kinds():
    for ip in netinfo.local_ips():
        assert netinfo.classify(ip) in ("tailscale", "lan")
