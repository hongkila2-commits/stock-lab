"""실시간 시세: 메시지 파싱 · 1분봉 · 급등락 알림 · WebSocket 흐름 · 폴링 전환 (네트워크 없음)."""
from datetime import datetime, time as dtime

import pandas as pd
import pytest

from stocklab import db, realtime
from stocklab.config import load_settings


def frame(*ticks):
    """KIS 실시간 체결가 프레임: 0|H0STCNT0|건수|46필드^… (여러 건이 이어짐)."""
    body = []
    for code, hhmmss, price, pct, vol, acc in ticks:
        f = ["0"] * 46
        f[0], f[1], f[2], f[5] = code, hhmmss, str(price), str(pct)
        f[7], f[8], f[9] = str(price - 100), str(price + 200), str(price - 300)
        f[12], f[13], f[14] = str(vol), str(acc), str(acc * price)
        body += f
    return f"0|H0STCNT0|{len(ticks):03d}|" + "^".join(body)


def test_parse_multi_record_frame():
    kind, ticks = realtime.parse(frame(("005930", "093001", 71500, 1.2, 10, 1000),
                                       ("000660", "093001", 180000, -3.4, 5, 500)))
    assert kind == "ticks" and [t["code"] for t in ticks] == ["005930", "000660"]
    assert ticks[1]["price"] == 180000 and ticks[1]["change_pct"] == pytest.approx(-3.4)
    assert ticks[0]["tick_vol"] == 10 and ticks[0]["volume"] == 1000


def test_parse_ping_and_ack():
    ping = '{"header":{"tr_id":"PINGPONG","datetime":"20260925093000"}}'
    assert realtime.parse(ping) == ("ping", ping)
    ack = '{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0","msg1":"SUBSCRIBE SUCCESS"}}'
    assert realtime.parse(ack) == ("ack", "SUBSCRIBE SUCCESS")


def test_minute_bars():
    b = realtime.Book()
    for hh, p, v in [("090001", 100, 1), ("090030", 105, 2), ("090059", 98, 3), ("090100", 99, 4)]:
        b.add({"code": "A", "hhmmss": hh, "price": p, "change_pct": 0, "tick_vol": v}, "2026-09-25")
    first = b.bars[("A", "2026-09-25 09:00")]
    assert (first["o"], first["h"], first["l"], first["c"], first["v"]) == (100, 105, 98, 98, 6)
    assert b.bars[("A", "2026-09-25 09:01")]["o"] == 99


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "rt.sqlite")
    c.execute("INSERT INTO user_watchlist VALUES ('005930', 'x')")
    c.execute("INSERT INTO stocks VALUES ('005930', '삼성전자', 'user')")
    c.commit()
    return c


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_move_alert_and_cooldown(conn):
    s = load_settings()
    sent = []
    clock = Clock(datetime(2026, 9, 25, 10, 0))
    r = realtime.Runner(conn, None, s, ["005930"], clock=clock, notify=lambda t, on: sent.append(t))
    r.handle_ticks([{"code": "005930", "hhmmss": "100000", "price": 73000, "change_pct": 2.9,
                     "tick_vol": 1}], force_flush=True)
    assert sent == []                                     # 3% 미만
    r.handle_ticks([{"code": "005930", "hhmmss": "100010", "price": 74000, "change_pct": 3.2,
                     "tick_vol": 1}], force_flush=True)
    assert sent == ["[급등] 삼성전자 +3.20% (74,000원) 10:00"]
    clock.t = datetime(2026, 9, 25, 10, 30)
    r.handle_ticks([{"code": "005930", "hhmmss": "103000", "price": 75000, "change_pct": 4.5,
                     "tick_vol": 1}], force_flush=True)
    assert len(sent) == 1                                 # 60분 안에 같은 방향은 한 번만
    r.handle_ticks([{"code": "005930", "hhmmss": "103010", "price": 68000, "change_pct": -3.5,
                     "tick_vol": 1}], force_flush=True)
    assert sent[-1].startswith("[급락] 삼성전자 -3.50%")  # 반대 방향은 따로
    q = db.query(conn, "SELECT * FROM rt_quotes").iloc[0]
    assert q["price"] == 68000 and q["ts"] == "2026-09-25 10:30:10"


