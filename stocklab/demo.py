"""API 키 없이 화면과 모델을 시험해 보기 위한 가상 데이터.

실제 시장 데이터가 아닙니다. 외국인 수급이 다음 날 수익률에 약간 영향을 주도록
만들어 두었기 때문에, 모델이 그 신호를 찾아내는지 확인하는 용도로 쓸 수 있습니다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import db

NAMES = ["데모전자", "데모반도체", "데모디스플레이", "데모소재", "데모화학", "데모자동차",
         "데모바이오", "데모금융", "데모증권", "데모건설", "데모조선", "데모항공", "데모게임",
         "데모통신", "데모에너지", "데모식품", "데모유통", "데모철강", "데모방산", "데모로봇",
         "데모배터리", "데모엔터", "데모제약", "데모보험", "데모운송", "데모인터넷", "데모장비",
         "데모부품", "데모화장품", "데모전력"]


def _vix(us: np.ndarray, rng) -> np.ndarray:
    v = np.zeros(len(us))
    for t in range(1, len(us)):                          # 평균 회귀 + 미국 급락 시 급등
        v[t] = 0.95 * v[t - 1] + rng.normal(0, 0.8) - us[t] * 150
    return np.clip(18 + v, 10, 60)


def generate(conn, n_days: int = 750, seed: int = 7) -> dict[str, str]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1),
                           periods=n_days)
    ds = dates.strftime("%Y-%m-%d")
    mkt = rng.normal(0.0003, 0.011, n_days)
    fx = 1300 * np.exp(np.cumsum(-0.3 * mkt + rng.normal(0, 0.004, n_days)))
    us = rng.normal(0.0004, 0.012, n_days)
    stocks = {f"9{i:05d}": n for i, n in enumerate(NAMES)}

    price_rows, flow_rows, news_rows, dis_rows = [], [], [], []
    for code in stocks:
        beta = rng.uniform(0.6, 1.4)
        f = np.zeros(n_days)
        for t in range(1, n_days):                       # 외국인 수급: 지속성 있는 흐름
            f[t] = 0.7 * f[t - 1] + rng.normal(0, 1)
        signal = 0.0025 * np.r_[0, f[:-1]] / 1.4          # 전날 수급 → 오늘 수익률(약한 신호)
        us_spill = 0.3 * np.r_[0, us[:-1]]                # 전날 미국 시장 → 오늘
        r = beta * mkt + signal + us_spill + rng.normal(0, 0.015, n_days)
        close = rng.uniform(10_000, 200_000) * np.exp(np.cumsum(r))
        close = np.round(close, -1)
        open_ = close / (1 + r) * (1 + rng.normal(0, 0.003, n_days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n_days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n_days)))
        volume = np.round(rng.lognormal(13, 0.4, n_days))
        price_rows.append(pd.DataFrame({
            "code": code, "date": ds, "open": open_.round(-1), "high": high.round(-1),
            "low": low.round(-1), "close": close, "volume": volume,
            "value": (close * volume / 1e6).round()}))
        fq = np.round(f * volume * 0.05)
        oq = np.round(rng.normal(0, 1, n_days) * volume * 0.03)
        flow_rows.append(pd.DataFrame({
            "code": code, "date": ds, "frgn_qty": fq, "orgn_qty": oq, "prsn_qty": -(fq + oq),
            "frgn_amt": (fq * close / 1e6).round(), "orgn_amt": (oq * close / 1e6).round(),
            "prsn_amt": (-(fq + oq) * close / 1e6).round()}))
        # 최근 60일만 뉴스가 있는 것처럼 (실제로도 뉴스 API 는 최근 기사만 준다)
        for t in range(n_days - 60, n_days):
            for k in range(rng.poisson(1.5)):
                s = float(np.clip(np.tanh(r[t] * 40) + rng.normal(0, 0.5), -1, 1))
                pub = datetime.combine(dates[t].date(), datetime.min.time()) + timedelta(
                    hours=int(rng.integers(7, 22)))
                eff = dates[t] + pd.Timedelta(days=1 if pub.hour >= 16 else 0)
                news_rows.append({"code": code, "link": f"demo://{code}/{t}/{k}",
                                  "pub_ts": pub.isoformat(timespec="minutes"),
                                  "eff_date": eff.strftime("%Y-%m-%d"),
                                  "title": f"[데모] {stocks[code]} 관련 가상 기사 {'호재' if s > 0 else '우려'}",
                                  "sentiment": round(s, 3)})
        for t in rng.choice(n_days, 6, replace=False):
            kind, sign = [("수주", 1), ("유상증자", -1), ("자사주매입", 1), ("실적", 0)][
                int(rng.integers(0, 4))]
            dis_rows.append({"code": code, "rcept_no": f"D{code}{t:05d}", "date": ds[t],
                             "eff_date": (dates[t] + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                             "title": f"[데모] {kind} 공시", "kind": kind, "sign": sign})

    db.upsert(conn, "prices", pd.concat(price_rows))
    db.upsert(conn, "flows", pd.concat(flow_rows))
    db.upsert(conn, "news", pd.DataFrame(news_rows))
    db.upsert(conn, "disclosures", pd.DataFrame(dis_rows))
    kospi = 2500 * np.exp(np.cumsum(mkt))
    macro = [("KOSPI", kospi, "KR"), ("KOSDAQ", 800 * np.exp(np.cumsum(mkt * 1.2)), "KR"),
             ("USDKRW", fx, "GLOBAL"), ("SP500", 5000 * np.exp(np.cumsum(us)), "GLOBAL"),
             ("NASDAQ", 16000 * np.exp(np.cumsum(us * 1.3)), "GLOBAL"),
             ("SOX", 4500 * np.exp(np.cumsum(us * 1.8)), "GLOBAL"),
             ("VIX", _vix(us, rng), "GLOBAL")]
    for name, vals, region in macro:
        db.upsert(conn, "macro", pd.DataFrame({"series": name, "date": ds, "value": vals,
                                               "region": region}))
    db.upsert(conn, "stocks", pd.DataFrame({"code": list(stocks), "name": list(stocks.values()),
                                            "source": "demo"}))
    db.set_meta(conn, "demo", "1")
    db.set_meta(conn, "last_update", datetime.now().isoformat(timespec="minutes"))
    conn.commit()
    return stocks
