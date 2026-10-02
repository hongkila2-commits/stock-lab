"""DART 전자공시 수집 + 공시 유형 분류."""
from __future__ import annotations

import io
import logging
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from .. import db
from ..config import DATA_DIR, env

log = logging.getLogger(__name__)
API = "https://opendart.fss.or.kr/api"
OK, NO_DATA = "000", "013"
# 모든 종목이 같은 이유로 실패하는 상태 → 첫 번에 멈추고 사유를 남긴다
FATAL = {
    "010": "등록되지 않은 DART 인증키입니다. .env 의 DART_API_KEY 를 OpenDART → 인증키 신청/관리 → 오픈API 이용현황의 "
           "40자리 키와 비교하세요 (앞뒤 공백 없이).",
    "011": "사용할 수 없는 DART 인증키입니다 (일시 중지·탈퇴). OpenDART 에서 키 상태를 확인하세요.",
    "012": "이 PC 의 IP 에서 DART 를 쓸 수 없습니다. OpenDART 키 설정의 IP 제한을 확인하세요.",
    "020": "DART 하루 호출 한도(20,000회)를 넘었습니다. 내일 다시 수집됩니다.",
    "901": "DART 인증키가 만료되었습니다 (개인정보 보유기간 경과). OpenDART 에서 키를 다시 신청하세요.",
    "800": "DART 시스템 점검 중입니다. 다음 update 때 다시 수집됩니다.",
}


def _get(session, url: str, **kw):
    """요청 오류 메시지에는 주소(=인증키 crtfc_key 포함)가 들어가므로 주소 없이 다시 올린다."""
    try:
        return session.get(url, **kw)
    except requests.RequestException as e:
        raise DartError("NETWORK", f"DART 서버에 연결하지 못했습니다 ({type(e).__name__})") from None


class DartError(RuntimeError):
    def __init__(self, status: str, message: str = ""):
        self.status = status
        self.fatal = status in FATAL
        super().__init__(f"{FATAL.get(status, message or 'DART 응답 오류')} (status {status})")

# (공시 제목에 포함된 말, 분류, 방향)  위에서부터 먼저 맞는 것 적용
RULES = [
    ("단일판매ㆍ공급계약", "수주", 1), ("공급계약", "수주", 1),
    ("자기주식취득", "자사주매입", 1), ("자기주식소각", "자사주소각", 1), ("주식소각", "자사주소각", 1),
    ("현금ㆍ현물배당", "배당", 1),
    ("무상증자", "무상증자", 1),
    ("유상증자", "유상증자", -1), ("전환사채", "메자닌", -1), ("신주인수권부사채", "메자닌", -1),
    ("교환사채", "메자닌", -1),
    ("자기주식처분", "자사주처분", -1),
    ("감자", "감자", -1), ("횡령", "횡령배임", -1), ("배임", "횡령배임", -1),
    ("소송", "소송", -1), ("불성실공시", "제재", -1), ("관리종목", "제재", -1),
    ("영업(잠정)실적", "실적", 0), ("매출액또는손익구조", "실적", 0),
    ("최대주주변경", "지배구조", 0), ("임원ㆍ주요주주특정증권등소유상황", "지분변동", 0),
    ("주식등의대량보유", "지분변동", 0),
]


def classify(title: str) -> tuple[str, int]:
    t = title.replace(" ", "")
    for key, kind, sign in RULES:
        if key.replace(" ", "") in t:
            return kind, sign
    return "기타", 0


