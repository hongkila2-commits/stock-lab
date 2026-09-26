"""추천 선정 필터 · 예측 근거 · 추천 성적 추적."""
import pandas as pd
import pytest

from stocklab import db, demo, features, model, picks


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("p")
    conn = db.connect(d / "demo.sqlite")
    demo.generate(conn, n_days=400, n_stocks=20, n_listing_only=10)
    panel = features.build_panel(conn, horizon=5, target="excess")
    old = model.model_dir
    model.model_dir = lambda: d
    try:
        model.train(panel, 5, "excess")
        bundle = model.load(5)
    finally:
        model.model_dir = old
    pred = model.predict_latest(panel, bundle)
    db.upsert(conn, "predictions", pd.DataFrame({
        "asof": pred["date"].dt.strftime("%Y-%m-%d"), "code": pred["code"], "horizon": 5,
        "prob": pred["prob"], "model_id": "t"}))
    conn.commit()
    return conn, panel, bundle


def test_picks_respect_filters(env):
    conn, _, _ = env
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM prices")]
    # 한 종목은 거래대금을 거의 0으로, 한 종목은 최근 데이터를 지워 '오래된 시세'로 만든다
    thin, stale = codes[0], codes[1]
    conn.execute("UPDATE prices SET value = 1 WHERE code = ?", (thin,))
    last = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    conn.execute("DELETE FROM prices WHERE code = ? AND date = ?", (stale, last))
    try:
        top = picks.select_picks(conn, count=30, min_value_eok=10)
        assert thin not in set(top["code"]) and stale not in set(top["code"])
        assert top["prob"].is_monotonic_decreasing
        assert top["rank"].tolist() == list(range(1, len(top) + 1))
        assert len(top) == len(codes) - 2
    finally:
        conn.rollback()


def test_explanations_lightgbm_and_fallback(env):
    _, panel, bundle = env
    ex = picks.explanations(panel, bundle, top_k=3)
    assert ex.groupby("code").size().max() == 3
    if model.ENGINE == "lightgbm":                          # LightGBM → 기여도 있음
        assert ex["contrib"].notna().all()
    assert ex["pct"].between(0, 1).all()

    class NoBooster:                                        # scikit-learn 엔진 흉내
        pass
    ex2 = picks.explanations(panel, {"model": NoBooster(), "meta": bundle["meta"]}, top_k=3)
    assert ex2["contrib"].isna().all()
    assert not ex2["feature"].map(picks.is_macro).any()     # 백분위 설명엔 시장 공통 지표 제외


def test_describe():
    assert picks.describe("frgn_ratio_5", 0.1, 0.96, 0.2) == "외국인 5일 순매수 비중 상위 4% ▲"
    assert picks.describe("ret_20", -0.1, 0.1, -0.1) == "20일 수익률 하위 10% ▼"
    assert picks.describe("SOX_r5", 0.034, 0.5, None) == "[시장] 반도체지수 5일 변화 +3.4%"
    assert picks.describe("VIX_lvl", 21.3, 0.5, 0.1) == "[시장] VIX 수준 21.3 ▲"


def test_pick_performance(env):
    conn, _, _ = env
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
    d = dates[-20]                                          # 5거래일 뒤 결과가 있는 날
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM prices")][:3]
    db.upsert(conn, "picks", pd.DataFrame({"asof": d, "code": codes, "rank": [1, 2, 3], "prob": 0.6}))
    db.upsert(conn, "predictions", pd.DataFrame({"asof": d, "code": codes, "horizon": 5,
                                                 "prob": 0.6, "model_id": "t"}))
    perf = picks.pick_performance(conn, 5, excess=False)
    row = perf[perf["asof"] == d].iloc[0]
    px = db.query(conn, "SELECT code, date, close FROM prices")
    exp = []
    for c in codes:
        s = px[px["code"] == c].sort_values("date").reset_index(drop=True)
        i = s.index[s["date"] == d][0]
        exp.append(s.at[i + 5, "close"] / s.at[i, "close"] - 1)
    assert row["pick_ret"] == pytest.approx(sum(exp) / 3)
    assert row["n"] == 3
