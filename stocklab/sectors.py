"""업종별 수급 흐름.

업종 분류는 한국투자증권 현재가 API 의 업종명(bstp_kor_isnm)을 쓴다.
업종이 자주 바뀌지 않으므로 30일에 한 번만 다시 조회한다.
금액 단위: flows 의 *_amt 는 백만원 → 여기서 억원으로 바꿔 돌려준다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import db
from .kis import KisError

log = logging.getLogger(__name__)


def update_sectors(conn, kis, codes: list[str], max_age_days: int = 30) -> int:
    have = db.query(conn, "SELECT code, updated FROM sectors")
    fresh_after = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    fresh = set(have.loc[have["updated"] >= fresh_after, "code"]) if not have.empty else set()
    todo = [c for c in codes if c not in fresh]
    if todo:
        log.info("  업종 분류 조회 %d종목 (처음 한 번만 오래 걸림)", len(todo))
    rows = []
    for i, code in enumerate(todo, 1):
        try:
            sector = kis.quote(code).get("sector") or ""
        except (KisError, Exception) as e:  # 네트워크 오류 포함: 다음 update 때 다시
            log.warning("  %s 업종 조회 실패: %s", code, e)
            continue
        if sector:
            rows.append({"code": code, "sector": sector, "updated": datetime.now().isoformat()})
        if i % 50 == 0:
            db.upsert(conn, "sectors", pd.DataFrame(rows))
            conn.commit()
            rows = []
    db.upsert(conn, "sectors", pd.DataFrame(rows))
    conn.commit()
    return len(todo)


def _joined(conn) -> pd.DataFrame:
    sec = db.query(conn, "SELECT code, sector FROM sectors")
    fl = db.query(conn, "SELECT code, date, frgn_amt, orgn_amt, prsn_amt FROM flows")
    if sec.empty or fl.empty:
        return pd.DataFrame()
    pr = db.query(conn, "SELECT code, date, close FROM prices").sort_values(["code", "date"])
    pr["ret"] = pr.groupby("code")["close"].pct_change(fill_method=None)
    df = fl.merge(sec, on="code").merge(pr[["code", "date", "ret"]], on=["code", "date"], how="left")
    return df


def sector_flows(conn, days: int = 60) -> pd.DataFrame:
    """업종 × 날짜: 외국인·기관·개인 순매수(억원), 평균 수익률, 종목 수."""
    df = _joined(conn)
    if df.empty:
        return df
    dates = sorted(df["date"].unique())[-days:]
    df = df[df["date"].isin(dates)]
    out = df.groupby(["sector", "date"]).agg(
        frgn=("frgn_amt", "sum"), orgn=("orgn_amt", "sum"), prsn=("prsn_amt", "sum"),
        ret=("ret", "mean"), n=("code", "nunique")).reset_index()
    out[["frgn", "orgn", "prsn"]] /= 100
    return out


def _streak(s: pd.Series) -> int:
    """마지막 날부터 거꾸로 같은 부호가 이어진 날 수 (+매수 / -매도)."""
    sign = np.sign(s.to_numpy())
    if len(sign) == 0 or sign[-1] == 0:
        return 0
    n = 0
    for v in sign[::-1]:
        if v != sign[-1]:
            break
        n += 1
    return int(n * sign[-1])


def sector_summary(conn, horizon: int = 5) -> pd.DataFrame:
    """업종별 요약: 외국인·기관 5/20일 순매수(억원), 5/20일 평균 수익률, 평균 강세 확률, 연속 순매수일."""
    daily = sector_flows(conn, days=20)
    if daily.empty:
        return daily
    dates = sorted(daily["date"].unique())
    last5 = set(dates[-5:])
    sec = db.query(conn, "SELECT code, sector FROM sectors")
    pr = db.query(conn, "SELECT code, date, close FROM prices").sort_values(["code", "date"])
    g = pr.groupby("code")["close"]
    pr["r5"], pr["r20"] = pr["close"] / g.shift(5) - 1, pr["close"] / g.shift(20) - 1
    last = pr.groupby("code").tail(1).merge(sec, on="code")
    pred = db.query(conn, "SELECT code, prob FROM predictions WHERE horizon = ? AND asof = "
                          "(SELECT MAX(asof) FROM predictions WHERE horizon = ?)", (horizon, horizon))
    last = last.merge(pred, on="code", how="left")
    rows = []
    for sector, d in daily.groupby("sector"):
        d = d.sort_values("date")
        s = last[last["sector"] == sector]
        rows.append({
            "sector": sector, "n": int(s["code"].nunique()),
            "frgn_5": d.loc[d["date"].isin(last5), "frgn"].sum(), "frgn_20": d["frgn"].sum(),
            "orgn_5": d.loc[d["date"].isin(last5), "orgn"].sum(), "orgn_20": d["orgn"].sum(),
            "ret_5": s["r5"].mean(), "ret_20": s["r20"].mean(),
            "prob": s["prob"].mean(), "streak": _streak(d["frgn"]),
        })
    out = pd.DataFrame(rows).sort_values("frgn_5", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    return out


def sector_members(conn, sector: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT code FROM sectors WHERE sector = ?", (sector,))]