def corp_codes(api_key: str, session=requests) -> dict[str, str]:
    """종목코드 → DART 고유번호. 한 달에 한 번만 새로 받는다."""
    cache = DATA_DIR / "dart_corpcode.csv"
    if cache.exists() and (date.today() - date.fromtimestamp(cache.stat().st_mtime)).days < 30:
        df = pd.read_csv(cache, dtype=str)
    else:
        r = _get(session, f"{API}/corpCode.xml", params={"crtfc_key": api_key}, timeout=60)
        if r.status_code != 200:
            raise DartError(str(r.status_code), "DART 고유번호 목록을 받지 못했습니다")
        if not zipfile.is_zipfile(io.BytesIO(r.content)):     # 키 오류면 zip 대신 오류 XML/JSON 이 온다
            try:
                err = ET.fromstring(r.content)
                raise DartError(err.findtext("status") or "?", err.findtext("message") or "")
            except ET.ParseError:
                d = r.json()
                raise DartError(str(d.get("status", "?")), d.get("message", ""))
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
        df = pd.DataFrame([{"stock_code": (e.findtext("stock_code") or "").strip(),
                            "corp_code": e.findtext("corp_code")} for e in root.iter("list")])
        df = df[df["stock_code"] != ""]
        DATA_DIR.mkdir(exist_ok=True)
        df.to_csv(cache, index=False)
    return dict(zip(df["stock_code"], df["corp_code"]))


def _status(conn, ok: bool, text: str) -> None:
    db.set_meta(conn, "dart_status", f"{'ok' if ok else 'error'}|{datetime.now():%Y-%m-%d %H:%M}|{text}")
    conn.commit()


def fetch_list(key: str, corp: str, start: date, session=requests) -> list[dict]:
    rows, page = [], 1
    while True:
        data = _get(session, f"{API}/list.json", timeout=20, params={
            "crtfc_key": key, "corp_code": corp, "bgn_de": f"{start:%Y%m%d}",
            "end_de": f"{date.today():%Y%m%d}", "page_no": page, "page_count": 100,
        }).json()
        st = str(data.get("status", ""))
        if st == NO_DATA:
            return rows
        if st != OK:
            raise DartError(st, data.get("message", ""))
        rows += data.get("list", [])
        if page >= int(data.get("total_page", 1)):
            return rows
        page += 1


def update_disclosures(conn, stocks: dict[str, str], lookback_days: int = 365,
                       session=requests, sleep=time.sleep) -> int:
    key = env("DART_API_KEY")
    if not key:
        log.info("  DART 키가 없어 공시 수집을 건너뜁니다.")
        return 0
    try:
        mapping = corp_codes(key, session)
    except Exception as e:
        log.error("  DART 고유번호 목록 실패 — 공시 수집을 건너뜁니다: %s", e)
        _status(conn, False, str(e))
        return 0
    total, ok, failed = 0, 0, 0
    for i, (code, name) in enumerate(stocks.items(), 1):
        corp = mapping.get(code)
        if not corp:
            continue
        if i > 1:
            sleep(0.05)
        last = db.last_date(conn, "disclosures", code)
        start = (date.fromisoformat(last) - timedelta(days=3) if last
                 else date.today() - timedelta(days=lookback_days))
        try:
            rows = fetch_list(key, corp, start, session)
        except DartError as e:
            if e.fatal:
                log.error("  공시 수집 중단: %s", e)
                _status(conn, False, str(e))
                return total
            failed += 1
            log.warning("  [%d/%d] %s 공시 실패: %s", i, len(stocks), name, e)
            continue
        except Exception as e:
            failed += 1
            log.warning("  [%d/%d] %s 공시 실패: %s", i, len(stocks), name, e)
            continue
        ok += 1
        if not rows:
            continue
        df = pd.DataFrame(rows)
        kinds = df["report_nm"].map(classify)
        d = pd.to_datetime(df["rcept_dt"], format="%Y%m%d")
        out = pd.DataFrame({
            "code": code, "rcept_no": df["rcept_no"], "date": d.dt.strftime("%Y-%m-%d"),
            # 공시 시각을 알 수 없으므로 보수적으로 다음 날부터 반영
            "eff_date": (d + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d"),
            "title": df["report_nm"].str.strip(),
            "kind": [k for k, _ in kinds], "sign": [s for _, s in kinds],
        })
        total += db.upsert(conn, "disclosures", out)
        log.info("  [%d/%d] %s 공시 %d건", i, len(stocks), name, len(out))
    conn.commit()
    _status(conn, ok > 0 or failed == 0, f"{ok}종목 {total}건" + (f" · {failed}종목 실패" if failed else ""))
    log.info("  공시: %d종목 %d건", ok, total)
    return total
