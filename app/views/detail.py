"""종목 상세: 요약 카드 · 예측 근거 · 차트 · 뉴스/공시."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import (DOWN, H, HORIZONS, PROB_TITLE, S, TARGET_LABEL, UP, horizon_label, compact, eok, flows, rt_fresh, latest_pred, listing, metric,
                    my_watchlist, names, prices, q, refresh, signal, stats)
from stocklab.config import env
from stocklab.picks import describe


@st.cache_data(ttl=60, show_spinner=False)
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
    sec = q("SELECT sector FROM sectors WHERE code = ?", (code,))
    sector_name = sec["sector"].iloc[0] if not sec.empty else (qt.get("sector") if qt else "")
    sector = f" · {sector_name}" if sector_name else ""
    title.markdown(f"## {names().get(code, code)} <span style='font-size:0.9rem;color:gray'>"
                   f"{code} · {market}{sector}</span>", unsafe_allow_html=True)
    with btn:
        if st.session_state.get("readonly"):
            st.caption("👀 보기 전용")
        elif code in S.watchlist:
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

    # 현재가 우선순위: 실시간(2분 이내) → KIS 조회(60초 캐시) → 일봉 종가 → 전체 목록
    rt = rt_fresh()
    live = rt.loc[code] if code in rt.index else None
    if live is not None:
        price, chg = live["price"], live["change_pct"]
    elif qt and pd.notna(qt.get("price")):
        price, chg = qt["price"], qt.get("change_pct", np.nan)
    elif s is not None:
        price, chg = s["close"], s["r1"] * 100
    else:
        price = l["close"] if l is not None else np.nan
        chg = l["change_pct"] if l is not None else np.nan
    cap = qt["market_cap"] if qt and pd.notna(qt.get("market_cap")) else (
        l["market_cap"] if l is not None else np.nan)
    p = latest_pred().get(code, np.nan)
    probs = {h: latest_pred(h).get(code, np.nan) for h in HORIZONS}    # 1주·1개월 …

    if compact():
        _compact_cards(price, chg, cap, probs, s, qt)
        return
    m = iter(st.columns(5 + len(HORIZONS)))
    metric(next(m), "현재가(원)", _fmt(price, "{:,.0f}"), chg)
    next(m).metric("시가총액", eok(cap))
    for h, ph in probs.items():                      # 기간별 강세 확률 (1주, 1개월 …)
        next(m).metric(f"{horizon_label(h)} 강세", _fmt(ph, "{:.2f}"),
                       signal(ph) if pd.notna(ph) else None, delta_color="off",
                       help=f"{h}거래일 뒤 {TARGET_LABEL}. 0.5 = 반반")
    c52, cfr, clast = next(m), next(m), next(m)
    if s is not None and pd.notna(s["pos52"]):
        c52.metric("52주 위치", f"{s['pos52'] * 100:.0f}%",
                   help=f"최저 {s['lo']:,.0f} ~ 최고 {s['hi']:,.0f} 사이 어디쯤인지 (0% 최저, 100% 최고)")
    if s is not None and pd.notna(s.get("frgn_amt", np.nan)):
        cfr.metric("외국인 5일", f"{s['frgn_amt']:+,.0f}억")
    if qt:
        clast.metric("PER · PBR", f"{_fmt(qt['per'], '{:.1f}')} · {_fmt(qt['pbr'], '{:.2f}')}",
                     help=f"외국인 소진율 {_fmt(qt['foreign_pct'], '{:.1f}', '%')}")
    elif s is not None and pd.notna(s.get("orgn_amt", np.nan)):
        clast.metric("기관 5일", f"{s['orgn_amt']:+,.0f}억")


def _compact_cards(price, chg, cap, probs, s, qt) -> None:
    """휴대폰: 요약 지표를 3칸 격자 한 덩어리로 (Streamlit 열은 좁은 화면에서 세로로 쌓여 길어짐)."""
    def color(v):
        return UP if v > 0 else DOWN if v < 0 else "inherit"
    cards = [("현재가", _fmt(price, "{:,.0f}"),
              "" if pd.isna(chg) else f"<span style='color:{color(chg)}'>{chg:+.2f}%</span>"),
             ("시가총액", eok(cap), "")]
    cards += [(f"{horizon_label(h)} 강세", _fmt(ph, "{:.2f}"), signal(ph) if pd.notna(ph) else "")
              for h, ph in probs.items()]
    if s is not None:
        cards.append(("52주 위치", "-" if pd.isna(s["pos52"]) else f"{s['pos52'] * 100:.0f}%", ""))
        for col, lab in (("frgn_amt", "외국인 5일"), ("orgn_amt", "기관 5일"))[: 6 - len(cards)]:
            v = s.get(col, np.nan)
            cards.append((lab, "-" if pd.isna(v) else
                          f"<span style='color:{color(v)}'>{v:+,.1f}억</span>", ""))
    if qt and len(cards) < 6:
        cards.append(("PER·PBR", f"{_fmt(qt['per'], '{:.1f}')}·{_fmt(qt['pbr'], '{:.1f}')}", ""))
    cells = "".join(
        f"<div style='padding:6px 4px'><div style='font-size:0.75rem;color:gray'>{k}</div>"
        f"<div style='font-size:1.15rem;font-weight:600'>{v}</div>"
        f"<div style='font-size:0.75rem'>{sub}</div></div>" for k, v, sub in cards)
    st.markdown(f"<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:2px'>{cells}</div>",
                unsafe_allow_html=True)


def reasons(code: str) -> None:
    have = q("SELECT DISTINCT horizon FROM explain WHERE code = ?", (code,))
    hs = [h for h in HORIZONS if h in set(have["horizon"])] if not have.empty else []
    if not hs:
        return
    h = hs[0]
    if len(hs) > 1:
        h = st.segmented_control("예측 기간", hs, default=hs[0], key="reason_h",
                                 format_func=lambda x: f"{horizon_label(x)} 예측의 근거") or hs[0]
    ex = q("SELECT * FROM explain WHERE code = ? AND horizon = ? AND asof = "
           "(SELECT MAX(asof) FROM explain WHERE code = ? AND horizon = ?)", (code, int(h), code, int(h)))
    if ex.empty:
        return
    has_contrib = ex["contrib"].notna().any()
    st.markdown("#### 왜 이렇게 예측했나" if has_contrib else "#### 눈에 띄는 지표")
    st.caption(("▲ 확률을 올린 요인 · ▼ 내린 요인 (모델 기여도 순)" if has_contrib
                else "같은 날 분석 대상 전체 중 순위 (예측에 중요한 지표 중 두드러진 것)")
               + f" · 기준일 {ex['asof'].iloc[0]}")
    cols = st.columns(len(ex)) if not compact() else [st.container() for _ in range(len(ex))]
    for col, r in zip(cols, ex.itertuples()):
        color = UP if (r.contrib or 0) > 0 else DOWN if (r.contrib or 0) < 0 else "gray"
        col.markdown(f"<div style='border-left:4px solid {color};padding:4px 10px;font-size:0.9rem'>"
                     f"{describe(r.feature, r.value, r.pct, r.contrib)}</div>", unsafe_allow_html=True)
    st.write("")


@st.fragment(run_every=10)
def intraday(code: str) -> None:
    """가장 최근 장의 1분봉 (realtime.bat 이 받은 값, 10초마다 갱신)."""
    from common import _read
    day = _read("SELECT MAX(substr(minute, 1, 10)) AS d FROM rt_bars WHERE code = ?", (code,))
    if day.empty or not day["d"].iloc[0]:
        return
    d = day["d"].iloc[0]
    b = _read("SELECT * FROM rt_bars WHERE code = ? AND minute LIKE ? ORDER BY minute", (code, f"{d}%"))
    b["minute"] = pd.to_datetime(b["minute"])
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
    fig.add_trace(go.Candlestick(x=b["minute"], open=b["o"], high=b["h"], low=b["l"], close=b["c"],
                                 increasing_line_color=UP, decreasing_line_color=DOWN, name="1분봉"), 1, 1)
    fig.add_trace(go.Bar(x=b["minute"], y=b["v"], marker_color="#adb5bd", name="거래량"), 2, 1)
    fig.update_layout(height=260 if compact() else 360, xaxis_rangeslider_visible=False, showlegend=False,
                      margin=dict(t=10, b=10))
    st.markdown(f"**📡 {d} 장중 1분봉** · 마지막 {b['minute'].iloc[-1]:%H:%M} · 10초마다 갱신")
    st.plotly_chart(fig, width="stretch")


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
    fig.update_layout(height=480 if compact() else 680, xaxis_rangeslider_visible=False, barmode="group",
                      margin=dict(t=30, b=10), legend=dict(orientation="h", y=1.02))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch")


def _status(key: str) -> dict | None:
    """update 가 남긴 마지막 수집 결과 (meta.news_status / dart_status): ok|시각|내용."""
    m = q("SELECT value FROM meta WHERE key = ?", (key,))
    if m.empty:
        return None
    kind, when, text = (m["value"].iloc[0].split("|", 2) + ["", ""])[:3]
    return {"ok": kind == "ok", "when": when, "text": text}


def _news_now(code: str) -> None:
    """이 종목 뉴스를 지금 받기 — 키가 제대로 됐는지 바로 확인하는 용도도 겸한다."""
    from stocklab import db
    from stocklab.collectors.news import collect_one
    from stocklab.sentiment import Scorer
    name = names().get(code, code)
    with st.spinner(f"{name} 뉴스 받는 중…"):
        conn = db.connect()
        try:
            n = collect_one(conn, code, name, int(S.collect["news_per_stock"]), Scorer(S.sentiment["engine"]))
            conn.commit()
            st.session_state["news_msg"] = (code, True, f"{name} 뉴스 {n}건을 받았습니다.")
        except Exception as e:
            st.session_state["news_msg"] = (code, False, f"뉴스를 받지 못했습니다: {e}")
        finally:
            conn.close()
    refresh()
    st.rerun()


def news_and_disclosures(code: str, demo: bool = False) -> None:
    from stocklab.collectors.news import has_keys
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**최근 뉴스**")
        msg = st.session_state.pop("news_msg", None)
        fresh = bool(msg and msg[0] == code)          # 방금 '지금 뉴스 받기' 결과 → 예전 수집 결과보다 우선
        if fresh:
            (st.success if msg[1] else st.error)(msg[2])
        news = q("SELECT pub_ts, title, sentiment, link FROM news WHERE code = ? "
                 "ORDER BY pub_ts DESC LIMIT 30", (code,))
        stat = _status("news_status")
        if news.empty:
            if demo:
                st.caption("데모 데이터에는 이 종목 뉴스가 없습니다.")
            elif not has_keys():
                st.caption("네이버 API 키가 없습니다 — `.env` 에 NAVER_CLIENT_ID · NAVER_CLIENT_SECRET 을 넣고 "
                           "저장하면 바로 반영됩니다 (대시보드를 다시 켤 필요 없음).")
            elif fresh:
                pass
            elif stat and not stat["ok"]:
                st.error(f"마지막 뉴스 수집 실패 ({stat['when']}): {stat['text']}")
            elif stat:
                st.caption(f"최근 기사가 없습니다 (마지막 수집 {stat['when']}).")
            else:
                st.caption("아직 뉴스를 수집하지 않았습니다 — 사이드바 🔄 지금 업데이트(또는 update.bat) 때 "
                           "분석 대상 전 종목을 받습니다. 아래 버튼으로 이 종목만 바로 받을 수도 있습니다.")
        else:
            news["감성"] = news["sentiment"].map(
                lambda s: "🔴 긍정" if s > 0.2 else "🔵 부정" if s < -0.2 else "⚪ 중립")
            st.dataframe(news[["pub_ts", "감성", "title", "link"]], hide_index=True, width="stretch",
                         height=380, column_config={
                             "link": st.column_config.LinkColumn("링크", display_text="열기"),
                             "pub_ts": "시각", "title": "제목"})
            if stat:
                st.caption(f"최근 30건 · 마지막 수집 {stat['when']}")
        if not demo and not st.session_state.get("readonly") and has_keys():
            if st.button("📰 지금 뉴스 받기", key="news_now",
                         help="이 종목의 최신 뉴스를 네이버에서 바로 받습니다 (키 확인용으로도 쓸 수 있음)"):
                _news_now(code)
    with c2:
        st.markdown("**최근 공시**")
        dis = q("SELECT date, kind, title, rcept_no FROM disclosures WHERE code = ? "
                "ORDER BY date DESC LIMIT 30", (code,))
        stat = _status("dart_status")
        if dis.empty:
            if demo:
                st.caption("데모 데이터에는 이 종목 공시가 없습니다.")
            elif not env("DART_API_KEY"):
                st.caption("DART 키가 없습니다 — `.env` 에 DART_API_KEY 를 넣으세요.")
            elif stat and not stat["ok"]:
                st.error(f"마지막 공시 수집 실패 ({stat['when']}): {stat['text']}")
            elif stat:
                st.caption(f"최근 1년 공시가 없습니다 (마지막 수집 {stat['when']}).")
            else:
                st.caption("아직 공시를 수집하지 않았습니다 — 사이드바 🔄 지금 업데이트(또는 update.bat) 때 받습니다.")
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
    intraday(code)
    chart(code)
    ph = q("SELECT asof, horizon, prob FROM predictions WHERE code = ? ORDER BY asof", (code,))
    if ph["asof"].nunique() > 1:
        st.markdown(f"**{TARGET_LABEL} 추이**")
        wide = ph.pivot(index="asof", columns="horizon", values="prob")
        wide.columns = [f"{horizon_label(h)}" for h in wide.columns]
        st.line_chart(wide, height=180)
    news_and_disclosures(code, demo)
