"""카카오톡 알림: 200자 분할 · 토큰 갱신 · 실패 무시 (가짜 서버)."""
import json
from datetime import datetime, timedelta

import pytest

from stocklab import kakao


def test_split_messages_respects_limit():
    lines = [f"줄{i} " + "가" * 60 for i in range(20)]   # 3줄씩 → 7건 분량 → 4건으로 자름
    parts = kakao.split_messages("\n".join(lines))
    assert len(parts) == 4 and all(len(p) <= 200 for p in parts)
    assert parts[-1].endswith("(대시보드에서 더 보기)")
    assert kakao.split_messages("짧은 메시지") == ["짧은 메시지"]
    assert len(kakao.split_messages("가" * 500)[0]) == 200


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeKakao:
    def __init__(self):
        self.posts = []
        self.expire_first_send = False

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, data, headers))
        if url == kakao.TOKEN_URL:
            if data["grant_type"] == "authorization_code":
                return Resp({"access_token": "A1", "refresh_token": "R1", "expires_in": 21599,
                             "refresh_token_expires_in": 5183999})
            return Resp({"access_token": "A2", "expires_in": 21599})       # 갱신: refresh 유지
        if self.expire_first_send:
            self.expire_first_send = False
            return Resp({"msg": "this access token is already expired", "code": -401}, 401)
        return Resp({"result_code": 0})


def test_login_refresh_and_send(tmp_path):
    t0 = datetime(2026, 9, 25, 18, 0)
    clock = {"now": t0}
    fake = FakeKakao()
    k = kakao.Kakao("REST", "", tmp_path / "tok.json", session=fake, now=lambda: clock["now"])
    k.exchange_code("CODE")
    assert k.access_token() == "A1"

    clock["now"] = t0 + timedelta(hours=7)                 # access_token 만료 → 자동 갱신
    assert k.send("안녕") == 1
    tok = json.loads((tmp_path / "tok.json").read_text())
    assert tok["access_token"] == "A2" and tok["refresh_token"] == "R1"
    sent = [p for p in fake.posts if p[0] == kakao.SEND_URL][-1]
    assert sent[2]["Authorization"] == "Bearer A2"
    assert json.loads(sent[1]["template_object"])["text"] == "안녕"


def test_server_side_expiry_retries_once(tmp_path):
    fake = FakeKakao()
    k = kakao.Kakao("REST", "", tmp_path / "tok.json", session=fake)
    k.exchange_code("CODE")
    fake.expire_first_send = True
    assert k.send("x") == 1
    assert sum(1 for p in fake.posts if p[0] == kakao.SEND_URL) == 2


def test_not_logged_in_and_notify_never_raises(tmp_path, monkeypatch):
    k = kakao.Kakao("REST", "", tmp_path / "none.json", session=FakeKakao())
    with pytest.raises(kakao.KakaoError, match="kakao_setup.bat"):
        k.access_token()
    monkeypatch.setenv("KAKAO_REST_API_KEY", "REST")
    monkeypatch.setattr(kakao, "DATA_DIR", tmp_path)
    assert kakao.notify("x") is False                      # 로그인 안 됨 → False, 예외 없음
    assert kakao.notify("x", enabled=False) is False
