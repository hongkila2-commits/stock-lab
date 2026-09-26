"""DART 전자공시 수집 + 공시 유형 분류."""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from .. import db
from ..config import DATA_DIR, env

log = logging.getLogger(__name__)
API = "https://opendart.fss.or.kr/api"

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


def corp_codes(api_key: str) -> dict[str, str]:
    """종목코드 → DART 고유번호. 한 달에 한 번만 새로 받는다."""
    cache = DATA_DIR / "dart_corpcode.csv"
    if cache.exists() and (date.today() - date.fromtimestamp(cache.stat().st_mtime)).days < 30:
        df = pd.read_csv(cache, dtype=str)
    else:
        r = requests.get(f"{API}/corpCode.xml", params={"crtfc_key": api_key}, timeout=60)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            root = ET.fromstring(z.read(z.namelist()[0]))
        df = pd.DataFrame([{"stock_code": (e.findtext("stock_code") or "").strip(),
                            "corp_code": e.findtext("corp_code")} for e in root.iter("list")])
        df = df[df["stock_code"] != ""]
        DATA_DIR.mkdir(exist_ok=True)
        df.to_csv(cache, index=False)
    return dict(zip(df["stock_code"], df["corp_code"]))


def update_disclosures(conn, stocks: dict[str, str], lookback_days: int = 365) -> int:
    key = env("DART_API_KEY")
    if not key:
        log.info("  DART 키가 없어 공시 수집을 건너뜁니다.")
        return 0
    try:
        mapping = corp_codes(key)
    except Exception as e:
        log.warning("  DART 고유번호 목록 실패: %s", e)
        return 0
    total = 0
    for code, name in stocks.items():
        corp = mapping.get(code)
        if not corp:
            continue
        last = db.last_date(conn, "disclosures", code)
        start = (date.fromisoformat(last) - timedelta(days=3) if last
                 else date.today() - timedelta(days=lookback_days))
        rows, page = [], 1
        while True:
            try:
                data = requests.get(f"{API}/list.json", timeout=20, params={
                    "crtfc_key": key, "corp_code": corp, "bgn_de": f"{start:%Y%m%d}",
                    "end_de": f"{date.today():%Y%m%d}", "page_no": page, "page_count": 100,
                }).json()
            except Exception as e:
                log.warning("  %s 공시 실패: %s", name, e)
                break
            if data.get("status") != "000":   # 013 = 조회 결과 없음
                break
            rows += data.get("list", [])
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
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
        log.info("  %s 공시 %d건", name, len(out))
    conn.commit()
    return total
