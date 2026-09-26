"""전체 종목 목록 파서 — 공공데이터포털·네이버 가짜 응답으로 검증 (실제 호출 없음)."""
from datetime import date

import pandas as pd
import pytest

from stocklab import db
from stocklab.collectors import listing as L


class Resp:
    def __init__(self, data=None, text=""):
        self._d, self.text = data, text

    def json(self):
        if self._d is None:
            raise ValueError("not json")
        return self._d

    def raise_for_status(self):
        pass


def datagokr_item(code, name, market, close, cap_won):
    return {"basDt": "20260925", "srtnCd": code, "itmsNm": name, "mrktCtg": market,
            "clpr": str(close), "vs": "-500", "fltRt": "-.69", "trqu": "1000",
            "trPrc": "71500000", "lstgStCnt": "100", "mrktTotAmt": str(cap_won)}


class FakeDataGoKr:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout, params):
        self.calls.append(params)
        if params["basDt"] == "20260928":          # 월요일: 아직 미공개 → 빈 결과
            return Resp({"response": {"header": {"resultCode": "00"},
                                      "body": {"items": {"item": []}, "totalCount": 0}}})
        items = [datagokr_item("A005930", "삼성전자", "KOSPI", 71500, 426_000_000_000_000),
                 datagokr_item("247540", "에코프로비엠", "KOSDAQ", 150000, 14_000_000_000_000),
                 datagokr_item("123450", "코넥스기업", "KONEX", 1000, 1e10)]
        return Resp({"response": {"header": {"resultCode": "00"},
                                  "body": {"items": {"item": items}, "totalCount": 3}}})


def test_datagokr_parses_and_walks_back_to_last_business_day():
    fake = FakeDataGoKr()
    df = L.fetch_datagokr("abc%2Bdef%3D%3D", today=date(2026, 9, 28), session=fake)
    assert fake.calls[0]["serviceKey"] == "abc+def=="      # Encoding 키를 풀어서 보냄
    assert [c["basDt"] for c in fake.calls] == ["20260928", "20260925"]   # 주말 건너뜀
    assert df["code"].tolist() == ["005930", "247540"]      # A 접두어 제거, 코넥스 제외
    sam = df.iloc[0]
    assert sam["market_cap"] == pytest.approx(4_260_000)   # 억원
    assert sam["change_pct"] == pytest.approx(-0.69)
    assert sam["source"] == "공공데이터포털" and sam["asof"] == "2026-09-25"


def test_datagokr_key_error_is_reported():
    class Bad:
        def get(self, *a, **k):
            return Resp(None, "<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>"
                              "SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>")
    with pytest.raises(RuntimeError, match="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        L.fetch_datagokr("k", today=date(2026, 9, 25), session=Bad())


class FakeNaver:
    def get(self, url, headers, timeout, params):
        market = url.rsplit("/", 1)[-1]
        if params["page"] > 1:
            return Resp({"stocks": []})
        stocks = [{"itemCode": "005930" if market == "KOSPI" else "247540",
                   "stockName": "삼성전자" if market == "KOSPI" else "에코프로비엠",
                   "stockEndType": "stock", "closePrice": "71,500", "fluctuationsRatio": "-0.69",
                   "accumulatedTradingVolume": "12,345", "accumulatedTradingValue": "883",
                   "marketValue": "4,268,532", "localTradedAt": "2026-09-25T15:30:00+09:00"},
                  {"itemCode": "069500", "stockName": "KODEX 200", "stockEndType": "etf",
                   "closePrice": "35,000", "marketValue": "70,000"}]
        return Resp({"stocks": stocks})


def test_naver_parses_and_skips_etf():
    df = L.fetch_naver(session=FakeNaver(), page_size=2)
    assert set(df["code"]) == {"005930", "247540"}
    sam = df[df["code"] == "005930"].iloc[0]
    assert sam["close"] == 71500 and sam["market_cap"] == 4268532 and sam["asof"] == "2026-09-25"


def test_is_common():
    assert L.is_common("005930", "삼성전자")
    assert not L.is_common("005935", "삼성전자우")
    assert not L.is_common("123450", "하나15호스팩")


def test_auto_universe_takes_common_stocks_by_market_cap(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "listing", pd.DataFrame([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "close": 1, "market_cap": 400},
        {"code": "005935", "name": "삼성전자우", "market": "KOSPI", "close": 1, "market_cap": 300},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "close": 1, "market_cap": 200},
        {"code": "111110", "name": "작은회사", "market": "KOSDAQ", "close": 1, "market_cap": 1},
    ]))
    assert list(L.auto_universe(conn, 2)) == ["005930", "000660"]
