"""한국투자증권 클라이언트를 가짜 서버 응답으로 검증 (실제 API 호출 없음)."""
from datetime import date, timedelta

import pandas as pd
import pytest

from stocklab.kis import KisClient, KisError


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self._d


def bdays(end: date, n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(end=end, periods=n)]


class FakeKis:
    """일봉은 요청한 기간 안에서 최신순 최대 100건만 돌려준다 (실제 API 와 같은 동작)."""

    def __init__(self, days: list[date]):
        self.days, self.tokens, self.gets, self.rate_limit_once = days, 0, [], False

    def post(self, url, json, timeout):
        self.tokens += 1
        return Resp({"access_token": f"T{self.tokens}", "expires_in": 86400})

    def get(self, url, headers, params, timeout):
        self.gets.append(params)
        if self.rate_limit_once:
            self.rate_limit_once = False
            return Resp({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과"})
        if "inquire-investor" in url:
            return Resp({"rt_cd": "0", "output": [
                {"stck_bsop_date": "20260925", "frgn_ntby_qty": "1000", "orgn_ntby_qty": "-200",
                 "prsn_ntby_qty": "-800", "frgn_ntby_tr_pbmn": "70", "orgn_ntby_tr_pbmn": "-14",
                 "prsn_ntby_tr_pbmn": "-56"},
                {"stck_bsop_date": "20260926", "frgn_ntby_qty": "", "orgn_ntby_qty": "",
                 "prsn_ntby_qty": "", "frgn_ntby_tr_pbmn": "", "orgn_ntby_tr_pbmn": "",
                 "prsn_ntby_tr_pbmn": ""},
            ]})
        start = date(*map(int, (params["FID_INPUT_DATE_1"][:4], params["FID_INPUT_DATE_1"][4:6],
                               params["FID_INPUT_DATE_1"][6:])))
        end = date(*map(int, (params["FID_INPUT_DATE_2"][:4], params["FID_INPUT_DATE_2"][4:6],
                             params["FID_INPUT_DATE_2"][6:])))
        sel = [d for d in self.days if start <= d <= end][::-1][:100]
        return Resp({"rt_cd": "0", "output2": [
            {"stck_bsop_date": d.strftime("%Y%m%d"), "stck_oprc": "100", "stck_hgpr": "110",
             "stck_lwpr": "90", "stck_clpr": str(100 + i), "acml_vol": "1000", "acml_tr_pbmn": "100000"}
            for i, d in enumerate(sel)]})


def client(tmp_path, fake):
    return KisClient("key", "secret", "paper", token_path=tmp_path / "t.json",
                     session=fake, min_interval=0, sleep=lambda s: None)


def test_daily_prices_pages_backwards_without_gaps(tmp_path):
    end = date(2026, 9, 25)
    days = bdays(end, 350)
    fake = FakeKis(days)
    df = client(tmp_path, fake).daily_prices("005930", days[0], end)
    assert len(df) == 350
    assert df["date"].is_unique and df["date"].is_monotonic_increasing
    assert len(fake.gets) == 4          # 100 + 100 + 100 + 50


def test_token_is_cached_across_clients(tmp_path):
    fake = FakeKis(bdays(date(2026, 9, 25), 5))
    client(tmp_path, fake).daily_prices("005930", date(2026, 9, 1), date(2026, 9, 25))
    client(tmp_path, fake).daily_prices("005930", date(2026, 9, 1), date(2026, 9, 25))
    assert fake.tokens == 1             # 두 번째 실행은 파일에 저장된 토큰을 재사용


def test_rate_limit_is_retried(tmp_path):
    fake = FakeKis(bdays(date(2026, 9, 25), 5))
    fake.rate_limit_once = True
    df = client(tmp_path, fake).daily_prices("005930", date(2026, 9, 1), date(2026, 9, 25))
    assert len(df) == 5


def test_investor_flow_drops_unconfirmed_today_row(tmp_path):
    df = client(tmp_path, FakeKis([])).investor_flow("005930")
    assert df["date"].tolist() == ["2026-09-25"]
    assert df["frgn_qty"].iloc[0] == 1000


def test_error_is_raised_with_code(tmp_path):
    class Bad(FakeKis):
        def get(self, *a, **k):
            return Resp({"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스 코드"})
    with pytest.raises(KisError, match="OPSQ0002"):
        client(tmp_path, Bad([])).investor_flow("005930")


def test_missing_keys_give_clear_message():
    with pytest.raises(ValueError, match="KIS_APP_KEY"):
        KisClient("", "", "paper")


# ── 서버 응답 지연 (사용자 PC 에서 실제로 난 ReadTimeout) ──────────────────────
import requests  # noqa: E402

from stocklab import db  # noqa: E402
from stocklab.collectors import market  # noqa: E402


class Flaky(FakeKis):
    """처음 n 번의 GET 은 ReadTimeout (모의투자 서버가 응답을 늦게 주는 상황)."""

    def __init__(self, days, fail_times):
        super().__init__(days)
        self.fail_times = fail_times

    def get(self, url, headers, params, timeout):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")
        return super().get(url, headers, params, timeout)


def slow_client(tmp_path, fake, waits):
    return KisClient("key", "secret", "paper", token_path=tmp_path / "t.json",
                     session=fake, min_interval=0, sleep=waits.append)


def test_read_timeout_is_retried_then_succeeds(tmp_path):
    waits = []
    fake = Flaky(bdays(date(2026, 9, 25), 5), fail_times=2)
    df = slow_client(tmp_path, fake, waits).daily_prices("005930", date(2026, 9, 1), date(2026, 9, 25))
    assert len(df) == 5 and waits == [2, 5]


def test_persistent_timeout_becomes_kis_error(tmp_path):
    waits = []
    with pytest.raises(KisError, match="NETWORK"):
        slow_client(tmp_path, Flaky([], fail_times=99), waits).investor_flow("005930")
    assert waits == [2, 5, 10]


def test_token_timeout_is_retried(tmp_path):
    class SlowToken(FakeKis):
        calls = 0

        def post(self, url, json, timeout):
            SlowToken.calls += 1
            if SlowToken.calls == 1:
                raise requests.exceptions.ConnectTimeout("connect timeout")
            return super().post(url, json, timeout)
    waits = []
    assert slow_client(tmp_path, SlowToken([]), waits).token() == "T1" and waits == [2]


def test_update_prices_skips_failed_stock_and_continues(tmp_path):
    conn = db.connect(tmp_path / "m.sqlite")
    days = bdays(date(2026, 9, 25), 5)

    class OneBad(FakeKis):
        def get(self, url, headers, params, timeout):
            if params.get("FID_INPUT_ISCD") == "000660":
                raise requests.exceptions.ReadTimeout("Read timed out.")
            return super().get(url, headers, params, timeout)
    kis = slow_client(tmp_path, OneBad(days), [])
    r = market.update_prices(conn, kis, ["005930", "000660", "035420"], history_years=1)
    assert r == {"ok": 2, "failed": ["000660"], "stopped": False}
    assert sorted(c for (c,) in conn.execute("SELECT DISTINCT code FROM prices")) == ["005930", "035420"]


def test_update_prices_stops_when_server_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "MAX_CONSECUTIVE_FAILS", 3)
    conn = db.connect(tmp_path / "m.sqlite")
    days = bdays(date(2026, 9, 25), 5)

    class DownAfterFirst(FakeKis):
        def get(self, url, headers, params, timeout):
            if params.get("FID_INPUT_ISCD") != "A":
                raise requests.exceptions.ReadTimeout("Read timed out.")
            return super().get(url, headers, params, timeout)
    fake = DownAfterFirst(days)
    codes = ["A", "B", "C", "D", "E", "F"]
    r = market.update_prices(conn, slow_client(tmp_path, fake, []), codes, history_years=1)
    assert r["stopped"] and r["ok"] == 1 and r["failed"] == ["B", "C", "D", "E", "F"]
    assert [c for (c,) in conn.execute("SELECT DISTINCT code FROM prices")] == ["A"]   # 받은 건 저장됨
