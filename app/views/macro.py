"""거시지표: 지수·환율 비교, 관심종목과의 상관관계."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import DOWN, UP, my_watchlist, names, prices, q


def render() -> None:
    st.header("거시지표")
    mac = q("SELECT series, date, value FROM macro")
    if mac.empty:
        st.info("거시지표가 없습니다. `update.bat` 을 실행하세요.")
        return
    mac["date"] = pd.to_datetime(mac["date"])
    wide = mac.pivot(index="date", columns="series", values="value").sort_index()
    pick = st.multiselect("지표", list(wide.columns),
                          default=[c for c in ("KOSPI", "USDKRW", "SOX", "NASDAQ") if c in wide])
    yrs = st.slider("기간(년)", 1, 5, 1)
    w = wide[wide.index >= wide.index.max() - pd.DateOffset(years=yrs)][pick].ffill()
    if pick:
        st.markdown("**시작일 = 100 으로 맞춘 비교**")
        st.line_chart(w / w.bfill().iloc[0] * 100, height=380)

    st.markdown("**관심종목 수익률과 거시지표 변화의 상관관계** (최근 1년, 일간)")
    st.caption("해외 지표는 하루 전 값과 비교합니다 (미국 장은 한국 장 마감 뒤에 열리므로).")
    regions = q("SELECT series, MAX(region) AS region FROM macro GROUP BY series")
    glob = set(regions.loc[regions["region"] == "GLOBAL", "series"])
    chg = wide.pct_change(fill_method=None)
    chg = chg.apply(lambda s: s.shift(1) if s.name in glob else s)
    p = prices()
    watch = [c for c in my_watchlist() if c in set(p["code"])][:15]
    if not watch:
        st.caption("시세가 있는 관심종목이 없습니다.")
        return
    ret = p[p["code"].isin(watch)].pivot(index="date", columns="code", values="close").pct_change(fill_method=None)
    ret = ret[ret.index >= ret.index.max() - pd.DateOffset(years=1)]
    both = ret.join(chg, how="inner")
    corr = (both.corr().loc[ret.columns, chg.columns]
            .rename(index=lambda c: names().get(c, c)).dropna(axis=1, how="all"))
    fig = go.Figure(go.Heatmap(z=corr.values, x=corr.columns, y=corr.index, zmin=-1, zmax=1,
                               colorscale=[[0, DOWN], [0.5, "#f8f9fa"], [1, UP]],
                               text=np.round(corr.values, 2), texttemplate="%{text}"))
    fig.update_layout(height=120 + 40 * len(corr), margin=dict(t=40), xaxis=dict(side="top"))
    st.plotly_chart(fig, width="stretch")
