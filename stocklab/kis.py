"""한국투자증권 Open API 클라이언트 (시세 조회 전용 — 주문 기능 없음).

주의 사항
- 접근토큰은 하루 유효하고, 발급은 1분에 1회로 제한된다. 그래서 파일에 저장해 재사용한다.
- 모의투자 서버는 초당 호출 수가 적다. 호출 사이 간격을 자동으로 둔다.
- 일봉 API 는 한 번에 최대 100건만 주므로 기간을 거꾸로 나눠 반복 호출한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .config import DATA_DIR

log = logging.getLogger(__name__)

BASE_URL = {
    "paper": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}
# 호출 간 최소 간격(초). 모의투자는 초당 제한이 낮다.
MIN_INTERVAL = {"paper": 0.55, "real": 0.06}

RATE_LIMIT_CODES = {"EGW00201"}              # 초당 거래건수 초과
TOKEN_EXPIRED_CODES = {"EGW00123", "EGW00121"}  # 토큰 만료 / 유효하지 않은 토큰
TOKEN_TOO_OFTEN = "EGW00133"                 # 토큰 발급 1분당 1회 제한
TIMEOUT = (5, 30)               # (연결, 응답) 초 — 모의투자 서버는 응답이 10초 넘게 걸릴 때가 있다
NETWORK_WAITS = (2, 5, 10)      # 시간 초과·연결 끊김 시 이만큼 쉬고 다시 (최대 3회)


class KisError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


class KisClient:
    def __init__(self, app_key: str, app_secret: str, env: str = "paper",
                 token_path: Path | None = None, session: requests.Session | None = None,
                 min_interval: float | None = None, sleep=time.sleep):
        if env not in BASE_URL:
            raise ValueError("KIS_ENV 는 paper 또는 real 이어야 합니다.")
        if not app_key or not app_secret:
            raise ValueError(".env 에 KIS_APP_KEY / KIS_APP_SECRET 을 입력하세요.")
        self.app_key, self.app_secret, self.env = app_key, app_secret, env
        self.base = BASE_URL[env]
        self.http = session or requests.Session()
        self.token_path = token_path or DATA_DIR / f"kis_token_{env}.json"
        self.min_interval = MIN_INTERVAL[env] if min_interval is None else min_interval
        self._sleep = sleep
        self._last_call = 0.0
        self._token: str | None = None

    # ── 네트워크 ───────────────────────────────────────────
    def _request(self, method: str, url: str, **kw):
        """시간 초과·연결 끊김은 잠시 쉬고 다시 시도. 끝내 안 되면 KisError("NETWORK")."""
        for attempt in range(len(NETWORK_WAITS) + 1):
            try:
                return getattr(self.http, method)(url, timeout=TIMEOUT, **kw)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt == len(NETWORK_WAITS):
                    raise KisError("NETWORK", "한국투자증권 서버가 응답하지 않습니다 "
                                              f"({type(e).__name__}). 잠시 후 다시 실행하세요.") from e
                wait = NETWORK_WAITS[attempt]
                log.warning("  서버 응답 지연 — %d초 후 다시 시도 (%d/%d)", wait, attempt + 1, len(NETWORK_WAITS))
                self._sleep(wait)

    # ── 토큰 ───────────────────────────────────────────────
    def _key_id(self) -> str:
        return hashlib.sha256(self.app_key.encode()).hexdigest()[:12]

    def _load_cached_token(self) -> str | None:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("key_id") != self._key_id():
            return None
        expires = datetime.fromisoformat(data["expires_at"])
        if expires - datetime.now() < timedelta(minutes=10):
            return None
        return data["token"]

    def _issue_token(self) -> str:
        body = {"grant_type": "client_credentials",
                "appkey": self.app_key, "appsecret": self.app_secret}
        for attempt in range(2):
            r = self._request("post", f"{self.base}/oauth2/tokenP", json=body)
            data = r.json()
            if "access_token" in data:
                break
            code = data.get("error_code") or data.get("msg_cd") or str(r.status_code)
            if code == TOKEN_TOO_OFTEN and attempt == 0:
                log.warning("토큰 발급은 1분에 1회만 가능합니다. 61초 기다립니다.")
                self._sleep(61)
                continue
            raise KisError(code, data.get("error_description") or data.get("msg1") or r.text)
        expires_in = int(data.get("expires_in", 86400))
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps({
            "token": data["access_token"], "expires_at": expires_at.isoformat(),
            "key_id": self._key_id(),
        }), encoding="utf-8")
        return data["access_token"]

    def token(self, force: bool = False) -> str:
        if not force and self._token:
            return self._token
        self._token = (None if force else self._load_cached_token()) or self._issue_token()
        return self._token

    # ── 공통 GET ───────────────────────────────────────────
    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def get(self, path: str, tr_id: str, params: dict) -> dict:
        refreshed = False
        for attempt in range(5):
            self._throttle()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.token()}",
                "appkey": self.app_key, "appsecret": self.app_secret,
                "tr_id": tr_id, "custtype": "P",
            }
            r = self._request("get", f"{self.base}{path}", headers=headers, params=params)
            try:
                data = r.json()
            except ValueError:
                raise KisError(str(r.status_code), r.text[:200])
            if data.get("rt_cd") == "0":
                return data
            code = data.get("msg_cd", "")
            if code in RATE_LIMIT_CODES:
                self._sleep(1.0 + attempt)
                continue
            if code in TOKEN_EXPIRED_CODES and not refreshed:
                self.token(force=True)
                refreshed = True
                continue
            raise KisError(code, data.get("msg1", r.text[:200]))
        raise KisError("RETRY", f"{path} 호출이 반복 제한되었습니다.")

    # ── 시세 ───────────────────────────────────────────────
    def daily_prices(self, code: str, start: date, end: date | None = None) -> pd.DataFrame:
        """기간별 일봉(수정주가). 100건씩 끊어 end → start 방향으로 거슬러 올라간다."""
        end = end or date.today()
        frames, cursor = [], end
        while cursor >= start:
            data = self.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                 "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                 "FID_INPUT_DATE_2": cursor.strftime("%Y%m%d"),
                 "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
            )
            rows = [r for r in data.get("output2") or [] if r.get("stck_bsop_date")]
            if not rows:
                break
            frames.append(pd.DataFrame(rows))
            oldest = min(datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date() for r in rows)
            if oldest <= start or len(rows) < 100:
                break
            cursor = oldest - timedelta(days=1)
        if not frames:
            return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close",
                                         "volume", "value"])
        raw = pd.concat(frames).drop_duplicates("stck_bsop_date")
        df = pd.DataFrame({
            "code": code,
            "date": pd.to_datetime(raw["stck_bsop_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d"),
            "open": pd.to_numeric(raw["stck_oprc"], errors="coerce"),
            "high": pd.to_numeric(raw["stck_hgpr"], errors="coerce"),
            "low": pd.to_numeric(raw["stck_lwpr"], errors="coerce"),
            "close": pd.to_numeric(raw["stck_clpr"], errors="coerce"),
            "volume": pd.to_numeric(raw["acml_vol"], errors="coerce"),
            "value": pd.to_numeric(raw.get("acml_tr_pbmn"), errors="coerce"),
        })
        df = df[(df["date"] >= start.isoformat()) & (df["close"] > 0)]
        return df.sort_values("date").reset_index(drop=True)

    def investor_flow(self, code: str) -> pd.DataFrame:
        """종목별 투자자 순매수 (최근 약 30거래일). 매일 쌓아서 이력을 만든다."""
        data = self.get("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        rows = [r for r in data.get("output") or [] if r.get("stck_bsop_date")]
        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(rows)
        num = lambda c: pd.to_numeric(raw.get(c), errors="coerce")  # noqa: E731
        df = pd.DataFrame({
            "code": code,
            "date": pd.to_datetime(raw["stck_bsop_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d"),
            "frgn_qty": num("frgn_ntby_qty"), "orgn_qty": num("orgn_ntby_qty"),
            "prsn_qty": num("prsn_ntby_qty"),
            "frgn_amt": num("frgn_ntby_tr_pbmn"), "orgn_amt": num("orgn_ntby_tr_pbmn"),
            "prsn_amt": num("prsn_ntby_tr_pbmn"),
        })
        # 장중에는 당일 행이 빈 값으로 온다 → 확정되지 않은 행은 저장하지 않는다.
        df = df.dropna(subset=["frgn_qty", "orgn_qty"], how="all")
        return df.sort_values("date").reset_index(drop=True)

    def quote(self, code: str) -> dict:
        """현재가·시가총액·PER/PBR·52주 고저·외국인 소진율 (주식현재가 시세)."""
        o = self.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}).get("output") or {}
        f = lambda k: pd.to_numeric(o.get(k), errors="coerce")  # noqa: E731
        return {
            "price": f("stck_prpr"), "change_pct": f("prdy_ctrt"),
            "open": f("stck_oprc"), "high": f("stck_hgpr"), "low": f("stck_lwpr"),
            "volume": f("acml_vol"), "value": f("acml_tr_pbmn"),
            "market_cap": f("hts_avls"),                    # 억원
            "per": f("per"), "pbr": f("pbr"), "eps": f("eps"), "bps": f("bps"),
            "high_52w": f("w52_hgpr"), "low_52w": f("w52_lwpr"),
            "foreign_pct": f("hts_frgn_ehrt"),              # 외국인 소진율(%)
            "sector": (o.get("bstp_kor_isnm") or "").strip(),
        }

    def approval_key(self) -> str:
        """실시간(WebSocket) 접속키. REST 토큰과 별개이며 appsecret 대신 'secretkey' 로 보낸다."""
        r = self._request("post", f"{self.base}/oauth2/Approval", json={
            "grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret})
        data = r.json()
        if not data.get("approval_key"):
            raise KisError(str(data.get("error_code") or r.status_code),
                           str(data.get("error_description") or data))
        return data["approval_key"]
