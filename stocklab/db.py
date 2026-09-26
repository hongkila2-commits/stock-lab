"""SQLite 저장소.

SQLite 를 쓰는 이유: 설치가 필요 없고, 대시보드가 읽는 동안 수집 스크립트가
동시에 써도(WAL 모드) 잠김 오류가 나지 않는다. 데이터 규모(수백 종목 × 수년)에 충분하다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY, name TEXT, source TEXT
);
-- 일봉 (수정주가)
CREATE TABLE IF NOT EXISTS prices (
    code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
    volume REAL, value REAL,
    PRIMARY KEY (code, date)
);
-- 투자자별 순매수. qty = 주, amt = 백만원
CREATE TABLE IF NOT EXISTS flows (
    code TEXT, date TEXT,
    frgn_qty REAL, orgn_qty REAL, prsn_qty REAL,
    frgn_amt REAL, orgn_amt REAL, prsn_amt REAL,
    PRIMARY KEY (code, date)
);
-- 거시지표. region: KR = 당일 장중 정보 / GLOBAL = 해외(한국 장 기준 하루 늦게 반영)
CREATE TABLE IF NOT EXISTS macro (
    series TEXT, date TEXT, value REAL, region TEXT,
    PRIMARY KEY (series, date)
);
-- eff_date: 이 뉴스를 '알 수 있게 된' 거래 기준일 (장 마감 15:30 이후 기사는 다음 날)
CREATE TABLE IF NOT EXISTS news (
    code TEXT, link TEXT, pub_ts TEXT, eff_date TEXT, title TEXT, sentiment REAL,
    PRIMARY KEY (code, link)
);
CREATE TABLE IF NOT EXISTS disclosures (
    code TEXT, rcept_no TEXT, date TEXT, eff_date TEXT, title TEXT, kind TEXT, sign INTEGER,
    PRIMARY KEY (code, rcept_no)
);
CREATE TABLE IF NOT EXISTS predictions (
    asof TEXT, code TEXT, horizon INTEGER, prob REAL, model_id TEXT,
    PRIMARY KEY (asof, code, horizon)
);
CREATE TABLE IF NOT EXISTS screener (
    asof TEXT, code TEXT, frgn_amt_5 REAL, frgn_amt_20 REAL, frgn_ratio_5 REAL,
    streak INTEGER, orgn_amt_5 REAL, score REAL, rank INTEGER,
    PRIMARY KEY (asof, code)
);
-- KOSPI·KOSDAQ 전체 종목 스냅샷 (update 때마다 통째로 교체). value 백만원, market_cap 억원
CREATE TABLE IF NOT EXISTS listing (
    code TEXT PRIMARY KEY, name TEXT, market TEXT, close REAL, change_pct REAL,
    volume REAL, value REAL, market_cap REAL, asof TEXT, source TEXT
);
-- 대시보드에서 추가한 관심종목 (settings.yaml 의 watchlist 와 합쳐서 사용)
CREATE TABLE IF NOT EXISTS user_watchlist (code TEXT PRIMARY KEY, added_at TEXT);
-- 날짜별 추천 종목 (누적 → 실제 성적 추적)
CREATE TABLE IF NOT EXISTS picks (
    asof TEXT, code TEXT, rank INTEGER, prob REAL,
    PRIMARY KEY (asof, code)
);
-- 종목별 예측 근거. pct = 같은 날 전 종목 중 백분위(0~1), contrib = 확률을 올린(+)/내린(-) 정도
CREATE TABLE IF NOT EXISTS explain (
    asof TEXT, code TEXT, feature TEXT, value REAL, pct REAL, contrib REAL,
    PRIMARY KEY (asof, code, feature)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")
    rows = df.astype(object).where(pd.notna(df), None).itertuples(index=False, name=None)
    conn.executemany(sql, rows)
    return len(df)


def query(conn: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def last_date(conn: sqlite3.Connection, table: str, code: str) -> str | None:
    row = conn.execute(f"SELECT MAX(date) FROM {table} WHERE code = ?", (code,)).fetchone()
    return row[0] if row else None


def user_watchlist(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT code FROM user_watchlist ORDER BY added_at")]