class FakeApp:
    """websocket-client 의 WebSocketApp 흉내: run_forever 가 콜백을 차례로 부른다."""
    script: list = []
    sent: list = []

    def __init__(self, url, on_open, on_message, on_error):
        self.url, self.on_open, self.on_message = url, on_open, on_message

    def send(self, text):
        FakeApp.sent.append(text)

    def close(self):
        pass

    def run_forever(self, ping_interval=0):
        self.on_open(self)
        for msg in FakeApp.script:
            self.on_message(self, msg)


class FakeWS:
    WebSocketApp = FakeApp


class FakeKis:
    env = "paper"

    def approval_key(self):
        return "APPROVAL"

    def quote(self, code):
        return {"price": 70000, "change_pct": -1.0, "open": 71000, "high": 71500, "low": 69900,
                "volume": 12345, "value": 9}


def test_websocket_flow_subscribes_pongs_and_stores(conn):
    ping = '{"header":{"tr_id":"PINGPONG"}}'
    FakeApp.script = [ping, frame(("005930", "093001", 71500, 1.2, 10, 1000))]
    FakeApp.sent = []
    r = realtime.Runner(conn, FakeKis(), load_settings(), ["005930", "000660"],
                        clock=Clock(datetime(2026, 9, 25, 9, 30)), notify=lambda *a: None,
                        ws_module=FakeWS)
    r.run_websocket()
    subs = [m for m in FakeApp.sent if "APPROVAL" in m]
    assert len(subs) == 2 and '"tr_key": "000660"' in subs[1]
    assert ping in FakeApp.sent                          # PINGPONG 을 되돌려 보냄
    assert db.query(conn, "SELECT price FROM rt_quotes")["price"].tolist() == [71500]
    assert db.get_meta(conn, "rt_mode") == "websocket"


def test_falls_back_to_polling_after_failures(conn):
    class Broken:
        class WebSocketApp:
            def __init__(self, *a, **k):
                raise ConnectionError("refused")

    clock = Clock(datetime(2026, 9, 25, 9, 30))
    sleeps = []

    def sleep(sec):
        sleeps.append(sec)
        if len(sleeps) > 3:                               # 폴링 한 바퀴 뒤 장 마감으로
            clock.t = datetime(2026, 9, 25, 15, 40)

    r = realtime.Runner(conn, FakeKis(), load_settings(), ["005930"], clock=clock,
                        notify=lambda *a: None, ws_module=Broken, sleep=sleep)
    r.run(max_ws_failures=3)
    assert sleeps[:3] == [2, 4, 8]                        # 점점 길게 기다렸다 재시도
    assert db.query(conn, "SELECT price FROM rt_quotes")["price"].tolist() == [70000]
    assert db.get_meta(conn, "rt_mode") == "off"


def test_market_hours():
    assert realtime.market_open(datetime(2026, 9, 25, 9, 0))
    assert not realtime.market_open(datetime(2026, 9, 25, 15, 31))
    assert not realtime.market_open(datetime(2026, 9, 26, 10, 0))   # 토요일


def test_snapshot_stores_quotes_without_alert_or_status(conn):
    n = realtime.snapshot(conn, FakeKis(), ["005930", "000660"],
                          clock=lambda: datetime(2026, 9, 25, 10, 15, 30))
    assert n == 2
    q = db.query(conn, "SELECT * FROM rt_quotes ORDER BY code")
    assert q["price"].tolist() == [70000, 70000] and q["ts"].iloc[0] == "2026-09-25 10:15:30"
    assert db.get_meta(conn, "rt_mode") is None                  # 실시간 상태는 그대로
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
