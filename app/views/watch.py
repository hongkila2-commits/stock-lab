"""관심종목: 내 관심종목 · 시가총액 상위 20 · AI 추천 상위 N."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import (H, PROB_COL, S, TARGET_LABEL, _color, compact, goto, listing, my_watchlist, names,
                    now_kst, price_basis, q, rt_quotes, show, stock_table)
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


@st.fragment(run_every=5)
def live_card() -> None:
    """장중 실시간 시세 (5초마다 자동 갱신). realtime.bat 이 받은 값."""
    rt = rt_quotes()
    if rt.empty:
        return
    mine = set(my_watchlist())
    last = pd.to_datetime(rt["ts"]).max()
    age = (now_kst() - last).total_seconds()
    when = f"{last:%H:%M:%S}" if age < 90 else f"마지막 수신 {last:%m/%d %H:%M}"
    th = float(S.alerts["move_pct"])
    df = pd.DataFrame({
        "code": rt.index, "종목": [("★ " if c in mine else "") + names().get(c, c) for c in rt.index],
        "현재가": rt["price"].to_numpy(), "등락%": rt["change_pct"].to_numpy(),
        "거래량": rt["volume"].to_numpy(), "시각": rt["ts"].str[11:19].to_numpy(),
    })
    df["_mine"] = df["code"].isin(mine)
    df = df.sort_values(["_mine", "등락%"], ascending=[False, False]).drop(columns="_mine")
    df = df.reset_index(drop=True)
    with st.container(border=True):
        st.markdown(f"**📡 실시간 시세** · {when}" + ("" if compact() else
                    f" · 5초마다 갱신 · ★ 관심종목 · ±{th:g}% 이상이면 카카오톡 알림"))
        codes = df["code"].tolist()
        view = df.drop(columns="code")
        if compact():
            view = view[["종목", "현재가", "등락%"]]
        styled = (view.style.map(_color, subset=["등락%"])
                  .map(lambda v: "font-weight:700" if abs(v) >= th else "", subset=["등락%"])
                  .format({"현재가": "{:,.0f}", "등락%": "{:+.2f}", "거래량": "{:,.0f}"}, na_rep="-"))
        nonce = st.session_state.get("live_nonce", 0)
        ev = st.dataframe(styled, hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key=f"t_live_{nonce}",
                          height=min(38 + 35 * len(view), 318))
        if ev and ev.selection.rows:
            st.session_state["live_nonce"] = nonce + 1     # 돌아왔을 때 다시 이동하지 않도록
            goto(codes[ev.selection.rows[0]])


def render() -> None:
    st.header("관심종목")
    st.caption("표의 행을 누르면 종목 상세로 이동합니다. 종목 추가는 왼쪽 **종목 검색**에서. "
               "● = 장중 실시간 가격")
    live_card()

    st.subheader("내 관심종목")
    mine = my_watchlist()
    basis = price_basis(mine)
    if basis:
        st.caption(basis)
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
