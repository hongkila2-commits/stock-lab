"""주가·투자자 수급 수집 (한국투자증권)."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .. import db
from ..kis import KisClient, KisError

log = logging.getLogger(__name__)


def update_prices(conn, kis: KisClient, codes: list[str], history_years: int = 5) -> int:
    total = 0
    for i, code in enumerate(codes, 1):
        last = db.last_date(conn, "prices", code)
        # 마지막 날짜부터 다시 받는다 → 장중에 받은 미완성 일봉을 확정값으로 덮어씀
        start = (date.fromisoformat(last) if last
                 else date.today() - timedelta(days=365 * history_years))
        try:
            df = kis.daily_prices(code, start)
        except KisError as e:
            log.warning("  %s 주가 수집 실패: %s", code, e)
            continue
        n = db.upsert(conn, "prices", df)
        conn.commit()
        total += n
        log.info("  [%d/%d] %s 일봉 %d건", i, len(codes), code, n)
    return total


def update_flows(conn, kis: KisClient, codes: list[str]) -> int:
    total = 0
    for i, code in enumerate(codes, 1):
        try:
            df = kis.investor_flow(code)
        except KisError as e:
            log.warning("  %s 수급 수집 실패: %s", code, e)
            continue
        total += db.upsert(conn, "flows", df)
        if i % 10 == 0 or i == len(codes):
            conn.commit()
            log.info("  수급 %d/%d 종목 완료", i, len(codes))
    return total
