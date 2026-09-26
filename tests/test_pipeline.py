"""특징값의 미래 정보 누설 여부 + 데모 데이터로 전체 흐름 검증."""
import numpy as np
import pandas as pd
import pytest

from stocklab import db, demo, features, model, screener
from stocklab.collectors.news import effective_date
from stocklab.sentiment import lexicon_score


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    c = db.connect(tmp_path_factory.mktemp("d") / "demo.sqlite")
    demo.generate(c, n_days=400)
    return c


def test_features_do_not_use_future_data(conn):
    """t 이후 데이터를 망가뜨려도 t 시점 특징값이 그대로여야 한다."""
    panel = features.build_panel(conn, horizon=5)
    cut = panel["date"].sort_values().unique()[-60]
    before = panel[panel["date"] <= cut].set_index(["code", "date"])
    cut_s = pd.Timestamp(cut).strftime("%Y-%m-%d")
    conn.execute("UPDATE prices SET close = close * 3, volume = volume * 5 WHERE date > ?", (cut_s,))
    conn.execute("UPDATE flows SET frgn_qty = -frgn_qty * 9 WHERE date > ?", (cut_s,))
    conn.execute("UPDATE macro SET value = value * 2 WHERE date >= ? AND region = 'GLOBAL'", (cut_s,))
    conn.execute("UPDATE macro SET value = value * 2 WHERE date > ? AND region = 'KR'", (cut_s,))
    conn.execute("UPDATE news SET sentiment = -sentiment WHERE eff_date > ?", (cut_s,))
    try:
        after = features.build_panel(conn, horizon=5)
        after = after[after["date"] <= cut].set_index(["code", "date"])
        feats = features.feature_columns(before.reset_index())
        pd.testing.assert_frame_equal(before[feats], after[feats])
    finally:
        conn.rollback()


def test_global_macro_uses_previous_day_only(conn):
    panel = features.build_panel(conn, horizon=5)
    sp = db.query(conn, "SELECT date, value FROM macro WHERE series='SP500' ORDER BY date")
    sp["r1"] = sp["value"].pct_change()
    d = panel["date"].sort_values().unique()[200]
    got = panel.loc[panel["date"] == d, "SP500_r1"].iloc[0]
    prev = sp[pd.to_datetime(sp["date"]) < d].iloc[-1]["r1"]
    assert got == pytest.approx(prev)


def test_train_predict_and_screen(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(model, "model_dir", lambda: tmp_path)
    panel = features.build_panel(conn, horizon=5, target="excess")
    meta = model.train(panel, 5, "excess")
    assert meta["cv"] and 0 < meta["cv_mean"]["auc"] < 1
    pred = model.predict_latest(panel, model.load(5))
    assert len(pred) == panel["code"].nunique()
    assert pred["prob"].between(0, 1).all()
    ranked = screener.run(conn)
    assert ranked["rank"].tolist() == list(range(1, len(ranked) + 1))


def test_news_after_close_counts_next_day():
    kst = pd.Timestamp("2026-09-25 15:29", tz="Asia/Seoul").to_pydatetime()
    assert effective_date(kst) == "2026-09-25"
    assert effective_date(kst + pd.Timedelta(minutes=2)) == "2026-09-26"


def test_lexicon():
    assert lexicon_score("삼성전자, <b>흑자전환</b> 성공…신고가 돌파") > 0
    assert lexicon_score("LG디스플레이 적자 확대 우려") < 0
    assert lexicon_score("오늘의 날씨") == 0
