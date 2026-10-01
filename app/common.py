"""대시보드 공통: 데이터 읽기 · 종목 요약 · 표 · 페이지 이동."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stocklab import db  # noqa: E402
from stocklab.config import db_path, horizon_label, load_settings  # noqa: E402

UP, DOWN = "#d6336c", "#1c7ed6"   # 한국 관례: 상승 빨강, 하락 파랑
PAGES = ["관심종목", "종목 상세", "업종 수급", "외국인 수급", "예측 모델", "거시지표"]
RT_FRESH_SEC = 120        # 실시간 값이 이 시간 안이면 표의 현재가를 실시간 값으로
S = load_settings()
HORIZONS = S.horizons                 # 예: [5, 20]
H = HORIZONS[0]                       # 기본(1주)
TARGET_LABEL = "코스피 대비 강세 확률" if S.model["target"] == "excess" else "상승 확률"
PROB_COL = "강세확률"
PROB_TITLE = "강세 확률" if S.model["target"] == "excess" else "상승 확률"


# ── 데이터 ──────────────────────────────────────────────
@st.cache_data(ttl=300)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    if not db_path().exists():
        return pd.DataFrame()
    conn = db.connect()
    try:
        return db.query(conn, sql, params)
    finally:
        conn.close()


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    if not db_path().exists():
        return pd.DataFrame()
    conn = db.connect()
    try:
        return db.query(conn, sql, params)
    finally:
        conn.close()


def refresh() -> None:
    st.cache_data.clear()


@st.cache_data(ttl=300)
def prices() -> pd.DataFrame:
    p = q("SELECT * FROM prices")
    if not p.empty:
        p["date"] = pd.to_datetime(p["date"])
        p = p.sort_values(["code", "date"]).reset_index(drop=True)
    return p


@st.cache_data(ttl=300)
def flows() -> pd.DataFrame:
    f = q("SELECT * FROM flows")
    if not f.empty:
        f["date"] = pd.to_datetime(f["date"])
        f = f.sort_values(["code", "date"])
    return f


@st.cache_data(ttl=300)
def listing() -> pd.DataFrame:
    """전체 종목 목록 (index = code)."""
    li = q("SELECT * FROM listing")
    if li.empty:
        return pd.DataFrame(columns=["name", "market", "close", "change_pct", "volume", "value",
                                     "market_cap", "asof", "source"]).rename_axis("code")
    return li.set_index("code")


@st.cache_data(ttl=300)
def names() -> dict[str, str]:
    d = q("SELECT code, name FROM stocks")
    out = dict(zip(d["code"], d["name"])) if not d.empty else {}
    li = listing()
    out.update(dict(zip(li.index, li["name"])))
    out.update(S.all_stocks)
    return out


@st.cache_data(ttl=300)
def latest_pred(h: int = H) -> pd.Series:
    pred = q("SELECT asof, code, prob FROM predictions WHERE horizon = ?", (int(h),))
    if pred.empty:
        return pd.Series(dtype=float)
    return pred.sort_values("asof").groupby("code").tail(1).set_index("code")["prob"]


@st.cache_data(ttl=300)
def stats() -> pd.DataFrame:
    """종목별 요약: 종가·수익률·52주 위치·외국인/기관 5일 순매수(억원)."""
    p = prices()
    if p.empty:
        return pd.DataFrame()
    g = p.groupby("code")["close"]
    c = p["close"]
    p = p.assign(r1=c / g.shift(1) - 1, r5=c / g.shift(5) - 1, r20=c / g.shift(20) - 1,
                 hi=g.transform(lambda s: s.rolling(250, min_periods=20).max()),
                 lo=g.transform(lambda s: s.rolling(250, min_periods=20).min()))
    last = p.groupby("code").tail(1).set_index("code")
    out = last[["date", "close", "r1", "r5", "r20", "hi", "lo", "value"]].copy()
    out["pos52"] = (out["close"] - out["lo"]) / (out["hi"] - out["lo"])
    f = flows()
    if not f.empty:
        f5 = f.groupby("code").tail(5).groupby("code")[["frgn_amt", "orgn_amt"]].sum() / 100
        out = out.join(f5)
    return out


@st.cache_data(ttl=4)
def rt_quotes() -> pd.DataFrame:
    """장중 실시간 최신 체결 (realtime.bat 이 채움). index = code."""
    r = _read("SELECT * FROM rt_quotes")
    return r.set_index("code") if not r.empty else r


def rt_status() -> dict:
    m = _read("SELECT key, value FROM meta WHERE key LIKE 'rt_%'")
    return dict(zip(m["key"], m["value"])) if not m.empty else {}


def now_kst():
    from stocklab.realtime import now_kst as _n
    return _n()


def rt_fresh() -> pd.DataFrame:
    """RT_FRESH_SEC 이내에 받은 실시간 값만."""
    r = rt_quotes()
    if r.empty:
        return r
    age = (now_kst() - pd.to_datetime(r["ts"])).dt.total_seconds()
    return r[age <= RT_FRESH_SEC]


def rt_alive() -> bool:
    """realtime.bat 이 90초 안에 신호를 남겼으면 실행 중."""
    m = rt_status()
    hb = pd.to_datetime(m.get("rt_heartbeat"), errors="coerce")
    return m.get("rt_mode") in ("websocket", "poll") and pd.notna(hb) and \
        (now_kst() - hb).total_seconds() < 90


def price_basis(codes: list[str]) -> str:
    """표의 '현재가'가 언제 값인지: '● 실시간 10:31:05 3종목 · 나머지 09-25 종가'."""
    rt, st_ = rt_fresh(), stats()
    live = [c for c in codes if c in rt.index]
    parts = []
    if live:
        parts.append(f"● 실시간 {pd.to_datetime(rt.loc[live, 'ts']).max():%H:%M:%S} ({len(live)}종목)")
    rest = [c for c in codes if c not in live and c in st_.index]
    if rest:
        parts.append(("나머지 " if live else "") + f"{st_.loc[rest, 'date'].max():%m-%d} 종가")
    return "가격 기준: " + " · ".join(parts) if parts else ""


def goto(code: str) -> None:
    """fragment 안에서 종목 상세로 이동: 다음 전체 실행 맨 앞에서 처리된다."""
    st.session_state["goto"] = code
    st.rerun()


def my_watchlist() -> list[str]:
    """settings.yaml 관심종목 + 대시보드에서 추가한 종목 (데이터가 전혀 없는 종목은 제외)."""
    user = q("SELECT code FROM user_watchlist ORDER BY added_at")
    codes = list(S.watchlist) + [c for c in (user["code"] if not user.empty else []) if c not in S.watchlist]
    known = set(stats().index) | set(listing().index)
    return [c for c in codes if c in known]


def label(code: str) -> str:
    return f"{names().get(code, code)} ({code})"


def signal(p) -> str:
    if p is None or pd.isna(p):
        return "-"
    return "강세" if p >= S.model["bullish"] else "약세" if p <= S.model["bearish"] else "중립"


def eok(x) -> str:
    """억원 → '1,234억' / '12.3조'."""
    if x is None or pd.isna(x):
        return "-"
    return f"{x / 10000:,.1f}조" if abs(x) >= 10000 else f"{x:,.0f}억"


# ── 표 ─────────────────────────────────────────────────
def stock_table(codes: list[str], extra: dict[str, dict] | None = None, h: int = H) -> pd.DataFrame:
    """공통 종목 표. 시세 이력이 있으면 그 값(당일), 없으면 전체 목록 값."""
    st_, li, pr, rt = stats(), listing(), latest_pred(h), rt_fresh()
    rows = []
    for c in codes:
        has = c in st_.index
        r = st_.loc[c] if has else None
        l = li.loc[c] if c in li.index else None
        p = pr.get(c, np.nan)
        live = rt.loc[c] if c in rt.index else None       # 장중 실시간 값이 있으면 우선
        rows.append({
            "code": c, "종목": names().get(c, c) + (" ●" if live is not None else ""),
            "시장": l["market"] if l is not None else "",
            "현재가": live["price"] if live is not None else (
                r["close"] if has else (l["close"] if l is not None else np.nan)),
            "등락%": live["change_pct"] if live is not None else (
                r["r1"] * 100 if has else (l["change_pct"] if l is not None else np.nan)),
            "5일%": r["r5"] * 100 if has else np.nan,
            "20일%": r["r20"] * 100 if has else np.nan,
            "시가총액": l["market_cap"] if l is not None else np.nan,
            "외국인5일(억)": r.get("frgn_amt", np.nan) if has else np.nan,
            PROB_COL: p, "신호": signal(p),
            **((extra or {}).get(c, {})),
        })
    df = pd.DataFrame(rows)
    for c in ("현재가", "등락%", "5일%", "20일%", "시가총액", "외국인5일(억)", PROB_COL):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def metric(col, label: str, value: str, chg: float | None, help: str | None = None) -> None:
    """등락을 한국 관례 색(상승 빨강·하락 파랑)으로. 구버전 Streamlit 은 inverse 로 대체."""
    if chg is None or pd.isna(chg):
        col.metric(label, value, help=help)
        return
    try:
        col.metric(label, value, f"{chg:+.2f}%", delta_color="red" if chg > 0 else "blue" if chg < 0 else "off",
                   help=help)
    except Exception:
        col.metric(label, value, f"{chg:+.2f}%", delta_color="inverse", help=help)


NUMFMT = {"현재가": "{:,.0f}", "등락%": "{:+.2f}", "5일%": "{:+.2f}", "20일%": "{:+.2f}",
          "외국인5일(억)": "{:+,.1f}", "기관5일(억)": "{:+,.1f}", "외국인20일(억)": "{:+,.1f}",
          "거래량대비%": "{:+.1f}", "연속일": "{:+d}"}
SIGNED = ["등락%", "5일%", "20일%", "외국인5일(억)", "기관5일(억)", "외국인20일(억)",
          "거래량대비%", "연속일", "신호"]


def _color(v):
    if isinstance(v, (int, float, np.floating)) and not pd.isna(v):
        return f"color: {UP}" if v > 0 else f"color: {DOWN}" if v < 0 else ""
    return {"강세": f"color: {UP}; font-weight: 600",
            "약세": f"color: {DOWN}; font-weight: 600"}.get(v, "")


COMPACT_COLS = ["순위", "종목", "현재가", "등락%", PROB_COL, "주요 근거"]


def compact() -> bool:
    """휴대폰용 '간단히 보기' (사이드바 토글, 휴대폰 브라우저면 기본 켜짐)."""
    return bool(st.session_state.get("compact"))


def show(df: pd.DataFrame, key: str, height: int | None = None, colcfg: dict | None = None,
         compact_cols: list[str] | None = None) -> None:
    """종목 표. 행을 누르면 종목 상세로 이동. df 에 'code' 열이 있어야 한다.
    간단히 보기면 compact_cols(기본 COMPACT_COLS) 열만."""
    if df.empty:
        st.caption("표시할 종목이 없습니다.")
        return
    codes = df["code"].tolist()
    view = df.reset_index(drop=True)
    if compact():
        view = view[["code"] + [c for c in (compact_cols or COMPACT_COLS) if c in view]]
    fmt = {k: v for k, v in NUMFMT.items() if k in view}
    if "시가총액" in view:
        view["시가총액"] = view["시가총액"].map(eok)
    styled = view.style.map(_color, subset=[c for c in SIGNED if c in view]).format(fmt, na_rep="-")
    cfg = {"code": None,
           PROB_COL: st.column_config.ProgressColumn(PROB_TITLE, format="%.2f",
                                                     min_value=0, max_value=1,
                                                     help=f"{H}거래일 뒤 {TARGET_LABEL}"),
           **(colcfg or {})}
    kw = {"height": height} if height else {}
    if compact():                                  # 휴대폰 화면 폭(약 360px)에 맞춤
        cfg.update({"현재가": st.column_config.Column(width=72), "등락%": st.column_config.Column(width=56),
                    PROB_COL: st.column_config.ProgressColumn(PROB_TITLE[:2], format="%.2f", min_value=0,
                                                              max_value=1, width=64)})

    def go():
        rows = st.session_state[key].selection.rows
        if rows:
            open_stock(codes[rows[0]])

    st.dataframe(styled, column_config=cfg, hide_index=True, width="stretch",
                 on_select=go, selection_mode="single-row", key=key, **kw)


# ── 이동 ───────────────────────────────────────────────
def open_stock(code: str) -> None:
    """콜백 안에서 호출: 종목 상세 페이지로 이동."""
    st.session_state["code"] = code
    st.session_state["page"] = "종목 상세"


@st.cache_data(ttl=300)
def model_meta(h: int) -> dict | None:
    """학습된 모델의 검증 결과 (models/model_h{h}.json)."""
    import json
    from stocklab.config import model_dir
    p = model_dir() / f"model_h{h}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def reliability(h: int) -> str:
    """'과거 검증: AUC 0.53 · 5개 구간 중 4개에서 상위 추천 > 하위' (+ 약하면 경고)."""
    m = model_meta(h)
    if not m or not m.get("cv"):
        return ""
    cv = m["cv"]
    good = sum(1 for c in cv if c["top20_ret"] > c["bottom20_ret"])
    auc = m["cv_mean"]["auc"]
    text = f"과거 검증: AUC {auc:.3f} · {len(cv)}개 기간 중 {good}개에서 상위 추천이 하위보다 좋았음"
    if auc < 0.53 or good < len(cv) - 1:
        text += " · ⚠️ 예측력이 약한 편이라 참고만 하세요"
    return text
