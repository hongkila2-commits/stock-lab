"""뉴스·공시 수집: 키 오류는 첫 번에 멈추고 사유를 남김 · 한 종목 실패는 건너뜀 · .env 다시 읽기."""
import io
import os
import zipfile

import pytest

from stocklab import cli, config, db
from stocklab.collectors import dart, news

ITEM = {"title": "<b>삼성전자</b> 실적 개선 기대", "link": "https://n.news.naver.com/1",
        "originallink": "https://news.example.com/1", "pubDate": "Wed, 01 Oct 2026 10:00:00 +0900"}


class Resp:
    def __init__(self, status=200, data=None, content=b""):
        self.status_code, self._d, self.content = status, data, content
        self.text = str(data)

    def json(self):
        if self._d is None:
            raise ValueError("no json")
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeNaver:
    """query 별로 상태코드를 정해 돌려주는 가짜 네이버."""

    def __init__(self, status_for=None):
        self.status_for, self.calls = status_for or {}, []

    def get(self, url, timeout, params, headers):
        self.calls.append(params["query"])
        st = self.status_for.get(params["query"].strip('"'), 200)
        if st == "timeout":
            raise TimeoutError("read timeout")
        if st != 200:
            return Resp(st, {"errorMessage": "Authentication failed", "errorCode": "024"})
        return Resp(200, {"items": [ITEM, {**ITEM, "link": "https://n.news.naver.com/2",
                                            "originallink": "https://news.example.com/2"}]})


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DART_API_KEY", "k" * 40)
    c = db.connect(tmp_path / "n.sqlite")
    yield c
    c.close()


