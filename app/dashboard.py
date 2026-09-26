"""StockLab 대시보드.  실행: dashboard.bat  (또는 streamlit run app/dashboard.py)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stocklab import db  # noqa: E402
from stocklab.config import db_path, load_settings, model_dir  # noqa: E402

UP, DOWN = "#d6336c", "#1c7ed6"   # 한국 관례: 상승 빨강, 하락 파랑
st.set_page_config(page_title="StockLab", page_icon="📈", layout="wide")
S = load_settings()
H = int(S.model["horizon"])
TARGET_LABEL = "코스피 대비 강세 확률" if S.model["target"] == "excess" else "상승 확률"


@st.cache_data(ttl=300)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    if not db_path().exists():
        return pd.DataFrame()
    conn = db.connect()
    try:
        return db.query(conn, sql, params)
    finally:
        conn.close()


def names() -> dict[str, str]:
    d = q("SELECT code, name FROM stocks")
    return {**dict(zip(d["code"], d["name"])), **S.all_stocks} if not d.empty else S.all_stocks


def label(code: str) -> str:
    return f"{NAMES.get(code, code)} ({code})"


def signal(p: float) -> str:
    if pd.isna(p):
        return "-"
    return "강세" if p >= S.model["bullish"] else "약세" if p <= S.model["bearish"] else "중립"


def fmt_eok(x):  # 백만원 → 억원
    return None if pd.isna(x) else round(x / 100, 1)


NAMES = names()
meta = q("SELECT key, value FROM meta")
META = dict(zip(meta["key"], meta["value"])) if not meta.empty else {}

# ── 사이드바 ─────────────────────────────────────────────
with st.sidebar:
    st.title("StockLab")
    if META.get("demo") == "1":
        st.warning("**데모 모드** — 가상 데이터입니다. 실제 시세가 아닙니다.")
    st.caption(f"데이터: `{db_path().name}`")
    st.caption(f"마지막 업데이트: {META.get('last_update', '없음')}")
    if st.button("새로고침"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption(f"예측: {H}거래일 뒤 {TARGET_LABEL}\n\n"
               f"강세 ≥ {S.model['bullish']:.2f} / 약세 ≤ {S.model['bearish']:.2f}")
    st.caption("⚠️ 참고용 통계 모델입니다. 투자 판단과 책임은 본인에게 있습니다.")

prices = q("SELECT * FROM prices")
if prices.empty:
    st.info("데이터가 없습니다. `update.bat`(실제) 또는 `demo.bat`(가상)을 먼저 실행하세요.")
    st.stop()
prices["date"] = pd.to_datetime(prices["date"])
prices = prices.sort_values(["code", "date"])

pred = q("SELECT * FROM predictions WHERE horizon = ?", (H,))
latest_pred = (pred.sort_values("asof").groupby("code").tail(1).set_index("code")["prob"]
               if not pred.empty else pd.Series(dtype=float))
flows = q("SELECT * FROM flows")
if not flows.empty:
    flows["date"] = pd.to_datetime(flows["date"])


def summary_table(codes: list[str]) -> pd.DataFrame:
    rows = []
    for code in codes:
        g = prices[prices["code"] == code]
        if g.empty:
            continue
        c = g["close"].to_numpy()
        f = flows[flows["code"] == code].sort_values("date").tail(5) if not flows.empty else pd.DataFrame()
        p = latest_pred.get(code, np.nan)
        rows.append({
            "종목": label(code), "기준일": g["date"].iloc[-1].date(), "종가": c[-1],
            "1일%": (c[-1] / c[-2] - 1) * 100 if len(c) > 1 else np.nan,
            "5일%": (c[-1] / c[-6] - 1) * 100 if len(c) > 5 else np.nan,
            "20일%": (c[-1] / c[-21] - 1) * 100 if len(c) > 20 else np.nan,
            "외국인5일(억)": fmt_eok(f["frgn_amt"].sum()) if len(f) else None,
            "기관5일(억)": fmt_eok(f["orgn_amt"].sum()) if len(f) else None,
            TARGET_LABEL: p, "신호": signal(p),
        })
    return pd.DataFrame(rows)


COLCFG = {
    "종가": st.column_config.NumberColumn(format="localized"),
    "1일%": st.column_config.NumberColumn(format="%.2f"),
    "5일%": st.column_config.NumberColumn(format="%.2f"),
    "20일%": st.column_config.NumberColumn(format="%.2f"),
    TARGET_LABEL: st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
}


def color_signed(v):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return f"color: {UP}" if v > 0 else f"color: {DOWN}" if v < 0 else ""
    return {"강세": f"color: {UP}; font-weight: 600", "약세": f"color: {DOWN}; font-weight: 600"}.get(v, "")


NUMFMT = {"종가": "{:,.0f}", "1일%": "{:+.2f}", "5일%": "{:+.2f}", "20일%": "{:+.2f}",
          "외국인5일(억)": "{:+,.1f}", "기관5일(억)": "{:+,.1f}", "외국인20일(억)": "{:+,.1f}",
          "거래량대비%": "{:+.1f}", "연속일": "{:+d}"}


def styled(df: pd.DataFrame, signed: list[str]):
    fmt = {k: v for k, v in NUMFMT.items() if k in df}
    return (df.style.map(color_signed, subset=[c for c in signed if c in df])
            .format(fmt, na_rep="-"))


def show_table(df: pd.DataFrame, height: int | None = None):
    signed = ["1일%", "5일%", "20일%", "외국인5일(억)", "기관5일(억)", "신호"]
    kw = {"height": height} if height else {}
    st.dataframe(styled(df, signed), column_config=COLCFG, hide_index=True, width="stretch", **kw)


tab_watch, tab_detail, tab_frgn, tab_model, tab_macro = st.tabs(
    ["관심종목", "종목 상세", "외국인 수급", "예측 모델", "거시지표"])

# ── 1. 관심종목 ─────────────────────────────────────────
with tab_watch:
    have = set(prices["code"])
    watch = [c for c in S.watchlist if c in have] or sorted(have)[:6]
    st.subheader("관심종목")
    show_table(summary_table(watch))

    if not latest_pred.empty:
        st.subheader(f"전체 종목 예측 순위 ({H}거래일, {TARGET_LABEL})")
        c1, c2 = st.columns(2)
        ranked = latest_pred.sort_values(ascending=False)
        with c1:
            st.markdown("**상위 10**")
            show_table(summary_table(ranked.head(10).index.tolist()))
        with c2:
            st.markdown("**하위 10**")
            show_table(summary_table(ranked.tail(10).index[::-1].tolist()))

# ── 2. 종목 상세 ─────────────────────────────────────────
with tab_detail:
    codes = sorted(prices["code"].unique(), key=lambda c: (c not in S.watchlist, NAMES.get(c, c)))
    code = st.selectbox("종목", codes, format_func=label)
    period = st.radio("기간", ["3개월", "6개월", "1년", "3년", "전체"], index=2, horizontal=True)
    days = {"3개월": 92, "6개월": 183, "1년": 365, "3년": 1095, "전체": 100000}[period]
    g = prices[prices["code"] == code].copy()
    g["ma20"], g["ma60"] = g["close"].rolling(20).mean(), g["close"].rolling(60).mean()
    g = g[g["date"] >= g["date"].max() - pd.Timedelta(days=days)]
    fl = flows[(flows["code"] == code) & (flows["date"] >= g["date"].min())] if not flows.empty else pd.DataFrame()

    p = latest_pred.get(code, np.nan)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("종가", f"{g['close'].iloc[-1]:,.0f}",
              f"{(g['close'].iloc[-1] / g['close'].iloc[-2] - 1) * 100:.2f}%" if len(g) > 1 else None,
              delta_color="inverse")
    m2.metric(TARGET_LABEL, "-" if pd.isna(p) else f"{p:.2f}", signal(p), delta_color="off")
    if len(fl):
        m3.metric("외국인 5일 순매수", f"{fl.tail(5)['frgn_amt'].sum() / 100:,.1f}억")
        m4.metric("기관 5일 순매수", f"{fl.tail(5)['orgn_amt'].sum() / 100:,.1f}억")

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
    fig.update_layout(height=720, xaxis_rangeslider_visible=False, barmode="group",
                      margin=dict(t=30, b=10), legend=dict(orientation="h", y=1.02))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    st.plotly_chart(fig, width="stretch")

    ph = pred[pred["code"] == code].sort_values("asof") if not pred.empty else pd.DataFrame()
    if len(ph) > 1:
        st.markdown(f"**{TARGET_LABEL} 추이**")
        st.line_chart(ph.set_index("asof")["prob"], height=180)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**최근 뉴스**")
        news = q("SELECT pub_ts, title, sentiment, link FROM news WHERE code = ? "
                 "ORDER BY pub_ts DESC LIMIT 30", (code,))
        if news.empty:
            st.caption("뉴스 없음 (네이버 API 키 필요, 관심종목·외국인 상위 종목만 수집)")
        else:
            news["감성"] = news["sentiment"].map(lambda s: "🔴 긍정" if s > 0.2 else "🔵 부정" if s < -0.2 else "⚪ 중립")
            st.dataframe(news[["pub_ts", "감성", "title", "link"]], hide_index=True,
                         width="stretch", height=400,
                         column_config={"link": st.column_config.LinkColumn("링크", display_text="열기"),
                                        "pub_ts": "시각", "title": "제목"})
    with c2:
        st.markdown("**최근 공시**")
        dis = q("SELECT date, kind, title, rcept_no FROM disclosures WHERE code = ? "
                "ORDER BY date DESC LIMIT 30", (code,))
        if dis.empty:
            st.caption("공시 없음 (DART API 키 필요)")
        else:
            dis["link"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + dis["rcept_no"]
            st.dataframe(dis[["date", "kind", "title", "link"]], hide_index=True,
                         width="stretch", height=400,
                         column_config={"link": st.column_config.LinkColumn("원문", display_text="열기"),
                                        "date": "접수일", "kind": "분류", "title": "제목"})

# ── 3. 외국인 수급 ───────────────────────────────────────
with tab_frgn:
    sc = q("SELECT * FROM screener WHERE asof = (SELECT MAX(asof) FROM screener) ORDER BY rank")
    st.subheader("외국인 매수 강도 순위")
    if sc.empty:
        st.info("수급 데이터가 없습니다. update 를 실행하세요.")
    else:
        st.caption(f"기준일 {sc['asof'].iloc[0]} · 점수 = 5일·20일 순매수금액, 거래량 대비 순매수 비율, "
                   "연속 순매수일의 순위 평균 (1에 가까울수록 강함)")
        out = pd.DataFrame({
            "순위": sc["rank"], "종목": sc["code"].map(label), "점수": sc["score"],
            "외국인5일(억)": sc["frgn_amt_5"].map(fmt_eok), "외국인20일(억)": sc["frgn_amt_20"].map(fmt_eok),
            "거래량대비%": sc["frgn_ratio_5"] * 100, "연속일": sc["streak"],
            "기관5일(억)": sc["orgn_amt_5"].map(fmt_eok),
            TARGET_LABEL: sc["code"].map(latest_pred),
        })
        out["신호"] = out[TARGET_LABEL].map(signal)
        st.dataframe(
            styled(out, ["외국인5일(억)", "외국인20일(억)", "거래량대비%", "연속일", "기관5일(억)", "신호"]),
            hide_index=True, width="stretch", height=560,
            column_config={"점수": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
                           TARGET_LABEL: COLCFG[TARGET_LABEL]})

# ── 4. 예측 모델 ─────────────────────────────────────────
with tab_model:
    path = model_dir() / f"model_h{H}.json"
    if not path.exists():
        st.info("학습된 모델이 없습니다. `python -m stocklab train` 을 실행하세요.")
    else:
        mm = json.loads(path.read_text(encoding="utf-8"))
        cm = mm.get("cv_mean", {})
        st.subheader(f"모델 {mm['model_id']} — {mm['horizon']}거래일 뒤 {TARGET_LABEL}")
        st.caption(f"학습 데이터 {mm['data_from']} ~ {mm['data_to']} · {mm['n_codes']}종목 · {mm['n_rows']:,}행")
        if cm:
            c = st.columns(4)
            c[0].metric("AUC", f"{cm['auc']:.3f}", help="0.5 = 동전 던지기. 주식에서 0.53~0.56 이면 의미 있는 수준")
            c[1].metric("정확도", f"{cm['accuracy'] * 100:.1f}%",
                        f"{(cm['accuracy'] - cm['baseline_acc']) * 100:+.1f}%p vs 기준선")
            c[2].metric("상위 20% 평균수익", f"{cm['top20_ret'] * 100:.2f}%",
                        f"{(cm['top20_ret'] - cm['all_ret']) * 100:+.2f}%p vs 전체")
            c[3].metric("하위 20% 평균수익", f"{cm['bottom20_ret'] * 100:.2f}%",
                        f"{(cm['bottom20_ret'] - cm['all_ret']) * 100:+.2f}%p vs 전체")
            st.markdown(
                "**읽는 법** — 과거 데이터를 시간 순서대로 잘라 '학습 → 바로 다음 기간 예측'을 5번 반복한 결과입니다. "
                "상위 20%(모델이 가장 강하게 본 종목)의 수익이 하위 20%보다 꾸준히 높아야 쓸모 있는 모델입니다. "
                "기간(fold)마다 결과가 들쭉날쭉하면 우연일 가능성이 큽니다.")
            cv = pd.DataFrame(mm["cv"])
            cv["test_start"], cv["test_end"] = cv["test_start"].str[:10], cv["test_end"].str[:10]
            for k in ("top20_ret", "bottom20_ret", "all_ret"):
                cv[k] = cv[k] * 100
            st.dataframe(cv.round(4).rename(columns={"test_start": "검증시작", "test_end": "검증끝", "top20_hit": "상위20%적중",
                                            "top20_ret": "상위20%수익%", "bottom20_ret": "하위20%수익%",
                                            "all_ret": "전체수익%", "baseline_acc": "기준선정확도",
                                            "n_train": "학습행", "n_test": "검증행", "accuracy": "정확도"}),
                         hide_index=True, width="stretch")
        imp = pd.Series(mm["importance"]).head(20)[::-1]
        fig = go.Figure(go.Bar(x=imp.values, y=imp.index, orientation="h", marker_color="#5c7cfa"))
        fig.update_layout(title="중요 특징값 Top 20 (" + ("정보 이득" if mm.get("engine", "lightgbm") == "lightgbm" else "순열 중요도") + ")", height=560, margin=dict(l=10, t=40))
        st.plotly_chart(fig, width="stretch")
        st.caption("frgn_* 외국인 · orgn_* 기관 수급 / ret_* 수익률 / ma_gap_* 이동평균 이격 / *_r1·r5 거시지표 변화율 / "
                   "news_* 뉴스 감성 / dart_* 공시")

# ── 5. 거시지표 ─────────────────────────────────────────
with tab_macro:
    mac = q("SELECT series, date, value FROM macro")
    if mac.empty:
        st.info("거시지표가 없습니다.")
    else:
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
        have = set(prices["code"])
        watch = [c for c in S.watchlist if c in have] or sorted(have)[:6]
        ret = prices[prices["code"].isin(watch)].pivot(index="date", columns="code", values="close").pct_change(fill_method=None)
        ret = ret[ret.index >= ret.index.max() - pd.DateOffset(years=1)]
        both = ret.join(chg, how="inner")
        corr = both.corr().loc[ret.columns, chg.columns].rename(index=label).dropna(axis=1, how="all")
        fig = go.Figure(go.Heatmap(z=corr.values, x=corr.columns, y=corr.index, zmin=-1, zmax=1,
                                   colorscale=[[0, DOWN], [0.5, "#f8f9fa"], [1, UP]],
                                   text=np.round(corr.values, 2), texttemplate="%{text}"))
        fig.update_layout(height=120 + 45 * len(corr), margin=dict(t=40), xaxis=dict(side="top"))
        st.plotly_chart(fig, width="stretch")
