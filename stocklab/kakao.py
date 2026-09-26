"""카카오톡 '나에게 보내기' 알림.

처음 한 번: kakao_setup.bat (= python -m stocklab kakao-login)
  브라우저로 카카오 로그인 → http://localhost:8765/callback 으로 돌아온 인가코드를 토큰으로 교환
  → data/kakao_token.json 에 저장.
이후: access_token 이 만료되면 refresh_token 으로 자동 갱신 (refresh_token 은 약 2개월,
      갱신할 때 새로 발급되면 교체되므로 매일 쓰면 계속 유지된다).
알림 실패는 로그만 남기고 호출한 쪽(update·실시간)을 멈추지 않는다.
"""
from __future__ import annotations

import json
import logging
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import DATA_DIR, env

log = logging.getLogger(__name__)

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
REDIRECT_URI = "http://localhost:8765/callback"
DASHBOARD_URL = "http://localhost:8501"
TEXT_LIMIT = 200          # 카카오 텍스트 템플릿 최대 글자 수


def split_messages(text: str, limit: int = TEXT_LIMIT, max_parts: int = 4) -> list[str]:
    """줄 단위로 limit 글자 이하 조각으로 나눈다. 너무 긴 한 줄은 잘라서 '…'."""
    parts, cur = [], ""
    for line in text.strip().splitlines():
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) <= limit:
            cur = cand
        else:
            parts.append(cur)
            cur = line
    if cur:
        parts.append(cur)
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        tail = "\n…(대시보드에서 더 보기)"
        parts[-1] = parts[-1][: limit - len(tail)] + tail
    return parts


class KakaoError(RuntimeError):
    pass


class Kakao:
    def __init__(self, rest_key: str | None = None, secret: str | None = None,
                 token_path: Path | None = None, session=requests, now=datetime.now):
        self.key = rest_key if rest_key is not None else env("KAKAO_REST_API_KEY")
        self.secret = secret if secret is not None else env("KAKAO_CLIENT_SECRET")
        self.token_path = token_path or DATA_DIR / "kakao_token.json"
        self.http, self.now = session, now

    # ── 토큰 ───────────────────────────────────────────
    def _save(self, data: dict, old: dict | None = None) -> dict:
        now = self.now()
        tok = dict(old or {})
        tok["access_token"] = data["access_token"]
        tok["expires_at"] = (now + timedelta(seconds=int(data.get("expires_in", 21599)))).isoformat()
        if data.get("refresh_token"):
            tok["refresh_token"] = data["refresh_token"]
            tok["refresh_expires_at"] = (now + timedelta(
                seconds=int(data.get("refresh_token_expires_in", 5_184_000)))).isoformat()
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(tok, indent=1), encoding="utf-8")
        return tok

    def _load(self) -> dict | None:
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _post_token(self, data: dict) -> dict:
        data = {"client_id": self.key, **data}
        if self.secret:
            data["client_secret"] = self.secret
        r = self.http.post(TOKEN_URL, data=data, timeout=10)
        body = r.json()
        if "access_token" not in body:
            raise KakaoError(f"토큰 발급 실패: {body.get('error_description') or body}")
        return body

    def exchange_code(self, code: str) -> dict:
        return self._save(self._post_token({"grant_type": "authorization_code",
                                            "redirect_uri": REDIRECT_URI, "code": code}))

    def access_token(self) -> str:
        tok = self._load()
        if not tok or "refresh_token" not in tok:
            raise KakaoError("카카오 로그인이 필요합니다. kakao_setup.bat 을 실행하세요.")
        if datetime.fromisoformat(tok["expires_at"]) - self.now() > timedelta(minutes=5):
            return tok["access_token"]
        if datetime.fromisoformat(tok["refresh_expires_at"]) <= self.now():
            raise KakaoError("카카오 로그인이 만료되었습니다(약 2개월). kakao_setup.bat 을 다시 실행하세요.")
        tok = self._save(self._post_token({"grant_type": "refresh_token",
                                           "refresh_token": tok["refresh_token"]}), tok)
        return tok["access_token"]

    def status(self) -> str:
        tok = self._load()
        if not self.key:
            return "미설정 (.env 에 KAKAO_REST_API_KEY 없음)"
        if not tok:
            return "로그인 필요 (kakao_setup.bat)"
        return f"로그인됨 · 재로그인 필요 시점 {tok.get('refresh_expires_at', '?')[:10]}"

    # ── 보내기 ─────────────────────────────────────────
    def _send_one(self, text: str, link: str) -> None:
        template = {"object_type": "text", "text": text,
                    "link": {"web_url": link, "mobile_web_url": link}, "button_title": "대시보드 열기"}
        for attempt in range(2):
            r = self.http.post(SEND_URL, timeout=10,
                               headers={"Authorization": f"Bearer {self.access_token()}"},
                               data={"template_object": json.dumps(template, ensure_ascii=False)})
            if r.status_code == 401 and attempt == 0:      # 토큰이 서버에서 먼저 만료된 경우
                tok = self._load() or {}
                tok["expires_at"] = self.now().isoformat()
                self.token_path.write_text(json.dumps(tok), encoding="utf-8")
                continue
            body = r.json()
            if body.get("result_code") == 0:
                return
            raise KakaoError(f"보내기 실패: {body.get('msg') or body}")

    def send(self, text: str, link: str | None = None) -> int:
        if link is None:                       # 휴대폰에서 누르면 열리도록 Tailscale 주소 우선
            from .netinfo import dashboard_url
            link = dashboard_url()
        parts = split_messages(text)
        for p in parts:
            self._send_one(p, link)
        return len(parts)


def notify(text: str, enabled: bool = True) -> bool:
    """알림 보내기. 설정이 꺼져 있거나 실패하면 False (예외를 던지지 않음)."""
    if not enabled or not env("KAKAO_REST_API_KEY"):
        return False
    try:
        n = Kakao().send(text)
        log.info("  카카오톡 알림 %d건 전송", n)
        return True
    except Exception as e:
        log.warning("  카카오톡 알림 실패: %s", e)
        return False


# ── 처음 로그인 ─────────────────────────────────────────
def login(timeout: int = 180) -> str:
    k = Kakao()
    if not k.key:
        raise KakaoError(".env 에 KAKAO_REST_API_KEY 를 먼저 입력하세요.")
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            got["code"] = (q.get("code") or [None])[0]
            got["error"] = (q.get("error_description") or q.get("error") or [None])[0]
            msg = "로그인 완료! 이 창을 닫아도 됩니다." if got["code"] else f"실패: {got['error']}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2>StockLab 카카오 {msg}</h2>".encode("utf-8"))

        def log_message(self, *a):
            pass

    server = HTTPServer(("localhost", 8765), Handler)
    server.timeout = timeout
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    url = AUTH_URL + "?" + urlencode({"client_id": k.key, "redirect_uri": REDIRECT_URI,
                                      "response_type": "code", "scope": "talk_message"})
    print("브라우저에서 카카오 로그인 후 '동의하고 계속하기'를 누르세요.\n(창이 안 열리면 이 주소를 직접 여세요)\n" + url)
    webbrowser.open(url)
    t.join(timeout + 5)
    server.server_close()
    if not got.get("code"):
        raise KakaoError(f"인가코드를 받지 못했습니다: {got.get('error') or '시간 초과'}")
    k.exchange_code(got["code"])
    return "카카오 로그인 완료. 토큰을 data/kakao_token.json 에 저장했습니다."
