"""장중 실시간 시세 (realtime.bat = python -m stocklab realtime).

1순위: 한국투자증권 WebSocket 실시간 체결가(H0STCNT0)
2순위: 연결이 계속 실패하면 REST 현재가 조회를 poll_seconds 마다 반복 (자동 전환)

저장: rt_quotes (종목별 최신 체결), rt_bars (1분봉). 대시보드가 이 테이블을 읽는다.
알림: 관심종목이 전일 대비 ±alerts.move_pct 이상이면 카카오톡 (alerts.move_alert 가 중복 방지).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from . import alerts, db, kakao

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
WS_URL = {"paper": "ws://ops.koreainvestment.com:31000", "real": "ws://ops.koreainvestment.com:21000"}
TR_PRICE = "H0STCNT0"            # 국내주식 실시간 체결가
OPEN, CLOSE, STOP = dtime(9, 0), dtime(15, 30), dtime(15, 35)

# H0STCNT0 필드 순서 (앞부분만 사용)
F_CODE, F_TIME, F_PRICE, F_CHG_PCT = 0, 1, 2, 5
F_OPEN, F_HIGH, F_LOW, F_TICK_VOL, F_ACC_VOL, F_ACC_VALUE = 7, 8, 9, 12, 13, 14


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def market_open(t: datetime) -> bool:
    return t.weekday() < 5 and OPEN <= t.time() <= CLOSE


# ── 메시지 파싱 ──────────────────────────────────────────
def parse(text: str) -> tuple[str, object]:
    """('ticks', [dict…]) · ('ping', text) · ('ack', msg) · ('other', text)."""
    if text and text[0] in "01" and "|" in text[:4]:
        enc, tr_id, count, body = text.split("|", 3)
        if tr_id != TR_PRICE or enc == "1":
            return "other", text
        vals = body.split("^")
        n = max(1, int(count))
        width = len(vals) // n
        ticks = []
        for i in range(n):
            v = vals[i * width:(i + 1) * width]
            num = lambda k: pd.to_numeric(v[k], errors="coerce")  # noqa: E731
            ticks.append({"code": v[F_CODE], "hhmmss": v[F_TIME], "price": num(F_PRICE),
                          "change_pct": num(F_CHG_PCT), "open": num(F_OPEN), "high": num(F_HIGH),
                          "low": num(F_LOW), "tick_vol": num(F_TICK_VOL), "volume": num(F_ACC_VOL),
                          "value": num(F_ACC_VALUE)})
        return "ticks", ticks
    try:
        msg = json.loads(text)
    except ValueError:
        return "other", text
    if msg.get("header", {}).get("tr_id") == "PINGPONG":
        return "ping", text
    return "ack", (msg.get("body") or {}).get("msg1", "")


def subscribe_message(approval_key: str, code: str, subscribe: bool = True) -> str:
    return json.dumps({
        "header": {"approval_key": approval_key, "custtype": "P",
                   "tr_type": "1" if subscribe else "2", "content-type": "utf-8"},
        "body": {"input": {"tr_id": TR_PRICE, "tr_key": code}},
    })


# ── 상태·저장 ────────────────────────────────────────────
@dataclass
class Book:
    """최신 체결과 1분봉을 메모리에 모았다가 1초마다 DB 에 한 번에 쓴다."""
    quotes: dict = field(default_factory=dict)
    bars: dict = field(default_factory=dict)
    dirty: set = field(default_factory=set)

    def add(self, t: dict, day: str) -> None:
        code = t["code"]
        hh = str(t["hhmmss"]).zfill(6)
        ts = f"{day} {hh[:2]}:{hh[2:4]}:{hh[4:6]}"
        minute = ts[:16]
        self.quotes[code] = {"code": code, "ts": ts, **{k: t.get(k) for k in
                             ("price", "change_pct", "open", "high", "low", "volume", "value")}}
        key = (code, minute)
        b = self.bars.get(key)
        p = t["price"]
        vol = t.get("tick_vol")
        if b is None:
            self.bars[key] = {"code": code, "minute": minute, "o": p, "h": p, "l": p, "c": p,
                              "v": vol if pd.notna(vol) else 0.0}
        else:
            b["h"], b["l"], b["c"] = max(b["h"], p), min(b["l"], p), p
            b["v"] += vol if pd.notna(vol) else 0.0
        self.dirty.add(key)

    def flush(self, conn) -> list[str]:
        if not self.dirty:
            return []
        codes = sorted({c for c, _ in self.dirty})
        db.upsert(conn, "rt_quotes", pd.DataFrame([self.quotes[c] for c in codes]))
        db.upsert(conn, "rt_bars", pd.DataFrame([self.bars[k] for k in self.dirty]))
        conn.commit()
        self.dirty.clear()
        # 지난 분봉은 메모리에서 정리 (종목별 최근 2개만 유지)
        for code in codes:
            keys = sorted(k for k in self.bars if k[0] == code)
            for k in keys[:-2]:
                del self.bars[k]
        return codes


def targets(conn, s) -> list[str]:
    """구독 대상: 내 관심종목 + AI 추천 상위 (최대 max_stocks)."""
    mine = alerts.my_codes(conn, s)
    h = s.horizons[0]                      # 장중에 볼 추천은 짧은 기간(1주) 기준
    picks = [r[0] for r in conn.execute(
        "SELECT code FROM picks WHERE horizon = ? AND asof = (SELECT MAX(asof) FROM picks WHERE horizon = ?) "
        "ORDER BY rank LIMIT ?", (h, h, int(s.realtime["picks_top"])))]
    return list(dict.fromkeys(mine + picks))[: int(s.realtime["max_stocks"])]


class Runner:
    def __init__(self, conn, kis, s, codes: list[str], clock=now_kst, notify=kakao.notify,
                 ws_module=None, sleep=time.sleep, stop_at: dtime = STOP):
        self.conn, self.kis, self.s, self.codes = conn, kis, s, codes
        self.clock, self.notify, self.sleep, self.stop_at = clock, notify, sleep, stop_at
        self.ws_module = ws_module
        self.book = Book()
        self.mine = set(alerts.my_codes(conn, s))
        self.last_flush = 0.0
        self.mode = "off"

    # 공통
    def _status(self, mode: str | None = None) -> None:
        if mode:
            self.mode = mode
        db.set_meta(self.conn, "rt_mode", self.mode)
        db.set_meta(self.conn, "rt_heartbeat", self.clock().isoformat(timespec="seconds"))
        db.set_meta(self.conn, "rt_count", str(len(self.codes)))
        self.conn.commit()

    def handle_ticks(self, ticks: list[dict], force_flush: bool = False) -> None:
        day = self.clock().strftime("%Y-%m-%d")
        for t in ticks:
            if pd.notna(t.get("price")):
                self.book.add(t, day)
        if force_flush or time.monotonic() - self.last_flush >= 1.0:
            self.flush()

    def flush(self) -> None:
        changed = self.book.flush(self.conn)
        self.last_flush = time.monotonic()
        self._status()
        for code in changed:
            if code not in self.mine:
                continue
            q = self.book.quotes[code]
            text = alerts.move_alert(self.conn, self.s, code, q["change_pct"], q["price"], self.clock())
            if text:
                log.info("  알림: %s", text)
                self.notify(text, bool(self.s.alerts["kakao"]))

    def done(self) -> bool:
        t = self.clock()
        return t.weekday() >= 5 or t.time() >= self.stop_at

    # WebSocket
    def run_websocket(self) -> None:
        ws_mod = self.ws_module
        if ws_mod is None:
            import websocket as ws_mod
        key = self.kis.approval_key()
        url = WS_URL[self.kis.env]
        stop = threading.Event()

        def on_open(ws):
            for c in self.codes:
                ws.send(subscribe_message(key, c))
            log.info("실시간 연결됨 — %d종목 구독", len(self.codes))
            self._status("websocket")

        def on_message(ws, text):
            kind, payload = parse(text)
            if kind == "ticks":
                self.handle_ticks(payload)
            elif kind == "ping":
                ws.send(payload)                 # KIS PINGPONG: 받은 그대로 돌려줘야 연결 유지
                self._status()
            elif kind == "ack" and payload and "SUCCESS" not in str(payload).upper():
                log.warning("  구독 응답: %s", payload)

        def watchdog(ws):
            while not stop.wait(5):
                if self.done():
                    log.info("장 마감 — 실시간 종료")
                    ws.close()
                    return

        def on_error(ws, e):
            if "closed normally" not in str(e):
                log.warning("  실시간 오류: %s", e)

        app = ws_mod.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error)
        threading.Thread(target=watchdog, args=(app,), daemon=True).start()
        try:
            app.run_forever(ping_interval=0)
        finally:
            stop.set()
            self.flush()

    # REST 폴링
    def poll_once(self) -> None:
        ticks = []
        for c in self.codes:
            try:
                q = self.kis.quote(c)
            except Exception as e:
                log.warning("  %s 조회 실패: %s", c, e)
                continue
            prev = self.book.quotes.get(c, {}).get("volume")
            tick_vol = (q["volume"] - prev) if prev is not None and pd.notna(q.get("volume")) else 0
            ticks.append({"code": c, "hhmmss": self.clock().strftime("%H%M%S"), "price": q["price"],
                          "change_pct": q["change_pct"], "open": q.get("open"), "high": q.get("high"),
                          "low": q.get("low"), "tick_vol": max(tick_vol or 0, 0),
                          "volume": q.get("volume"), "value": q.get("value")})
        self.handle_ticks(ticks, force_flush=True)

    def run_polling(self) -> None:
        self._status("poll")
        log.info("REST 조회 방식으로 실행 (%d종목, %s초마다)", len(self.codes), self.s.realtime["poll_seconds"])
        while not self.done():
            started = time.monotonic()
            self.poll_once()
            self.sleep(max(1.0, float(self.s.realtime["poll_seconds"]) - (time.monotonic() - started)))

    def run(self, max_ws_failures: int = 5) -> None:
        failures = 0
        while not self.done() and failures < max_ws_failures:
            try:
                self.run_websocket()
            except Exception as e:
                log.warning("  실시간 연결 실패: %s", e)
            if self.done():
                break
            failures += 1
            wait = min(60, 2 ** failures)
            log.info("  %d초 후 재연결 (%d/%d)", wait, failures, max_ws_failures)
            self.sleep(wait)
        if not self.done():
            self.run_polling()
        self._status("off")


def snapshot(conn, kis, codes: list[str], clock=now_kst) -> int:
    """지금 현재가를 한 번만 조회해 rt_quotes 에 저장 (대시보드 '💹 현재가 받기').
    realtime.bat 이 꺼져 있어도 표·상세에 당일 가격이 보이게 한다. 알림·상태 표시는 건드리지 않는다."""
    book, t = Book(), clock()
    for c in codes:
        try:
            q = kis.quote(c)
        except Exception as e:
            log.warning("  %s 현재가 조회 실패: %s", c, e)
            continue
        if pd.isna(q.get("price")):
            continue
        book.add({"code": c, "hhmmss": t.strftime("%H%M%S"), "price": q["price"],
                  "change_pct": q.get("change_pct"), "open": q.get("open"), "high": q.get("high"),
                  "low": q.get("low"), "tick_vol": 0, "volume": q.get("volume"),
                  "value": q.get("value")}, t.strftime("%Y-%m-%d"))
    return len(book.flush(conn))
