"""KOSPI·KOSDAQ 전체 상장 종목 목록 + 가격·시가총액 스냅샷.

출처 (자동 선택)
  1. 공공데이터포털 '금융위원회_주식시세정보' — .env 에 DATA_GO_KR_API_KEY 가 있을 때.
     공식 API. 가격은 '전 거래일 종가' (다음 영업일 오후에 공개된다).
  2. 네이버 금융 모바일 시세 — 키가 없거나 1이 실패할 때. 당일 시세지만 비공식이라
     네이버가 형식을 바꾸면 멈출 수 있다.

저장 단위: close 원 · volume 주 · value(거래대금) 백만원 · market_cap 억원
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from urllib.parse import unquote

import pandas as pd
import requests

from .. import db
from ..config import env

log = logging.getLogger(__name__)

DATAGOKR_URL = ("https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/"
                "getStockPriceInfo")
NAVER_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
COLUMNS = ["code", "name", "market", "close", "change_pct", "volume", "value",
           "market_cap", "asof", "source"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def num(x) -> float:
    """'71,500' · '-.69' · '' · None → 숫자 (없으면 NaN)."""
    if x is None:
        return float("nan")
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^0-9.\-]", "", str(x))
    try:
        return float(s)
    except ValueError:
        return float("nan")


def norm_code(x) -> str:
    """'A005930' · '5930' → '005930'."""
    s = str(x).strip().upper()
    return (s[1:] if s.startswith("A") else s).zfill(6)


def is_common(code: str, name: str) -> bool:
    """보통주만 True. 우선주(코드 끝자리가 0이 아님)·스팩 제외."""
    return str(code).endswith("0") and "스팩" not in str(name)


# ── 공공데이터포털 ─────────────────────────────────────────
def fetch_datagokr(key: str, today: date | None = None, session=requests) -> pd.DataFrame:
    # 포털의 'Encoding' 키(%2B 등 포함)를 붙여넣어도 requests 가 다시 인코딩하지 않도록 푼다
    key = unquote(key) if "%" in key else key
    today = today or date.today()
    for back in range(0, 10):                 # 최근 영업일부터 거슬러 올라가며 데이터 있는 날 찾기
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        r = session.get(DATAGOKR_URL, timeout=30, params={
            "serviceKey": key, "resultType": "json", "numOfRows": 5000, "pageNo": 1,
            "basDt": d.strftime("%Y%m%d"),
        })
        try:
            data = r.json()
        except ValueError:                    # 키 오류는 XML 로 온다
            m = re.search(r"<returnAuthMsg>([^<]+)", r.text) or re.search(r"<resultMsg>([^<]+)", r.text)
            raise RuntimeError(f"공공데이터포털 응답 오류: {m.group(1) if m else r.text[:120]}")
        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") not in (None, "00"):
            raise RuntimeError(f"공공데이터포털: {header.get('resultMsg')}")
        items = (data.get("response", {}).get("body", {}).get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        if items:
            return _from_datagokr(items)
    return pd.DataFrame(columns=COLUMNS)


def _from_datagokr(items: list[dict]) -> pd.DataFrame:
    raw = pd.DataFrame(items)
    raw = raw[raw["mrktCtg"].isin(["KOSPI", "KOSDAQ"])]
    asof = pd.to_datetime(raw["basDt"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return pd.DataFrame({
        "code": raw["srtnCd"].map(norm_code), "name": raw["itmsNm"].str.strip(),
        "market": raw["mrktCtg"], "close": raw["clpr"].map(num),
        "change_pct": raw["fltRt"].map(num), "volume": raw["trqu"].map(num),
        "value": raw["trPrc"].map(num) / 1e6, "market_cap": raw["mrktTotAmt"].map(num) / 1e8,
        "asof": asof, "source": "공공데이터포털",
    })[COLUMNS].reset_index(drop=True)


# ── 네이버 금융 (비공식) ───────────────────────────────────
def _pick(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def fetch_naver(session=requests, page_size: int = 100, max_pages: int = 40) -> pd.DataFrame:
    rows = []
    for market in ("KOSPI", "KOSDAQ"):
        for page in range(1, max_pages + 1):
            r = session.get(NAVER_URL.format(market=market), headers=UA, timeout=20,
                            params={"page": page, "pageSize": page_size})
            r.raise_for_status()
            stocks = r.json().get("stocks") or []
            for s in stocks:
                if _pick(s, "stockEndType") not in (None, "stock"):
                    continue                  # ETF·ETN 등 제외
                traded = str(_pick(s, "localTradedAt", "tradedAt") or "")[:10]
                rows.append({
                    "code": norm_code(_pick(s, "itemCode", "reutersCode")),
                    "name": str(_pick(s, "stockName", "itemName")).strip(),
                    "market": market,
                    "close": num(_pick(s, "closePrice", "nowPrice")),
                    "change_pct": num(_pick(s, "fluctuationsRatio")),
                    "volume": num(_pick(s, "accumulatedTradingVolume")),
                    "value": num(_pick(s, "accumulatedTradingValue")),       # 백만원
                    "market_cap": num(_pick(s, "marketValue")),              # 억원
                    "asof": traded or date.today().isoformat(), "source": "네이버",
                })
            if len(stocks) < page_size:
                break
    return pd.DataFrame(rows, columns=COLUMNS).drop_duplicates("code")


# ── 공통 ─────────────────────────────────────────────────
def fetch_listing() -> pd.DataFrame:
    key = env("DATA_GO_KR_API_KEY")
    if key:
        try:
            df = fetch_datagokr(key)
            if len(df):
                return df
            log.warning("  공공데이터포털: 최근 10일 데이터 없음 → 네이버로 대체")
        except Exception as e:
            log.warning("  공공데이터포털 실패 → 네이버로 대체: %s", e)
    return fetch_naver()


def update_listing(conn) -> pd.DataFrame:
    try:
        df = fetch_listing()
    except Exception as e:
        log.warning("  전체 종목 목록 수집 실패: %s", e)
        return pd.DataFrame(columns=COLUMNS)
    df = df.dropna(subset=["close"])
    if df.empty:
        log.warning("  전체 종목 목록이 비어 있습니다.")
        return df
    conn.execute("DELETE FROM listing")
    db.upsert(conn, "listing", df)
    conn.commit()
    log.info("  %s 기준 %d종목 (%s)", df["asof"].max(), len(df), df["source"].iloc[0])
    return df


def auto_universe(conn, n: int = 300) -> dict[str, str]:
    """보통주 시가총액 상위 n 종목 {code: name}."""
    df = db.query(conn, "SELECT code, name, market_cap FROM listing")
    if df.empty:
        return {}
    df = df[[is_common(c, nm) for c, nm in zip(df["code"], df["name"])]]
    top = df.sort_values("market_cap", ascending=False).head(n)
    return dict(zip(top["code"], top["name"]))
