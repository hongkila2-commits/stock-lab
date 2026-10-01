"""주가·투자자 수급 수집 (한국투자증권).

종목마다 받자마자 저장하므로 중간에 멈춰도 받은 종목은 남고, 다시 실행하면 이어서 받는다.
한 종목 실패는 건너뛰고, 연속으로 많이 실패하면 서버 문제로 보고 이 단계를 멈춘다.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .. import db
from ..kis import KisClient

log = logging.getLogger(__name__)
MAX_CONSECUTIVE_FAILS = 15


def _each(codes: list[str], fetch, what: str) -> dict:
    """codes 마다 fetch(code) 실행. 결과 {'ok': n, 'failed': [codes], 'stopped': bool}."""
    ok, failed, streak = 0, [], 0
    for i, code in enumerate(codes, 1):
        try:
            fetch(i, code)
            ok += 1
            streak = 0
        except Exception as e:          # KisError(NETWORK 포함) 등 — 한 종목 때문에 전체를 멈추지 않음
            failed.append(code)
            streak += 1
            log.warning("  [%d/%d] %s %s 실패: %s", i, len(codes), code, what, e)
            if streak >= MAX_CONSECUTIVE_FAILS:
                log.error("  %d종목 연속 실패 — 한국투자증권 서버 문제로 보고 %s 수집을 멈춥니다. "
                          "이미 받은 %d종목은 저장됐고, 잠시 후 update.bat 을 다시 실행하면 이어서 받습니다.",
                          streak, what, ok)
                return {"ok": ok, "failed": failed + codes[i:], "stopped": True}
    return {"ok": ok, "failed": failed, "stopped": False}


def update_prices(conn, kis: KisClient, codes: list[str], history_years: int = 5) -> dict:
    def fetch(i, code):
        last = db.last_date(conn, "prices", code)
        # 마지막 날짜부터 다시 받는다 → 장중에 받은 미완성 일봉을 확정값으로 덮어씀
        start = (date.fromisoformat(last) if last
                 else date.today() - timedelta(days=365 * history_years))
        n = db.upsert(conn, "prices", kis.daily_prices(code, start))
        conn.commit()
        log.info("  [%d/%d] %s 일봉 %d건", i, len(codes), code, n)

    r = _each(codes, fetch, "일봉")
    log.info("  일봉: 성공 %d · 실패 %d", r["ok"], len(r["failed"]))
    return r


def update_flows(conn, kis: KisClient, codes: list[str]) -> dict:
    def fetch(i, code):
        db.upsert(conn, "flows", kis.investor_flow(code))
        if i % 10 == 0 or i == len(codes):
            conn.commit()
            log.info("  수급 %d/%d 종목 완료", i, len(codes))

    r = _each(codes, fetch, "수급")
    conn.commit()
    log.info("  수급: 성공 %d · 실패 %d", r["ok"], len(r["failed"]))
    return r
