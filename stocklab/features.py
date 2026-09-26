"""모델 입력(특징값) 생성.

원칙: 날짜 t 의 특징값은 t 일 장 마감 시점까지 알 수 있는 정보만 쓴다.
- 기술지표: 과거 방향 rolling 만 사용
- 해외 지표: t 보다 '이전' 날짜 값만 사용 (미국 장은 한국 장 마감 뒤에 열린다)
- 뉴스: 15:30 이후 기사는 다음 날, 공시: 다음 날부터 반영
- 수급·뉴스처럼 최근 것만 모인 데이터는 수집 시작 이전을 0 이 아니라 '모름(NaN)' 으로 둔다.
  (0 으로 채우면 '과거엔 뉴스가 없었다' 는 거짓 패턴을 모델이 배운다)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db

META_COLS = ["date", "code", "close", "fwd_ret", "y"]
MACRO_RET = ["KOSPI", "KOSDAQ", "USDKRW", "SP500", "NASDAQ", "SOX", "DXY", "WTI", "USDJPY"]
MACRO_LEVEL = ["VIX", "US10Y"]


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def _streak(x: pd.Series) -> pd.Series:
    """연속 순매수(+)/순매도(-) 일수."""
    sign = np.sign(x.fillna(0))
    grp = (sign != sign.shift()).cumsum()
    out = sign.groupby(grp).cumsum()
    return out.where(x.notna())


def _technical(g: pd.DataFrame) -> pd.DataFrame:
    c, v = g["close"], g["volume"]
    r = c.pct_change(fill_method=None)
    out = pd.DataFrame(index=g.index)
    for n in (1, 5, 20, 60):
        out[f"ret_{n}"] = c.pct_change(n, fill_method=None)
    for n in (5, 20, 60):
        out[f"ma_gap_{n}"] = c / c.rolling(n).mean() - 1
    out["rsi_14"] = _rsi(c)
    out["vol_20"] = r.rolling(20).std()
    out["vol_ratio_5_60"] = r.rolling(5).std() / r.rolling(60).std()
    out["volume_ratio"] = v / v.rolling(20).mean()
    out["range_pct"] = (g["high"] - g["low"]) / c
    out["dist_52w_high"] = c / c.rolling(250, min_periods=120).max() - 1
    out["dist_52w_low"] = c / c.rolling(250, min_periods=120).min() - 1
    return out


def _flow(g: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=g.index)
    vol = g["volume"]
    for who in ("frgn", "orgn"):
        q = g[f"{who}_qty"]
        out[f"{who}_ratio_1"] = q / vol
        for n in (5, 20):
            out[f"{who}_ratio_{n}"] = q.rolling(n).sum() / vol.rolling(n).sum()
        out[f"{who}_streak"] = _streak(q)
    # 외국인·기관 동반 순매수 여부
    out["both_buy_5"] = ((g["frgn_qty"].rolling(5).sum() > 0)
                         & (g["orgn_qty"].rolling(5).sum() > 0)).astype(float)
    out.loc[out["frgn_ratio_5"].isna(), "both_buy_5"] = np.nan
    return out


def _macro(conn, dates: pd.DatetimeIndex) -> pd.DataFrame:
    m = db.query(conn, "SELECT series, date, value, region FROM macro")
    base = pd.DataFrame({"date": dates.sort_values().unique()})
    if m.empty:
        return base
    m["date"] = pd.to_datetime(m["date"])
    for name, g in m.groupby("series"):
        g = g.sort_values("date").set_index("date")
        s = g["value"]
        feats = pd.DataFrame(index=s.index)
        if name in MACRO_LEVEL:
            feats[f"{name}_lvl"] = s
            feats[f"{name}_chg5"] = s.diff(5)
        else:
            feats[f"{name}_r1"] = s.pct_change(fill_method=None)
            feats[f"{name}_r5"] = s.pct_change(5, fill_method=None)
            feats[f"{name}_r20"] = s.pct_change(20, fill_method=None)
        feats = feats.reset_index()
        base = pd.merge_asof(
            base, feats, on="date",
            # 해외 지표는 같은 날짜 값을 쓰면 미래 정보가 된다 → 엄격히 이전 값만
            allow_exact_matches=(g["region"].iloc[0] == "KR"),
            tolerance=pd.Timedelta(days=10),
        )
    return base


def _events_to_trading(events: pd.DataFrame, trading: pd.Series) -> pd.Series:
    """효력일(eff_date)을 그 날 또는 그 이후 첫 거래일로 옮긴다."""
    t = trading.sort_values().to_numpy()
    idx = np.searchsorted(t, events["eff_date"].to_numpy(), side="left")
    ok = idx < len(t)
    out = pd.Series(pd.NaT, index=events.index, dtype="datetime64[ns]")
    out[ok] = t[idx[ok]]
    return out


def _event_feats(prices: pd.DataFrame, events: pd.DataFrame, value_cols: dict,
                 window: int) -> pd.DataFrame:
    """종목별 이벤트(뉴스·공시)를 거래일에 붙이고 window 거래일 합계를 만든다."""
    out = []
    for code, p in prices.groupby("code", sort=False):
        f = pd.DataFrame(index=p.index)
        e = events[events["code"] == code]
        if e.empty:
            for k in value_cols:
                f[k] = np.nan
            out.append(f)
            continue
        e = e.assign(tdate=_events_to_trading(e, p["date"])).dropna(subset=["tdate"])
        daily = e.groupby("tdate").agg(**{k: v for k, v in value_cols.items()})
        joined = p[["date"]].join(daily, on="date").fillna(0)
        rolled = joined.drop(columns="date").rolling(window, min_periods=1).sum()
        rolled.loc[p["date"] < e["eff_date"].min()] = np.nan   # 수집 시작 전 = 모름
        out.append(rolled)
    return pd.concat(out) if out else pd.DataFrame(index=prices.index)


def build_panel(conn, codes: list[str] | None = None, horizon: int = 5,
                target: str = "excess") -> pd.DataFrame:
    sql = "SELECT * FROM prices"
    params: tuple = ()
    if codes:
        sql += f" WHERE code IN ({','.join('?' * len(codes))})"
        params = tuple(codes)
    p = db.query(conn, sql, params)
    if p.empty:
        return pd.DataFrame(columns=META_COLS)
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["code", "date"]).reset_index(drop=True)

    fl = db.query(conn, "SELECT * FROM flows")
    if not fl.empty:
        fl["date"] = pd.to_datetime(fl["date"])
        p = p.merge(fl, on=["code", "date"], how="left")
    else:
        for c in ("frgn_qty", "orgn_qty", "prsn_qty", "frgn_amt", "orgn_amt", "prsn_amt"):
            p[c] = np.nan

    parts = []
    for _, g in p.groupby("code", sort=False):
        parts.append(pd.concat([_technical(g), _flow(g)], axis=1))
    feats = pd.concat(parts)
    df = pd.concat([p[["date", "code", "close"]], feats], axis=1)

    # 거시지표 + 시장 대비 상대강도
    macro = _macro(conn, pd.DatetimeIndex(p["date"]))
    df = df.merge(macro, on="date", how="left")
    if "KOSPI_r5" in df:
        df["rel_ret_5"] = df["ret_5"] - df["KOSPI_r5"]
        df["rel_ret_20"] = df["ret_20"] - df["KOSPI_r20"]

    # 뉴스 감성
    news = db.query(conn, "SELECT code, eff_date, sentiment FROM news")
    if not news.empty:
        news["eff_date"] = pd.to_datetime(news["eff_date"])
        nf = _event_feats(df[["date", "code"]], news,
                          {"news_cnt_5": ("sentiment", "size"),
                           "news_sent_sum_5": ("sentiment", "sum")}, 5)
        df["news_cnt_5"] = nf["news_cnt_5"]
        df["news_sent_5"] = nf["news_sent_sum_5"] / nf["news_cnt_5"].replace(0, np.nan)
        df.loc[nf["news_cnt_5"] == 0, "news_sent_5"] = 0.0

    # 공시
    dis = db.query(conn, "SELECT code, eff_date, sign FROM disclosures")
    if not dis.empty:
        dis["eff_date"] = pd.to_datetime(dis["eff_date"])
        dis["pos"] = (dis["sign"] > 0).astype(float)
        dis["neg"] = (dis["sign"] < 0).astype(float)
        dfe = _event_feats(df[["date", "code"]], dis,
                           {"dart_pos_20": ("pos", "sum"), "dart_neg_20": ("neg", "sum")}, 20)
        df[["dart_pos_20", "dart_neg_20"]] = dfe[["dart_pos_20", "dart_neg_20"]]

    # 목표값: horizon 거래일 뒤 수익률
    df["fwd_ret"] = df.groupby("code")["close"].shift(-horizon) / df["close"] - 1
    if target == "excess":
        k = db.query(conn, "SELECT date, value FROM macro WHERE series='KOSPI' ORDER BY date")
        if k.empty:
            raise ValueError("target: excess 는 KOSPI 지수 데이터가 필요합니다.")
        k["date"] = pd.to_datetime(k["date"])
        k["kospi_fwd"] = k["value"].shift(-horizon) / k["value"] - 1
        df = df.merge(k[["date", "kospi_fwd"]], on="date", how="left")
        df["fwd_ret"] = df["fwd_ret"] - df.pop("kospi_fwd")
    df["y"] = (df["fwd_ret"] > 0).astype(float).where(df["fwd_ret"].notna())
    return df.replace([np.inf, -np.inf], np.nan)


def feature_columns(panel: pd.DataFrame) -> list[str]:
    return [c for c in panel.columns if c not in META_COLS]