STOCKS = {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"}


def collect(conn, fake, stocks=STOCKS):
    return news.update_news(conn, stocks, 100, "lexicon", session=fake, sleep=lambda s: None)


def test_news_saved_for_every_stock_with_progress(conn, caplog):
    caplog.set_level("INFO")
    assert collect(conn, FakeNaver()) == 6
    assert conn.execute("SELECT COUNT(DISTINCT code) FROM news").fetchone()[0] == 3
    assert "[3/3] NAVER 뉴스 2건" in caplog.text
    s = news.status(conn)
    assert s["ok"] and "3종목 6건" in s["text"]


@pytest.mark.parametrize("code,hint", [(401, "Client ID"), (403, "'검색'"), (429, "한도")])
def test_auth_error_stops_at_first_call_with_reason(conn, caplog, code, hint):
    fake = FakeNaver({n: code for n in STOCKS.values()})
    assert collect(conn, fake) == 0
    assert set(fake.calls) == {'"삼성전자"'}               # 첫 종목에서 멈춤 — 300종목에 같은 오류를 반복하지 않음
    assert len(fake.calls) == (2 if code == 401 else 1)  # 401 은 다른 곳(API HUB↔개발자센터) 키인지 한 번 더 확인
    s = news.status(conn)
    assert not s["ok"] and hint in s["text"]
    assert hint in caplog.text


def test_one_stock_failure_is_skipped(conn):
    fake = FakeNaver({"SK하이닉스": "timeout"})
    assert collect(conn, fake) == 4 and len(fake.calls) == 3
    assert "1종목 실패" in news.status(conn)["text"]


def test_no_keys_skips(conn, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "")
    fake = FakeNaver()
    assert collect(conn, fake) == 0 and not fake.calls


# ── update 의 뉴스 대상 ──────────────────────────────────
def test_update_collects_news_for_all_targets_watchlist_first(tmp_path, monkeypatch):
    """예전에는 관심종목 + 외국인 상위 10 만 수집해서 시총·AI 추천 종목은 항상 '뉴스 없음' 이었다."""
    from stocklab import demo
    monkeypatch.setenv("STOCKLAB_DB", str(tmp_path / "d.sqlite"))
    with db.session() as c:
        demo.generate(c)
    seen = {}
    monkeypatch.setattr(news, "update_news", lambda conn, st, *a, **k: seen.setdefault("news", list(st)))
    monkeypatch.setattr(dart, "update_disclosures", lambda conn, st, *a, **k: seen.setdefault("dart", list(st)))
    monkeypatch.setattr(cli, "_train_and_predict", lambda *a, **k: None)
    from stocklab.collectors import listing, macro
    monkeypatch.setattr(listing, "update_listing", lambda conn: None)
    monkeypatch.setattr(macro, "update_macro", lambda conn, years: None)
    s = config.load_settings()
    with db.session() as c:
        everyone = cli.analysis_targets(c, s)
        mine = (set(s.watchlist) | set(db.user_watchlist(c))) & set(everyone)
    assert cli.main(["update", "--skip-market"]) == 0
    assert set(seen["news"]) == set(everyone) and seen["news"] == seen["dart"]
    assert len(everyone) > len(mine) + 10                # 관심·외국인 상위 밖의 종목도 포함
    assert set(seen["news"][:len(mine)]) == mine         # 관심종목(설정 + 대시보드 추가) 먼저


# ── DART ────────────────────────────────────────────────
class FakeDart:
    def __init__(self, list_status="000", corp_ok=True):
        self.list_status, self.corp_ok, self.calls = list_status, corp_ok, 0

    def get(self, url, params, timeout):
        if url.endswith("corpCode.xml"):
            if not self.corp_ok:
                return Resp(200, content="<result><status>010</status><message>등록되지 않은 키입니다.</message>"
                                         "</result>".encode())
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("CORPCODE.xml", "<result>" + "".join(
                    f"<list><corp_code>C{c}</corp_code><stock_code>{c}</stock_code></list>" for c in STOCKS)
                    + "</result>")
            return Resp(200, content=buf.getvalue())
        self.calls += 1
        if self.list_status != "000":
            return Resp(200, {"status": self.list_status, "message": "오류"})
        return Resp(200, {"status": "000", "total_page": 1, "list": [
            {"rcept_no": f"2026{self.calls:04d}", "rcept_dt": "20260930", "report_nm": "단일판매ㆍ공급계약체결"}]})


@pytest.fixture
def no_corp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(dart, "DATA_DIR", tmp_path)


def run_dart(conn, fake):
    return dart.update_disclosures(conn, STOCKS, 30, session=fake, sleep=lambda s: None)


def test_dart_saves_with_status(conn, no_corp_cache):
    assert run_dart(conn, FakeDart()) == 3
    assert db.get_meta(conn, "dart_status").startswith("ok|")


def test_dart_bad_key_reason_is_recorded(conn, no_corp_cache):
    """예전에는 키 오류 때 'File is not a zip file' 같은 알 수 없는 메시지만 남았다."""
    assert run_dart(conn, FakeDart(corp_ok=False)) == 0
    v = db.get_meta(conn, "dart_status")
    assert v.startswith("error|") and "등록되지 않은 DART 인증키" in v


def test_dart_limit_stops_at_first_call(conn, no_corp_cache):
    fake = FakeDart(list_status="020")
    assert run_dart(conn, fake) == 0 and fake.calls == 1
    assert "한도" in db.get_meta(conn, "dart_status")


# ── .env 다시 읽기 ──────────────────────────────────────
def test_reload_env_updates_only_env_file_keys(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_bytes("﻿NAVER_CLIENT_ID=fresh\nNAVER_CLIENT_SECRET=s2\n".encode("utf-8"))   # 메모장 BOM
    monkeypatch.setenv("NAVER_CLIENT_ID", "")
    monkeypatch.setenv("STOCKLAB_DB", "keep.sqlite")
    config.reload_env(f)
    assert os.environ["NAVER_CLIENT_ID"] == "fresh" and os.environ["STOCKLAB_DB"] == "keep.sqlite"
    assert news.has_keys()


def test_reload_env_clears_key_removed_from_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("NAVER_CLIENT_ID=abc\n", encoding="utf-8")
    monkeypatch.setenv("NAVER_CLIENT_ID", "")
    config.reload_env(f)
    assert os.environ["NAVER_CLIENT_ID"] == "abc"
    f.write_text("# 키를 지움\n", encoding="utf-8")
    config.reload_env(f)
    assert os.environ["NAVER_CLIENT_ID"] == ""


def test_network_errors_do_not_leak_dart_key(conn, no_corp_cache):
    """requests 오류 메시지에는 주소가 통째로 들어간다 → DART 키(crtfc_key)가 로그·화면에 찍히면 안 된다."""
    import requests

    class Down:
        def get(self, url, params=None, timeout=None, headers=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?crtfc_key={params.get('crtfc_key')}")
    with pytest.raises(dart.DartError) as e:
        dart.fetch_list("SECRETKEY123", "00126380", __import__("datetime").date(2026, 9, 1), Down())
    assert "SECRETKEY123" not in str(e.value) and "연결하지 못했습니다" in str(e.value)
    assert run_dart(conn, Down()) == 0
    assert "k" * 40 not in db.get_meta(conn, "dart_status")


# ── NAVER API HUB / 개발자센터 두 방식 ───────────────────────────────────────
class TwoPlatforms:
    """주소·헤더를 보고 응답하는 가짜 네이버. accepts = 키가 발급된 곳 ('hub' / 'developers' / None)."""
    HEADER = {"hub": "X-NCP-APIGW-API-KEY-ID", "developers": "X-Naver-Client-Id"}

    def __init__(self, accepts):
        self.accepts, self.calls = accepts, []

    def get(self, url, timeout, params, headers):
        plat = "hub" if url == "https://naverapihub.apigw.ntruss.com/search/v1/news" else \
            "developers" if url == "https://openapi.naver.com/v1/search/news.json" else "?"
        self.calls.append(plat)
        if plat == self.accepts and headers.get(self.HEADER[plat]) == "id":
            return Resp(200, {"lastBuildDate": "x", "total": 2, "start": 1, "display": 2, "items": [ITEM]})
        if plat == "hub":
            return Resp(401, {"error": {"errorCode": "200", "message": "Authentication Failed",
                                        "details": "Invalid authentication information."}})
        return Resp(401, {"errorMessage": "NID AUTH Result Invalid (1000) : Authentication failed. (인증에 실패했습니다.)",
                          "errorCode": "024"})


@pytest.fixture(autouse=True)
def fresh_platform(monkeypatch):
    monkeypatch.setattr(news, "_platform", None, raising=False)


def test_api_hub_key_works(conn):
    """사용자 PC 에서 난 401 'NID AUTH Result Invalid': API HUB 키를 개발자센터 방식으로 보냈기 때문."""
    fake = TwoPlatforms("hub")
    assert collect(conn, fake) == 3
    assert fake.calls == ["hub"] * 3
    assert "API HUB" in news.status(conn)["text"]


def test_old_developers_key_still_works_and_is_remembered(conn):
    fake = TwoPlatforms("developers")
    assert collect(conn, fake) == 3
    assert fake.calls == ["hub", "developers", "developers", "developers"]   # 한 번 확인 뒤엔 되는 쪽만
    assert "개발자센터" in news.status(conn)["text"]


def test_wrong_key_on_both_stops_with_reason(conn):
    fake = TwoPlatforms(None)
    assert collect(conn, fake) == 0
    assert fake.calls == ["hub", "developers"]
    s = news.status(conn)
    assert not s["ok"] and "API HUB" in s["text"] and "Client ID" in s["text"]


def test_bad_item_is_skipped(conn):
    class Odd(TwoPlatforms):
        def get(self, *a, **k):
            r = super().get(*a, **k)
            if r.status_code == 200:
                r._d["items"] = [ITEM, {"title": "날짜 없는 기사", "link": "x"}, {**ITEM, "pubDate": "어제"}]
            return r
    assert collect(conn, Odd("hub"), {"005930": "삼성전자"}) == 1
