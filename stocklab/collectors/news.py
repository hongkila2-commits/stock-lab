"""네이버 뉴스 검색 API 로 종목 뉴스 수집 + 감성 점수.

- 키가 틀렸거나(401), 네이버 앱에 '검색' API 가 빠졌거나(403), 하루 한도를 넘으면(429)
  모든 종목이 같은 이유로 실패하므로 첫 번에 멈추고 사유를 남긴다 (meta.news_status).
- 그 밖의 한 종목 실패(일시 오류)는 건너뛰고 계속한다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .. import db
from ..config import env
from ..sentiment import Scorer, clean

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE = dtime(15, 30)
URL = "https://openapi.naver.com/v1/search/news.json"
MIN_INTERVAL = 0.1                  # 네이버 검색 API 는 초당 10회 제한
MAX_CONSECUTIVE_FAILS = 10

HELP = {
    401: "네이버 Client ID / Client Secret 이 맞지 않습니다. .env 의 NAVER_CLIENT_ID·NAVER_CLIENT_SECRET 을 "
         "네이버 개발자센터 → 내 애플리케이션 값과 비교하세요 (앞뒤 공백·따옴표 없이).",
    403: "이 네이버 애플리케이션에 '검색' API 사용 권한이 없습니다. 네이버 개발자센터 → 내 애플리케이션 → "
         "API 설정 → 사용 API 에 '검색' 을 추가하세요.",
    429: "네이버 검색 API 하루 호출 한도(25,000회)를 넘었습니다. 내일 다시 수집됩니다.",
}


class NaverError(RuntimeError):
    def __init__(self, status: int, detail: str = ""):
        self.status = status
        self.fatal = status in HELP                      # 모든 종목이 같은 이유로 실패하는 오류
        if status == 0:                                  # 연결 실패
            super().__init__(detail)
            return
        msg = HELP.get(status, f"네이버 응답 오류 {status}")
        super().__init__(f"{msg} ({status}{': ' + detail if detail else ''})")


def has_keys() -> bool:
    return bool(env("NAVER_CLIENT_ID") and env("NAVER_CLIENT_SECRET"))


def effective_date(pub: datetime) -> str:
    """장 마감(15:30) 이후 기사는 다음 날 정보로 취급 → 예측 시 미래 정보 누설 방지.
    주말·휴일은 특징값 생성 단계에서 다음 거래일로 밀린다."""
    local = pub.astimezone(KST)
    d = local.date() + (timedelta(days=1) if local.time() >= MARKET_CLOSE else timedelta(0))
    return d.isoformat()


def fetch_news(query: str, display: int = 100, session=requests) -> list[dict]:
    try:
        r = session.get(URL, timeout=10, params={"query": query, "display": min(display, 100), "sort": "date"},
                        headers={"X-Naver-Client-Id": env("NAVER_CLIENT_ID"),
                                 "X-Naver-Client-Secret": env("NAVER_CLIENT_SECRET")})
    except requests.RequestException as e:          # 긴 주소 대신 짧은 사유만
        raise NaverError(0, f"네이버에 연결하지 못했습니다 ({type(e).__name__})") from None
    if r.status_code != 200:
        try:
            detail = r.json().get("errorMessage", "")
        except ValueError:
            detail = r.text[:100]
        raise NaverError(r.status_code, detail)
    return r.json().get("items", [])


def collect_one(conn, code: str, name: str, per_stock: int, scorer: Scorer, session=requests) -> int:
    """한 종목 뉴스를 받아 저장하고 건수를 돌려준다 (대시보드 '지금 뉴스 받기' 도 사용)."""
    items = fetch_news(f'"{name}"', per_stock, session)
    if not items:
        return 0
    titles = [clean(it["title"]) for it in items]
    rows = []
    for it, title, s in zip(items, titles, scorer.score(titles)):
        pub = parsedate_to_datetime(it["pubDate"])
        rows.append({"code": code, "link": it.get("originallink") or it["link"],
                     "pub_ts": pub.astimezone(KST).isoformat(timespec="minutes"),
                     "eff_date": effective_date(pub), "title": title, "sentiment": s})
    db.upsert(conn, "news", pd.DataFrame(rows))
    return len(rows)


def _status(conn, ok: bool, text: str) -> None:
    db.set_meta(conn, "news_status", f"{'ok' if ok else 'error'}|{datetime.now():%Y-%m-%d %H:%M}|{text}")
    conn.commit()


def status(conn) -> dict | None:
    """마지막 수집 결과 {'ok', 'when', 'text'}."""
    v = db.get_meta(conn, "news_status")
    if not v:
        return None
    kind, when, text = (v.split("|", 2) + ["", ""])[:3]
    return {"ok": kind == "ok", "when": when, "text": text}


def update_news(conn, stocks: dict[str, str], per_stock: int = 100, engine: str = "lexicon",
                session=requests, sleep=time.sleep) -> int:
    if not has_keys():
        log.info("  네이버 API 키가 없어 뉴스 수집을 건너뜁니다.")
        return 0
    scorer = Scorer(engine)
    total, ok, streak, last = 0, 0, 0, None
    for i, (code, name) in enumerate(stocks.items(), 1):
        if i > 1:
            sleep(MIN_INTERVAL)
        try:
            n = collect_one(conn, code, name, per_stock, scorer, session)
        except NaverError as e:
            if e.fatal:
                log.error("  뉴스 수집 중단: %s", e)
                _status(conn, False, str(e))
                return total
            last, streak = e, streak + 1
            log.warning("  [%d/%d] %s 뉴스 실패: %s", i, len(stocks), name, e)
        except Exception as e:                       # 연결 끊김·시간 초과 등
            last, streak = e, streak + 1
            log.warning("  [%d/%d] %s 뉴스 실패: %s", i, len(stocks), name, e)
        else:
            total, ok, streak = total + n, ok + 1, 0
            log.info("  [%d/%d] %s 뉴스 %d건", i, len(stocks), name, n)
            if i % 20 == 0:
                conn.commit()
        if streak >= MAX_CONSECUTIVE_FAILS:
            log.error("  %d종목 연속 실패 — 네이버 연결 문제로 보고 뉴스 수집을 멈춥니다: %s", streak, last)
            _status(conn, False, f"연속 실패로 중단: {last}")
            return total
    conn.commit()
    if ok == 0 and stocks:
        _status(conn, False, f"모든 종목 실패: {last}")
    else:
        _status(conn, True, f"{ok}종목 {total}건" + (f" · {len(stocks) - ok}종목 실패" if ok < len(stocks) else ""))
    log.info("  뉴스: %d종목 %d건", ok, total)
    return total
