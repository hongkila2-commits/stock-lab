"""알림 내용 만들기: 저녁 요약 · 장중 급등락."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import db


def _signal(p: float, s) -> str:
    return "강세" if p >= s.model["bullish"] else "약세" if p <= s.model["bearish"] else "중립"


def _names(conn) -> dict[str, str]:
    d = db.query(conn, "SELECT code, name FROM listing UNION SELECT code, name FROM stocks")
    return dict(zip(d["code"], d["name"]))


def my_codes(conn, s) -> list[str]:
    return list(dict.fromkeys(list(s.watchlist) + db.user_watchlist(conn)))


def daily_summary(conn, s) -> str | None:
    """update 끝에 보내는 요약. 예측이 없으면 None."""
    h = s.horizons[0]
    pred = db.query(conn, "SELECT asof, code, prob FROM predictions WHERE horizon = ?", (h,))
    if pred.empty:
        return None
    names = _names(conn)
    dates = sorted(pred["asof"].unique())
    latest, prev = dates[-1], (dates[-2] if len(dates) > 1 else None)
    lines = [f"[StockLab] {latest[5:].replace('-', '/')} 요약"]

    from .config import horizon_label
    for hz in s.horizons:                        # 기간별 추천 상위 5 (1주, 1개월 …)
        picks = db.query(conn, "SELECT code, prob FROM picks WHERE horizon = ? AND asof = "
                               "(SELECT MAX(asof) FROM picks WHERE horizon = ?) ORDER BY rank LIMIT 5",
                         (hz, hz))
        if len(picks):
            lines.append(f"AI {horizon_label(hz)}: " + ", ".join(
                f"{names.get(c, c)} {p:.2f}" for c, p in zip(picks["code"], picks["prob"])))

    mine = my_codes(conn, s)
    now = pred[pred["asof"] == latest].set_index("code")["prob"]
    before = pred[pred["asof"] == prev].set_index("code")["prob"] if prev else pd.Series(dtype=float)
    changes = []
    for c in mine:
        if c in now.index and c in before.index:
            a, b = _signal(before[c], s), _signal(now[c], s)
            if a != b:
                changes.append(f"{names.get(c, c)} {a}→{b}")
    lines.append("관심종목 신호: " + (", ".join(changes) if changes else "변화 없음"))

    from .sectors import sector_summary
    sec = sector_summary(conn, h)
    if len(sec):
        top = sec.head(3)
        lines.append("외국인 매수 업종: " + ", ".join(
            f"{r.sector} {r.frgn_5:+,.0f}억" for r in top.itertuples()))
    return "\n".join(lines)


def move_alert(conn, s, code: str, change_pct: float, price: float,
               now: datetime | None = None) -> str | None:
    """관심종목이 전일 대비 ±move_pct 이상이면 알림 문구 (같은 방향은 cooldown 동안 한 번)."""
    th = float(s.alerts["move_pct"])
    if change_pct is None or pd.isna(change_pct) or abs(change_pct) < th:
        return None
    now = now or datetime.now()
    kind = "move_up" if change_pct > 0 else "move_down"
    since = (now - timedelta(minutes=int(s.alerts["cooldown_min"]))).isoformat(timespec="seconds")
    recent = conn.execute("SELECT 1 FROM alerts_sent WHERE code = ? AND kind = ? AND ts >= ?",
                          (code, kind, since)).fetchone()
    if recent:
        return None
    conn.execute("INSERT OR IGNORE INTO alerts_sent VALUES (?, ?, ?)",
                 (code, kind, now.isoformat(timespec="seconds")))
    conn.commit()
    name = _names(conn).get(code, code)
    tag = "급등" if change_pct > 0 else "급락"
    return f"[{tag}] {name} {change_pct:+.2f}% ({price:,.0f}원) {now:%H:%M}"
