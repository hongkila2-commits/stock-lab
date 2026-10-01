"""예측 모델: 검증 성적 · 추천 종목 실제 성적 · 중요 특징."""
from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import DOWN, H as H0, HORIZONS, S, TARGET_LABEL, UP, horizon_label
from stocklab import db
from stocklab.config import db_path, model_dir
from stocklab.picks import label as feat_label, pick_performance


@st.cache_data(ttl=300)
def performance(H: int = H0) -> pd.DataFrame:
    if not db_path().exists():
        return pd.DataFrame()
    conn = db.connect()
    try:
        return pick_performance(conn, H, S.model["target"] == "excess")
    finally:
        conn.close()


def tracking(H: int = H0) -> None:
    st.subheader(f"추천 {S.picks['count']}종목 실제 성적")
    perf = performance(H)
    what = "코스피 대비 초과수익" if S.model["target"] == "excess" else "수익률"
    if perf.empty:
        st.info(f"추천 기록이 쌓이고 {H}거래일이 지나면 여기에 실제 성적이 표시됩니다. "
                "매일 update 를 실행하면 날짜별 추천이 저장됩니다.")
        return
    c = st.columns(4)
    c[0].metric("평가된 추천일", f"{len(perf)}일")
    c[1].metric(f"추천 종목 평균 {what}", f"{perf['pick_ret'].mean() * 100:+.2f}%")
    c[2].metric(f"전체 평균 {what}", f"{perf['all_ret'].mean() * 100:+.2f}%")
    c[3].metric("추천 종목 적중률", f"{perf['hit'].mean() * 100:.0f}%",
                help=f"{H}거래일 뒤 {what} 이 플러스였던 추천 종목 비율")
    fig = go.Figure()
    fig.add_bar(x=perf["asof"], y=(perf["pick_ret"] - perf["all_ret"]) * 100, name="추천 − 전체 (%p)",
                marker_color=[UP if v > 0 else DOWN for v in perf["pick_ret"] - perf["all_ret"]])
    fig.add_scatter(x=perf["asof"], y=((perf["pick_ret"] - perf["all_ret"]).expanding().mean() * 100),
                    name="누적 평균 차이", line=dict(color="#212529"))
    fig.update_layout(height=320, margin=dict(t=30, b=10), legend=dict(orientation="h", y=1.1),
                      yaxis_title="%p")
    st.plotly_chart(fig, width="stretch")
    st.caption(f"막대: 그날 추천한 종목들의 {H}거래일 뒤 평균 {what}에서, 그날 예측한 전체 종목 평균을 뺀 값. "
               "빨간 막대가 꾸준히 많아야 추천이 의미 있습니다.")


def render() -> None:
    st.header("예측 모델")
    H = H0
    if len(HORIZONS) > 1:
        H = st.segmented_control("예측 기간", HORIZONS, default=HORIZONS[0], key="model_h",
                                 format_func=lambda x: f"{horizon_label(x)} ({x}거래일)") or HORIZONS[0]
    path = model_dir() / f"model_h{H}.json"
    if not path.exists():
        st.info("학습된 모델이 없습니다. `train.bat` 또는 `update.bat` 을 실행하세요.")
        return
    mm = json.loads(path.read_text(encoding="utf-8"))
    cm = mm.get("cv_mean", {})
    engine = "LightGBM" if mm.get("engine", "lightgbm") == "lightgbm" else "scikit-learn"
    st.caption(f"모델 {mm['model_id']} ({engine}) · {mm['horizon']}거래일 뒤 {TARGET_LABEL} · "
               f"학습 {mm['data_from']} ~ {mm['data_to']} · {mm['n_codes']}종목 · {mm['n_rows']:,}행")
    tracking(H)

    st.subheader("과거 데이터 검증 (시간 순서대로)")
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
            "과거 데이터를 시간 순서대로 잘라 '학습 → 바로 다음 기간 예측'을 5번 반복한 결과입니다. "
            "상위 20%(모델이 가장 강하게 본 종목)의 수익이 하위 20%보다 **모든 기간에서 꾸준히** 높아야 "
            "쓸모 있는 모델입니다.")
        cv = pd.DataFrame(mm["cv"])
        cv["test_start"], cv["test_end"] = cv["test_start"].str[:10], cv["test_end"].str[:10]
        for k in ("top20_ret", "bottom20_ret", "all_ret"):
            cv[k] = cv[k] * 100
        st.dataframe(cv.round(4).rename(columns={
            "fold": "구간", "test_start": "검증시작", "test_end": "검증끝", "n_train": "학습행",
            "n_test": "검증행", "auc": "AUC", "accuracy": "정확도", "baseline_acc": "기준선정확도",
            "top20_hit": "상위20%적중", "top20_ret": "상위20%수익%", "bottom20_ret": "하위20%수익%",
            "all_ret": "전체수익%"}), hide_index=True, width="stretch")

    imp = pd.Series(mm["importance"]).head(20)[::-1]
    imp.index = [feat_label(f) for f in imp.index]
    fig = go.Figure(go.Bar(x=imp.values, y=imp.index, orientation="h", marker_color="#5c7cfa"))
    fig.update_layout(title=f"예측에 중요한 지표 Top 20 ({'정보 이득' if engine == 'LightGBM' else '순열 중요도'})",
                      height=580, margin=dict(l=10, t=40))
    st.plotly_chart(fig, width="stretch")
