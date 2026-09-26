"""관심종목: 내 관심종목 · 시가총액 상위 20 · AI 추천 상위 N."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import H, PROB_COL, S, TARGET_LABEL, listing, my_watchlist, q, show, stock_table
from stocklab.collectors.listing import is_common
from stocklab.picks import describe, is_macro


def reasons(asof: str, codes: list[str], k: int = 2) -> dict[str, dict]:
    ex = q("SELECT * FROM explain WHERE asof = ?", (asof,))
    out = {}
    for c in codes:
        e = ex[ex["code"] == c]
        if e.empty:
            continue
        # 종목끼리 구분되는 근거만 (시장 공통 요인은 상세 화면에서), 확률을 올린 근거 우선
        own = e[~e["feature"].map(is_macro)]
        e = own if len(own) else e
        if e["contrib"].notna().any():
            e = e.sort_values("contrib", ascending=False)
        out[c] = {"주요 근거": " · ".join(
            describe(r.feature, r.value, r.pct, r.contrib) for r in e.head(k).itertuples())}
    return out


def render() -> None:
    st.header("관심종목")
    st.caption("표의 행을 누르면 종목 상세로 이동합니다. 종목 추가는 왼쪽 **종목 검색**에서.")

    st.subheader("내 관심종목")
    mine = my_watchlist()
    show(stock_table(mine), key="t_mine")
    if not mine:
        st.caption("왼쪽 종목 검색에서 종목을 고른 뒤 ☆ 관심종목 추가를 누르세요.")

    st.subheader("시가총액 상위 20")
    li = listing()
    if li.empty:
        st.info("전체 종목 목록이 없습니다. `update.bat` 실행 후 표시됩니다.")
    else:
        common = li[[is_common(c, n) for c, n in zip(li.index, li["name"])]]
        top = common.sort_values("market_cap", ascending=False).head(20).index.tolist()
        st.caption(f"보통주 기준 · {li['asof'].max()} · 출처 {li['source'].iloc[0]}")
        df = stock_table(top)
        df.insert(1, "순위", range(1, len(df) + 1))
        show(df, key="t_cap", height=35 * 20 + 38)

    st.subheader(f"AI 추천 — {H}거래일 {TARGET_LABEL} 상위 {S.picks['count']}")
    picks = q("SELECT * FROM picks WHERE asof = (SELECT MAX(asof) FROM picks) ORDER BY rank")
    if picks.empty:
        st.info("추천 결과가 없습니다. `update.bat` 을 실행하면 모델이 종목을 고릅니다.")
    else:
        asof = picks["asof"].iloc[0]
        st.caption(f"기준일 {asof} · 분석 대상 중 보통주, 20일 평균 거래대금 {S.picks['min_value_eok']}억원 이상에서 선정 · "
                   "▲ 확률을 올린 요인 ▼ 내린 요인 · 참고용 통계 모델이며 수익을 보장하지 않습니다.")
        codes = picks["code"].tolist()
        df = stock_table(codes, reasons(asof, codes))
        df.insert(1, "순위", picks["rank"].to_numpy())
        # 근거가 핵심이라 열을 줄이고 확률 바로 옆에 둔다
        df = df[[c for c in ["code", "순위", "종목", PROB_COL, "주요 근거", "현재가", "등락%",
                             "시가총액", "외국인5일(억)"] if c in df]]
        show(df, key="t_picks", height=35 * min(len(df), 15) + 38,
             colcfg={"주요 근거": st.column_config.TextColumn(width="large")})
