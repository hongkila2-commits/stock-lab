"""종목 상세: 요약 카드 · 예측 근거 · 차트 · 뉴스/공시."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import (DOWN, H, PROB_TITLE, S, TARGET_LABEL, UP, eok, flows, latest_pred, listing, metric,
                    my_watchlist, names, prices, q, refresh, signal, stats)
from stocklab.config import env
from stocklab.picks import describe


@st.cache_data(ttl=600, show_spinner=False)
def kis_quote(code: str) -> dict | None:
    """KIS 현재가·재무. 키가 없거나 실패하면 None (화면에서 조용히 생략)."""
    if not env("KIS_APP_KEY"):
        return None
    try:
        from stocklab.cli import _kis
        return _kis().quote(code)
    except Exception:
        return None


def _fmt(v, f="{:,.2f}", suffix=""):
    return "-" if v is None or pd.isna(v) else f.format(v) + suffix


def header(code: str, demo: bool) -> None:
    li = listing()
    l = li.loc[code] if code in li.index else None
    st_ = stats()
    s = st_.loc[code] if code in st_.index else None
    qt = None if demo else kis_quote(code)

    mine = code in my_watchlist()
    title, btn = st.columns([4, 1])
    market = l["market"] if l is not None else ""
    sector = f" · {qt['sector']}" if qt and qt.get("sector") else ""
    title.markdown(f"## {names().get(code, code)} <span style='font-size:0.9rem;color:gray'>"
                   f"{code} · {market}{sector}</span>", unsafe_allow_html=True)
    with btn:
        if code in S.watchlist:
            st.caption("★ 관심종목 (settings.yaml 에서 관리)")
        elif mine:
            if st.button("★ 관심종목 해제", width="stretch"):
                from stocklab.cli import remove_stock
                remove_stock(code)
                refresh()
                st.rerun()
        elif st.button("☆ 관심종목 추가", type="primary", width="stretch"):
            with st.spinner("추가 중… 시세 이력이 없으면 한국투자증권에서 받아옵니다 (약 5~10초)"):
                from stocklab.cli import add_stock
                st.session_state["flash"] = add_stock(code)
            refresh()
            st.rerun()

    price = qt["price"] if qt and pd.notna(qt.get("price")) else (
        s["close"] if s is not None else (l["close"] if l is not None else np.nan))
    chg = qt["change_pct"] if qt and pd.notna(qt.get("change_pct")) else (
        s["r1"] * 100 if s is not None else (l["change_pct"] if l is not None else np.nan))
    cap = qt["market_cap"] if qt and pd.notna(qt.get("market_cap")) else (
        l["market_cap"] if l is not None else np.nan)
    p = latest_pred().get(code, np.nan)

    m = st.columns(6)
    metric(m[0], "현재가(원)", _fmt(price, "{:,.0f}"), chg)
    m[1].metric("시가총액", eok(cap))
    m[2].metric(PROB_TITLE, _fmt(p, "{:.2f}"),
                signal(p) if pd.notna(p) else None, delta_color="off",
                help=f"{H}거래일 뒤 {TARGET_LABEL}. 0.5 = 반반")
    if s is not None and pd.notna(s["pos52"]):
        m[3].metric("52주 위치", f"{s['pos52'] * 100:.0f}%",
                    help=f"최저 {s['lo']:,.0f} ~ 최고 {s['hi']:,.0f} 사이 어디쯤인지 (0% 최저, 100% 최고)")
    if s is not None and pd.notna(s.get("frgn_amt", np.nan)):
        m[4].metric("외국인 5일", f"{s['frgn_amt']:+,.1f}억")
    if qt:
        m[5].metric("PER · PBR", f"{_fmt(qt['per'], '{:.1f}')} · {_fmt(qt['pbr'], '{:.2f}')}",
                    help=f"외국인 소진율 {_fmt(qt['foreign_pct'], '{:.1f}', '%')}")
    elif s is not None and pd.notna(s.get("orgn_amt", np.nan)):
        m[5].metric("기관 5일", f"{s['orgn_amt']:+,.1f}억")


def reasons(code: str) -> None:
    ex = q("SELECT * FROM explain WHERE code = ? AND asof = (SELECT MAX(asof) FROM explain WHERE code = ?)",
           (code, code))
    if ex.empty:
        return
    has_contrib = ex["contrib"].notna().any()
    st.markdown("#### 왜 이렇게 예측했나" if has_contrib else "#### 눈에 띄는 지표")
    st.caption(("▲ 확률을 올린 요인 · ▼ 내린 요인 (모델 기여도 순)" if has_contrib
                else "같은 날 분석 대상 전체 중 순위 (예측에 중요한 지표 중 두드러진 것)")
               + f" · 기준일 {ex['asof'].iloc[0]}")
    cols = st.columns(len(ex))
    for col, r in zip(cols, ex.itertuples()):
        color = UP if (r.contrib or 0) > 0 else DOWN if (r.contrib or 0) < 0 else "gray"
        col.markdown(f"<div style='border-left:4px solid {color};padding:4px 10px;font-size:0.9rem'>"
                     f"{describe(r.feature, r.value, r.pct, r.contrib)}</div>", unsafe_allow_html=True)
    st.write("")


def chart(code: str) -> None:
    period = st.radio("기간", ["3개월", "6개월", "1년", "3년", "전체"], index=2, horizontal=True)
    days = {"3개월": 92, "6개월": 183, "1년": 365, "3년": 1095, "전체": 100000}[period]
    p = prices()
    g = p[p["code"] == code].copy()
    g["ma20"], g["ma60"] = g["close"].rolling(20).mean(), g["close"].rolling(60).mean()
    g = g[g["date"] >= g["date"].max() - pd.Timedelta(days=days)]
    f = flows()
    fl = f[(f["code"] == code) & (f["date"] >= g["date"].min())] if not f.empty else pd.DataFrame()

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.6, 0.15, 0.25],
                        subplot_titles=("", "거래량", "투자자별 순매수(억원)"))
    fig.add_trace(go.Candlestick(x=g["date"], open=g["open"], high=g["high"], low=g["low"],
                                 close=g["close"], name="주가", increasing_line_color=UP,
                                 decreasing_line_color=DOWN), 1, 1)
    fig.add_trace(go.Scatter(x=g["date"], y=g["ma20"], name="20일선",
                             line=dict(width=1.2, color="#f59f00")), 1, 1)
    fig.add_trace(go.Scatter(x=g["date"], y=g["ma60"], name="60일선",
                             line=dict(width=1.2, color="#868e96")), 1, 1)
    fig.add_trace(go.Bar(x=g["date"], y=g["volume"], name="거래량", marker_color="#adb5bd",
                         showlegend=False), 2, 1)
    if len(fl):
        for col, nm, color in (("frgn_amt", "외국인", "#7048e8"), ("orgn_amt", "기관", "#20c997")):
            fig.add_trace(go.Bar(x=fl["date"], y=fl[col] / 100, name=nm, marker_color=color), 3, 1)
    fig.update_layout(height=680, xaxis_rangeslider_visible=False, barmode="group",
                      margin=dict(t=30, b=10), legend=dict(orientation="h", y=1.02))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch")


def news_and_disclosures(code: str) -> None:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**최근 뉴스**")
        news = q("SELECT pub_ts, title, sentiment, link FROM news WHERE code = ? "
                 "ORDER BY pub_ts DESC LIMIT 30", (code,))
        if news.empty:
            st.caption("뉴스 없음 (네이버 API 키 필요 · 관심종목과 외국인 상위 종목만 수집)")
        else:
            news["감성"] = news["sentiment"].map(
                lambda s: "🔴 긍정" if s > 0.2 else "🔵 부정" if s < -0.2 else "⚪ 중립")
            st.dataframe(news[["pub_ts", "감성", "title", "link"]], hide_index=True, width="stretch",
                         height=380, column_config={
                             "link": st.column_config.LinkColumn("링크", display_text="열기"),
                             "pub_ts": "시각", "title": "제목"})
    with c2:
        st.markdown("**최근 공시**")
        dis = q("SELECT date, kind, title, rcept_no FROM disclosures WHERE code = ? "
                "ORDER BY date DESC LIMIT 30", (code,))
        if dis.empty:
            st.caption("공시 없음 (DART 키 필요 · 관심종목과 외국인 상위 종목만 수집)")
        else:
            dis["link"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + dis["rcept_no"]
            st.dataframe(dis[["date", "kind", "title", "link"]], hide_index=True, width="stretch",
                         height=380, column_config={
                             "link": st.column_config.LinkColumn("원문", display_text="열기"),
                             "date": "접수일", "kind": "분류", "title": "제목"})


def render(demo: bool) -> None:
    code = st.session_state.get("code")
    if not code:
        st.info("왼쪽 **종목 검색**에서 종목을 고르거나, 관심종목 표에서 행을 누르세요.")
        return
    header(code, demo)
    if code not in stats().index:
        if code in my_watchlist():
            st.info("관심종목이지만 아직 시세 이력이 없습니다. 다음 `update.bat` 때 받아와 차트·예측을 표시합니다.")
        else:
            st.info("이 종목은 아직 시세 이력이 없습니다. **☆ 관심종목 추가**를 누르면 한국투자증권에서 "
                    "과거 시세·수급을 받아오고(약 5~10초), 예측도 계산합니다.")
        return
    reasons(code)
    chart(code)
    ph = q("SELECT asof, prob FROM predictions WHERE code = ? AND horizon = ? ORDER BY asof", (code, H))
    if len(ph) > 1:
        st.markdown(f"**{TARGET_LABEL} 추이**")
        st.line_chart(ph.set_index("asof")["prob"], height=180)
    news_and_disclosures(code)
