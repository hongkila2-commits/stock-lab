"""외국인 수급: 분석 대상 전체의 외국인 매수 강도 순위."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import PROB_COL, latest_pred, listing, names, q, show, signal


def render() -> None:
    st.header("외국인 매수 강도 순위")
    sc = q("SELECT * FROM screener WHERE asof = (SELECT MAX(asof) FROM screener) ORDER BY rank")
    if sc.empty:
        st.info("수급 데이터가 없습니다. `update.bat` 을 실행하세요.")
        return
    st.caption(f"기준일 {sc['asof'].iloc[0]} · 분석 대상 {len(sc)}종목 · 점수 = 5일·20일 순매수금액, "
               "거래량 대비 순매수 비율, 연속 순매수일의 순위 평균 (1에 가까울수록 강함) · 행을 누르면 상세로")
    li, pr = listing(), latest_pred()
    df = pd.DataFrame({
        "code": sc["code"], "순위": sc["rank"], "종목": sc["code"].map(lambda c: names().get(c, c)),
        "시장": sc["code"].map(lambda c: li.at[c, "market"] if c in li.index else ""),
        "점수": sc["score"],
        "외국인5일(억)": sc["frgn_amt_5"] / 100, "외국인20일(억)": sc["frgn_amt_20"] / 100,
        "거래량대비%": sc["frgn_ratio_5"] * 100, "연속일": sc["streak"].astype(int),
        "기관5일(억)": sc["orgn_amt_5"] / 100, PROB_COL: sc["code"].map(pr),
    })
    df["신호"] = df[PROB_COL].map(signal)
    show(df, key="t_flows", height=640, compact_cols=["순위", "종목", "점수", "외국인5일(억)", PROB_COL], colcfg={
        "점수": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1)})
