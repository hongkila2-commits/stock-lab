"""거시지표 수집: 야후 파이낸스(지수·환율·원자재), 한국은행 ECOS(선택), FRED(보조).

region 규칙 (정보 누설 방지의 핵심)
- KR     : 한국 장 마감 시점에 이미 아는 값 → 같은 날짜에 붙인다.
- GLOBAL : 해외 시장 값 → 한국 기준으로 '다음 거래일'부터 쓸 수 있다고 본다.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import requests

from .. import db
from ..config import env

log = logging.getLogger(__name__)

# 이름: (야후 티커, region)
YAHOO_SERIES = {
    "KOSPI": ("^KS11", "KR"),
    "KOSDAQ": ("^KQ11", "KR"),
    "USDKRW": ("KRW=X", "GLOBAL"),
    "SP500": ("^GSPC", "GLOBAL"),
    "NASDAQ": ("^IXIC", "GLOBAL"),
    "SOX": ("^SOX", "GLOBAL"),       # 필라델피아 반도체지수
    "VIX": ("^VIX", "GLOBAL"),
    "US10Y": ("^TNX", "GLOBAL"),     # 미국 10년물 금리(%)
    "DXY": ("DX-Y.NYB", "GLOBAL"),   # 달러인덱스
    "WTI": ("CL=F", "GLOBAL"),
    "USDJPY": ("JPY=X", "GLOBAL"),
}

# 야후가 막혔을 때 쓰는 보조 출처 (키 불필요)
FRED_FALLBACK = {"USDKRW": "DEXKOUS", "US10Y": "DGS10", "VIX": "VIXCLS", "SP500": "SP500"}


def _yahoo(ticker: str, start: date) -> pd.Series:
    """야후 파이낸스 차트 API 직접 호출 (yfinance 패키지 없이 — 순수 파이썬)."""
    t0 = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}",
        params={"period1": t0, "period2": int(datetime.now(timezone.utc).timestamp()),
                "interval": "1d", "events": "history"},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=20,
    )
    r.raise_for_status()
    res = (r.json().get("chart", {}).get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return pd.Series(dtype=float)
    tz = res.get("meta", {}).get("exchangeTimezoneName") or "UTC"
    # 거래소 현지 날짜로 변환 (코스피 → 서울, 나스닥 → 뉴욕)
    idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(tz).strftime("%Y-%m-%d")
    close = res["indicators"]["quote"][0]["close"]
    s = pd.Series(close, index=idx, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")]


def _fred(series_id: str, start: date) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def _ecos_usdkrw(api_key: str, start: date) -> pd.Series:
    """한국은행 원/달러 매매기준율(일별)."""
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10000/"
           f"731Y001/D/{start:%Y%m%d}/{date.today():%Y%m%d}/0000001")
    rows = requests.get(url, timeout=20).json().get("StatisticSearch", {}).get("row", [])
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["TIME"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return pd.Series(pd.to_numeric(df["DATA_VALUE"], errors="coerce").values, index=idx).dropna()


def _start_for(conn, series: str, years: int) -> date:
    row = conn.execute("SELECT MAX(date) FROM macro WHERE series = ?", (series,)).fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0]) - timedelta(days=7)
    return date.today() - timedelta(days=365 * years + 30)


def _store(conn, name: str, s: pd.Series, region: str) -> int:
    df = pd.DataFrame({"series": name, "date": s.index, "value": s.values, "region": region})
    return db.upsert(conn, "macro", df)


def update_macro(conn, history_years: int = 5) -> int:
    total = 0
    for name, (ticker, region) in YAHOO_SERIES.items():
        start = _start_for(conn, name, history_years)
        s = pd.Series(dtype=float)
        try:
            s = _yahoo(ticker, start)
        except Exception as e:  # 야후는 비공식이라 종종 실패한다
            log.warning("  야후 %s 실패: %s", ticker, e)
        if s.empty and name in FRED_FALLBACK:
            try:
                s = _fred(FRED_FALLBACK[name], start)
                log.info("  %s → FRED 로 대체", name)
            except Exception as e:
                log.warning("  FRED %s 실패: %s", name, e)
        n = _store(conn, name, s, region)
        total += n
        log.info("  %-7s %d건", name, n)

    key = env("ECOS_API_KEY")
    if key:
        try:
            s = _ecos_usdkrw(key, _start_for(conn, "USDKRW_BOK", history_years))
            total += _store(conn, "USDKRW_BOK", s, "KR")
            log.info("  USDKRW_BOK(한국은행) %d건", len(s))
        except Exception as e:
            log.warning("  ECOS 실패: %s", e)
    conn.commit()
    return total
