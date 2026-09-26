"""업종별 수급 집계 · 저녁 요약 문구."""
import pandas as pd
import pytest

from stocklab import alerts, db, sectors
from stocklab.config import load_settings


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "s.sqlite")
    days = pd.bdate_range("2026-09-01", periods=25).strftime("%Y-%m-%d")
    rows, flows = [], []
    for code, sector, fr in [("A00000", "반도체", 100), ("B00000", "반도체", 300), ("C00000", "은행", -50)]:
        c.execute("INSERT INTO sectors VALUES (?, ?, '2099-01-01')", (code, sector))
        for i, d in enumerate(days):
            rows.append({"code": code, "date": d, "close": 1000 + i * 10, "volume": 1, "value": 1})
            flows.append({"code": code, "date": d, "frgn_amt": fr, "orgn_amt": 10, "prsn_amt": -fr - 10})
    db.upsert(c, "prices", pd.DataFrame(rows))
    db.upsert(c, "flows", pd.DataFrame(flows))
    c.commit()
    return c


def test_sector_flows_sum_in_eok(conn):
    f = sectors.sector_flows(conn, days=20)
    semi = f[f["sector"] == "반도체"]
    assert len(semi) == 20 and semi["frgn"].iloc[-1] == pytest.approx(4.0)   # (100+300)백만 = 4억
    assert semi["n"].iloc[0] == 2


def test_sector_summary_rank_and_streak(conn):
    s = sectors.sector_summary(conn)
    assert s["sector"].tolist() == ["반도체", "은행"]
    semi = s.iloc[0]
    assert semi["frgn_5"] == pytest.approx(20.0) and semi["frgn_20"] == pytest.approx(80.0)
    assert semi["streak"] == 20 and s.iloc[1]["streak"] == -20
    assert semi["ret_5"] > 0


def test_update_sectors_only_fetches_missing(conn):
    class K:
        calls = []

        def quote(self, code):
            K.calls.append(code)
            return {"sector": "화학"}
    sectors.update_sectors(conn, K(), ["A00000", "D00000"])
    assert K.calls == ["D00000"]
    assert sectors.sector_members(conn, "화학") == ["D00000"]


def test_daily_summary_mentions_signal_change(conn):
    s = load_settings()
    conn.execute("INSERT INTO user_watchlist VALUES ('A00000', 'x')")
    conn.execute("INSERT INTO stocks VALUES ('A00000', '에이반도체', 'user')")
    db.upsert(conn, "predictions", pd.DataFrame([
        {"asof": "2026-10-02", "code": "A00000", "horizon": 5, "prob": 0.50, "model_id": "t"},
        {"asof": "2026-10-03", "code": "A00000", "horizon": 5, "prob": 0.60, "model_id": "t"}]))
    db.upsert(conn, "picks", pd.DataFrame([{"asof": "2026-10-03", "code": "A00000", "rank": 1, "prob": 0.6}]))
    text = alerts.daily_summary(conn, s)
    assert "10/03" in text and "에이반도체 0.60" in text
    assert "에이반도체 중립→강세" in text
    assert "반도체 +20억" in text
