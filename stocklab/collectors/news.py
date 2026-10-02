"""네이버 뉴스 검색 API 로 종목 뉴스 수집 + 감성 점수.

네이버 검색 API 는 개발자센터(developers.naver.com)에서 NAVER API HUB(네이버 클라우드)로 옮겨가는 중이다
(개발자센터 신규 발급 2026-07-31 종료, 기존 키 2027-06-30 까지). 두 곳은 주소·헤더가 다르고 키도 서로 안 통한다.
→ API HUB 로 먼저 부르고 401 이면 개발자센터로 한 번 더 시도해, 되는 쪽을 기억한다. .env 는 그대로
  NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (어느 곳에서 받은 키든).

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
# 이름, 주소, (ID 헤더, Secret 헤더) — 앞의 것부터 시도
PLATFORMS = {
    "hub": ("NAVER API HUB", "https://naverapihub.apigw.ntruss.com/search/v1/news",
            ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")),
    "developers": ("네이버 개발자센터", "https://openapi.naver.com/v1/search/news.json",
                   ("X-Naver-Client-Id", "X-Naver-Client-Secret")),
}
_platform: str | None = None        # 키가 통한 곳 (한 번 확인되면 그쪽만 호출)
MIN_INTERVAL = 0.1                  # 네이버 검색 API 는 초당 10회 제한
MAX_CONSECUTIVE_FAILS = 10

HELP = {
    401: "네이버 Client ID / Client Secret 이 맞지 않습니다 (NAVER API HUB·개발자센터 둘 다 거절). "
         ".env 의 NAVER_CLIENT_ID·NAVER_CLIENT_SECRET 을 키를 받은 곳(NAVER API HUB 콘솔의 애플리케이션, "
         "또는 네이버 개발자센터 → 내 애플리케이션)의 값과 비교하세요 (앞뒤 공백·따옴표 없이).",
    403: "이 애플리케이션에 '검색' API 사용 설정이 없습니다. NAVER API HUB 콘솔(또는 네이버 개발자센터 → "
         "내 애플리케이션 → API 설정)에서 애플리케이션에 검색 API 를 추가하세요.",
    429: "네이버 검색 API 호출 한도(하루 25,000회)를 넘었습니다. 내일 다시 수집됩니다.",
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


def platform_name() -> str:
    """마지막으로 키가 통한 곳의 이름 (아직 모르면 빈 문자열)."""
    return PLATFORMS[_platform][0] if _platform else ""


def _error_detail(r) -> str:
    try:
        d = r.json()
    except ValueError:
        return r.text[:100]
    if isinstance(d.get("error"), dict):                   # NAVER API HUB (API Gateway) 형식
        e = d["error"]
        return " ".join(x for x in (e.get("message"), e.get("details")) if x)
    return d.get("errorMessage", "")                       # 개발자센터 형식


def fetch_news(query: str, display: int = 100, session=requests) -> list[dict]:
    global _platform
    order = [_platform, *[p for p in PLATFORMS if p != _platform]] if _platform else list(PLATFORMS)
    for i, plat in enumerate(order):
        _, url, (id_header, secret_header) = PLATFORMS[plat]
        try:
            r = session.get(url, timeout=10, params={"query": query, "display": min(display, 100), "sort": "date"},
                            headers={id_header: env("NAVER_CLIENT_ID"), secret_header: env("NAVER_CLIENT_SECRET")})
        except requests.RequestException as e:          # 긴 주소 대신 짧은 사유만
            raise NaverError(0, f"네이버에 연결하지 못했습니다 ({type(e).__name__})") from None
        if r.status_code == 401 and i < len(order) - 1:
            continue                                     # 다른 곳에서 받은 키일 수 있다
        if r.status_code != 200:
            raise NaverError(r.status_code, _error_detail(r))
        _platform = plat
        return r.json().get("items") or []
    raise NaverError(401)                                # (도달하지 않음)


def collect_one(conn, code: str, name: str, per_stock: int, scorer: Scorer, session=requests) -> int:
    """한 종목 뉴스를 받아 저장하고 건수를 돌려준다 (대시보드 '지금 뉴스 받기' 도 사용)."""
    items = fetch_news(f'"{name}"', per_stock, session)
    if not items:
        return 0
    good = []
    for it in items:                                     # 날짜·링크가 없거나 형식이 다른 기사는 건너뜀
        try:
            pub = parsedate_to_datetime(it["pubDate"])
        except (KeyError, TypeError, ValueError):
            continue
        if pub.tzinfo is not None and (it.get("originallink") or it.get("link")):
            good.append((it, pub))
    if not good:
        return 0
    titles = [clean(it.get("title", "")) for it, _ in good]
    rows = []
    for (it, pub), title, s in zip(good, titles, scorer.score(titles)):
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
        _status(conn, True, f"{ok}종목 {total}건" + (f" · {len(stocks) - ok}종목 실패" if ok < len(stocks) else "")
                + (f" ({platform_name()})" if platform_name() else ""))
    log.info("  뉴스: %d종목 %d건 (%s)", ok, total, platform_name() or "-")
    return total
