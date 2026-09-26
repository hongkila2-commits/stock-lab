"""업종 수급: 업종별 외국인·기관 순매수 흐름."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import DOWN, H, PROB_COL, PROB_TITLE, UP, _color, compact, show, stock_table
from stocklab import db
from stocklab.config import db_path
from stocklab.sectors import sector_flows, sector_summary

WHO = {"외국인": "frgn", "기관": "orgn", "개인": "prsn"}


@st.cache_data(ttl=300)
def _load():
    if not db_path().exists():
        return pd.DataFrame(), pd.DataFrame(), {}
    conn = db.connect()
    try:
        sec = db.query(conn, "SELECT code, sector FROM sectors")
        members = sec.groupby("sector")["code"].apply(list).to_dict() if not sec.empty else {}
        return sector_summary(conn, H), sector_flows(conn, days=60), members
    finally:
        conn.close()


def render() -> None:
    st.header("업종 수급")
    summary, daily, members = _load()
    if summary.empty:
        st.info("업종 데이터가 없습니다. `update.bat` 을 실행하면 분석 대상 종목의 업종을 조회해 채웁니다 "
                "(처음 한 번 약 3분).")
        return
    last = daily["date"].max()
    st.caption(f"기준일 {last} · 분석 대상 종목을 한국투자증권 업종 분류로 묶어 합산 · 단위 억원 · "
               "업종 옆 괄호는 종목 수 · 표의 행을 누르면 그 업종 종목이 아래에 나옵니다")

    view = pd.DataFrame({
        "순위": summary["rank"], "업종": summary["sector"] + " (" + summary["n"].astype(str) + ")",
        "외국인5일(억)": summary["frgn_5"], "외국인20일(억)": summary["frgn_20"],
        "기관5일(억)": summary["orgn_5"], "기관20일(억)": summary["orgn_20"],
        "5일%": summary["ret_5"] * 100, "20일%": summary["ret_20"] * 100,
        PROB_COL: summary["prob"], "외국인연속": summary["streak"].astype(int),
    })
    if compact():
        view = view[["업종", "외국인5일(억)", "5일%", PROB_COL]]
    signed = [c for c in ["외국인5일(억)", "외국인20일(억)", "기관5일(억)", "기관20일(억)", "5일%", "20일%",
                          "외국인연속"] if c in view]
    styled = view.style.map(_color, subset=signed).format(
        {k: v for k, v in {"외국인5일(억)": "{:+,.0f}", "외국인20일(억)": "{:+,.0f}", "기관5일(억)": "{:+,.0f}",
                           "기관20일(억)": "{:+,.0f}", "5일%": "{:+.2f}", "20일%": "{:+.2f}",
                           "외국인연속": "{:+d}", PROB_COL: "{:.2f}"}.items() if k in view}, na_rep="-")
    ev = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key="t_sectors",
                      height=min(38 + 35 * len(view), 460), column_config={
                          PROB_COL: st.column_config.ProgressColumn(
                              f"평균 {PROB_TITLE}", format="%.2f", min_value=0, max_value=1),
                          "외국인연속": st.column_config.NumberColumn(
                              help="업종 합계로 외국인이 연속 순매수(+)/순매도(-)한 거래일 수")})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        sector = summary["sector"].iloc[rows[0]]
        st.markdown(f"#### {sector} 종목")
        df = stock_table(members.get(sector, []))
        show(df.sort_values("외국인5일(억)", ascending=False, na_position="last"), key="t_sector_members")

    c1, c2 = st.columns([1, 3])
    who = c1.radio("주체", list(WHO), horizontal=True, key="sec_who")
    col = WHO[who]

    st.markdown(f"**업종 × 최근 {10 if compact() else 20}거래일 {who} 순매수 (억원)** — 빨강 순매수 · 파랑 순매도")
    n_days = 10 if compact() else 20                  # 휴대폰에선 최근 10일만
    d20 = daily[daily["date"].isin(sorted(daily["date"].unique())[-n_days:])]
    heat = d20.pivot(index="sector", columns="date", values=col).reindex(summary["sector"][::-1])  # 1위가 맨 위
    lim = float(heat.abs().quantile(0.95).max() or 1)
    fig = go.Figure(go.Heatmap(z=heat.values, x=[d[5:] for d in heat.columns], y=heat.index,
                               zmin=-lim, zmax=lim, colorscale=[[0, DOWN], [0.5, "#f8f9fa"], [1, UP]],
                               hovertemplate="%{y} %{x}<br>%{z:,.0f}억<extra></extra>"))
    fig.update_layout(height=80 + 28 * len(heat), margin=dict(t=10, b=10, l=10))
    fig.update_xaxes(type="category")          # '09-25' 를 날짜(연도)로 잘못 읽지 않게
    st.plotly_chart(fig, width="stretch")

    st.markdown(f"**{who} 누적 순매수 추이 (최근 60거래일, 억원)** — 실선: 가장 많이 산 업종 4 · 점선: 가장 많이 판 업종 2")
    cum = daily.pivot(index="date", columns="sector", values=col).fillna(0).cumsum()
    order = cum.iloc[-1].sort_values(ascending=False).index
    pick = list(order[:4]) + list(order[-2:])
    cum = cum[pick]
    fig = go.Figure()
    for i, s in enumerate(pick):
        fig.add_scatter(x=cum.index, y=cum[s], name=s, mode="lines",
                        line=dict(width=2.5 if i < 4 else 1.5, dash="solid" if i < 4 else "dot"))
    fig.update_layout(height=360, margin=dict(t=10, b=10),
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")
