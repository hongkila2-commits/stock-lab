"""네이버 뉴스 검색 API 로 종목 뉴스 수집 + 감성 점수."""
from __future__ import annotations

import logging
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


def effective_date(pub: datetime) -> str:
    """장 마감(15:30) 이후 기사는 다음 날 정보로 취급 → 예측 시 미래 정보 누설 방지.
    주말·휴일은 특징값 생성 단계에서 다음 거래일로 밀린다."""
    local = pub.astimezone(KST)
    d = local.date() + (timedelta(days=1) if local.time() >= MARKET_CLOSE else timedelta(0))
    return d.isoformat()


def fetch_news(query: str, display: int = 100) -> list[dict]:
    cid, secret = env("NAVER_CLIENT_ID"), env("NAVER_CLIENT_SECRET")
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
        params={"query": query, "display": min(display, 100), "sort": "date"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def update_news(conn, stocks: dict[str, str], per_stock: int = 100,
                engine: str = "lexicon") -> int:
    if not (env("NAVER_CLIENT_ID") and env("NAVER_CLIENT_SECRET")):
        log.info("  네이버 API 키가 없어 뉴스 수집을 건너뜁니다.")
        return 0
    scorer = Scorer(engine)
    total = 0
    for code, name in stocks.items():
        try:
            items = fetch_news(f'"{name}"', per_stock)
        except Exception as e:
            log.warning("  %s 뉴스 실패: %s", name, e)
            continue
        if not items:
            continue
        titles = [clean(it["title"]) for it in items]
        scores = scorer.score(titles)
        rows = []
        for it, title, s in zip(items, titles, scores):
            pub = parsedate_to_datetime(it["pubDate"])
            rows.append({"code": code, "link": it.get("originallink") or it["link"],
                         "pub_ts": pub.astimezone(KST).isoformat(timespec="minutes"),
                         "eff_date": effective_date(pub), "title": title, "sentiment": s})
        total += db.upsert(conn, "news", pd.DataFrame(rows))
        log.info("  %s 뉴스 %d건", name, len(rows))
    conn.commit()
    return total
