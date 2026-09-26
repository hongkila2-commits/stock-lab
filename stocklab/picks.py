"""추천 종목 선정 · 예측 근거 · 추천 성적 추적."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db
from .collectors.listing import is_common
from .features import MACRO_LEVEL, MACRO_RET

# 특징값 → 사람이 읽는 이름
FEATURE_LABELS = {
    "ret_1": "1일 수익률", "ret_5": "5일 수익률", "ret_20": "20일 수익률", "ret_60": "60일 수익률",
    "ma_gap_5": "5일선 대비 위치", "ma_gap_20": "20일선 대비 위치", "ma_gap_60": "60일선 대비 위치",
    "rsi_14": "RSI(14)", "vol_20": "20일 변동성", "vol_ratio_5_60": "최근 변동성 확대",
    "volume_ratio": "거래량 급증", "range_pct": "당일 변동폭",
    "dist_52w_high": "52주 최고가 대비", "dist_52w_low": "52주 최저가 대비",
    "frgn_ratio_1": "외국인 당일 순매수 비중", "frgn_ratio_5": "외국인 5일 순매수 비중",
    "frgn_ratio_20": "외국인 20일 순매수 비중", "frgn_streak": "외국인 연속 순매수",
    "orgn_ratio_1": "기관 당일 순매수 비중", "orgn_ratio_5": "기관 5일 순매수 비중",
    "orgn_ratio_20": "기관 20일 순매수 비중", "orgn_streak": "기관 연속 순매수",
    "both_buy_5": "외국인·기관 동반 매수", "rel_ret_5": "코스피 대비 5일 상대강도",
    "rel_ret_20": "코스피 대비 20일 상대강도", "news_cnt_5": "최근 5일 뉴스 수",
    "news_sent_5": "최근 뉴스 분위기", "dart_pos_20": "최근 호재성 공시", "dart_neg_20": "최근 악재성 공시",
}
MACRO_NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "USDKRW": "원/달러", "SP500": "S&P500",
               "NASDAQ": "나스닥", "SOX": "반도체지수", "DXY": "달러인덱스", "WTI": "유가",
               "USDJPY": "엔/달러", "VIX": "VIX", "US10Y": "미 10년물"}


def is_macro(feature: str) -> bool:
    return feature.split("_")[0] in set(MACRO_RET) | set(MACRO_LEVEL) | {"USDKRW"}


def label(feature: str) -> str:
    if feature in FEATURE_LABELS:
        return FEATURE_LABELS[feature]
    head, _, tail = feature.partition("_")
    if head in MACRO_NAMES:
        period = {"r1": "1일 변화", "r5": "5일 변화", "r20": "20일 변화", "lvl": "수준", "chg5": "5일 변화"}
        return f"{MACRO_NAMES[head]} {period.get(tail, tail)}"
    return feature


def describe(feature: str, value: float, pct: float, contrib: float | None = None) -> str:
    """'외국인 5일 순매수 비중 상위 5% ▲' 같은 한 줄 설명."""
    arrow = "" if contrib is None or pd.isna(contrib) else (" ▲" if contrib > 0 else " ▼")
    if is_macro(feature):   # 시장 전체에 같은 값 → 순위 대신 값 자체
        if feature.endswith("_lvl"):
            v = f"{value:.1f}"
        elif feature.endswith("_chg5"):
            v = f"{value:+.2f}"
        else:
            v = f"{value * 100:+.1f}%"
        return f"[시장] {label(feature)} {v}{arrow}"
    if pd.isna(pct):
        return f"{label(feature)}{arrow}"
    rank = f"상위 {max(1, round((1 - pct) * 100))}%" if pct >= 0.5 else f"하위 {max(1, round(pct * 100))}%"
    return f"{label(feature)} {rank}{arrow}"


# ── 추천 선정 ───────────────────────────────────────────
def eligible(conn, codes: list[str], min_value_eok: float = 10, min_history: int = 120) -> pd.Series:
    """추천 가능 여부: 보통주 · 20일 평균 거래대금 ≥ 기준 · 최신 데이터 · 충분한 이력."""
    p = db.query(conn, "SELECT code, date, value FROM prices")
    if p.empty:
        return pd.Series(False, index=codes)
    latest = p["date"].max()
    p = p.sort_values(["code", "date"])
    g = p.groupby("code")
    stat = pd.DataFrame({
        "last": g["date"].max(), "n": g.size(),
        "value20": g["value"].apply(lambda s: s.tail(20).mean()),   # 백만원
    })
    names = db.query(conn, "SELECT code, name FROM stocks UNION SELECT code, name FROM listing")
    name_of = dict(zip(names["code"], names["name"]))
    ok = {}
    for c in codes:
        if c not in stat.index:
            ok[c] = False
            continue
        s = stat.loc[c]
        ok[c] = bool(is_common(c, name_of.get(c, "")) and s["last"] == latest
                     and s["n"] >= min_history and s["value20"] >= min_value_eok * 100)
    return pd.Series(ok)


def select_picks(conn, count: int = 30, min_value_eok: float = 10, horizon: int = 5) -> pd.DataFrame:
    pred = db.query(conn, "SELECT asof, code, prob FROM predictions WHERE horizon = ? AND asof = "
                          "(SELECT MAX(asof) FROM predictions WHERE horizon = ?)", (horizon, horizon))
    if pred.empty:
        return pred
    ok = eligible(conn, pred["code"].tolist(), min_value_eok)
    pred = pred[pred["code"].map(ok).fillna(False).astype(bool)]
    top = pred.sort_values("prob", ascending=False).head(count).reset_index(drop=True)
    top["rank"] = np.arange(1, len(top) + 1)
    out = top[["asof", "code", "rank", "prob"]]
    if len(out):
        conn.execute("DELETE FROM picks WHERE asof = ?", (out["asof"].iloc[0],))
        db.upsert(conn, "picks", out)
    conn.commit()
    return out


# ── 예측 근거 ───────────────────────────────────────────
def explanations(panel: pd.DataFrame, bundle: dict, top_k: int = 5) -> pd.DataFrame:
    """최신일 종목별 근거 top_k. LightGBM 이면 SHAP 기여도, 아니면 중요 특징 중 두드러진 것."""
    feats = bundle["meta"]["features"]
    day = panel["date"].max()
    latest = panel[panel["date"] == day].copy()
    if latest.empty:
        return pd.DataFrame()
    for f in feats:
        if f not in latest:
            latest[f] = np.nan
    X = latest[feats]
    pct = X.rank(pct=True)
    model = bundle["model"]
    contrib = None
    if hasattr(model, "booster_"):                     # LightGBM: 로그오즈 기여도
        contrib = pd.DataFrame(model.predict(X, pred_contrib=True)[:, :-1],
                               index=X.index, columns=feats)
    important = list(bundle["meta"].get("importance", {}))[:15] or feats
    rows = []
    for i, code in zip(X.index, latest["code"]):
        if contrib is not None:
            order = contrib.loc[i].abs().sort_values(ascending=False).index
        else:
            ext = (pct.loc[i, [f for f in important if f in pct]] - 0.5).abs()
            ext = ext[[not is_macro(f) for f in ext.index]]
            order = ext.sort_values(ascending=False).index
        for f in [f for f in order if pd.notna(X.at[i, f])][:top_k]:
            rows.append({"asof": day.strftime("%Y-%m-%d"), "code": code, "feature": f,
                         "value": float(X.at[i, f]), "pct": float(pct.at[i, f]),
                         "contrib": None if contrib is None else float(contrib.at[i, f])})
    return pd.DataFrame(rows)


def save_explanations(conn, df: pd.DataFrame) -> None:
    if df.empty:
        return
    conn.execute("DELETE FROM explain WHERE asof = ?", (df["asof"].iloc[0],))
    db.upsert(conn, "explain", df)
    conn.commit()


# ── 추천 성적 ───────────────────────────────────────────
def pick_performance(conn, horizon: int = 5, excess: bool = True) -> pd.DataFrame:
    """날짜별: 추천 종목 평균 수익률 vs 그날 예측한 전체 종목 평균 (horizon 거래일 뒤, 실현된 것만)."""
    picks = db.query(conn, "SELECT asof, code FROM picks")
    if picks.empty:
        return pd.DataFrame()
    pr = db.query(conn, "SELECT code, date, close FROM prices").sort_values(["code", "date"])
    pr["fwd"] = pr.groupby("code")["close"].shift(-horizon) / pr["close"] - 1
    fwd = pr.dropna(subset=["fwd"]).set_index(["code", "date"])["fwd"]
    if excess:
        k = db.query(conn, "SELECT date, value FROM macro WHERE series='KOSPI' ORDER BY date")
        if not k.empty:
            k["kfwd"] = k["value"].shift(-horizon) / k["value"] - 1
            kfwd = k.dropna().set_index("date")["kfwd"]
            idx = fwd.index.get_level_values("date")
            fwd = fwd - kfwd.reindex(idx).to_numpy()
    allp = db.query(conn, "SELECT asof, code FROM predictions WHERE horizon = ?", (horizon,))
    rows = []
    for asof, g in picks.groupby("asof"):
        r = fwd.reindex(list(zip(g["code"], [asof] * len(g)))).dropna()
        if r.empty:
            continue
        a = allp[allp["asof"] == asof]
        ra = fwd.reindex(list(zip(a["code"], [asof] * len(a)))).dropna()
        rows.append({"asof": asof, "n": len(r), "pick_ret": r.mean(), "all_ret": ra.mean(),
                     "hit": (r > 0).mean()})
    return pd.DataFrame(rows)
