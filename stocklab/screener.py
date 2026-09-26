"""외국인 매수 강도 스크리너.

점수 = 아래 순위(백분위)의 평균
  - 최근 5일 외국인 순매수 금액
  - 최근 20일 외국인 순매수 금액
  - 최근 5일 외국인 순매수량 / 거래량 (시가총액이 작은 종목도 공정하게 비교)
  - 연속 순매수 일수
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db
from .features import _streak


def foreign_strength(conn, codes: list[str] | None = None) -> pd.DataFrame:
    fl = db.query(conn, "SELECT * FROM flows")
    if fl.empty:
        return pd.DataFrame()
    pr = db.query(conn, "SELECT code, date, close, volume FROM prices")
    df = fl.merge(pr, on=["code", "date"], how="left").sort_values(["code", "date"])
    if codes:
        df = df[df["code"].isin(codes)]
    asof = df["date"].max()
    rows = []
    for code, g in df.groupby("code"):
        if g["date"].iloc[-1] != asof or len(g) < 5:
            continue                        # 최신 수급이 없는 종목 제외
        t5, t20 = g.tail(5), g.tail(20)
        vol5 = t5["volume"].sum()
        rows.append({
            "asof": asof, "code": code,
            "frgn_amt_5": t5["frgn_amt"].sum(), "frgn_amt_20": t20["frgn_amt"].sum(),
            "frgn_ratio_5": t5["frgn_qty"].sum() / vol5 if vol5 else np.nan,
            "streak": int(_streak(g["frgn_qty"]).iloc[-1]),
            "orgn_amt_5": t5["orgn_amt"].sum(),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    ranks = out[["frgn_amt_5", "frgn_amt_20", "frgn_ratio_5", "streak"]].rank(pct=True)
    out["score"] = ranks.mean(axis=1).round(4)
    out["rank"] = out["score"].rank(ascending=False, method="first").astype(int)
    return out.sort_values("rank").reset_index(drop=True)


def run(conn, codes: list[str] | None = None) -> pd.DataFrame:
    out = foreign_strength(conn, codes)
    if not out.empty:
        db.upsert(conn, "screener", out)
        conn.commit()
    return out
