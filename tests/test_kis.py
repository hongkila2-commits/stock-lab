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
