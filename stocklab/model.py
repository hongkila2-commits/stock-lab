"""주가 방향 예측 모델 (LightGBM) + 시간 순서를 지키는 검증(walk-forward).

검증 방식
  과거 구간으로 학습 → 바로 다음 구간으로 평가 → 창을 앞으로 옮기며 반복.
  학습 끝과 평가 시작 사이에 horizon 일을 비워(purge) 목표값이 겹치지 않게 한다.

비교 기준(baseline)
  '항상 학습 구간에서 더 많았던 쪽(상승 또는 하락)으로 찍기'. 이것보다 못하면 모델은 쓸모없다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, roc_auc_score

from .config import model_dir
from .features import feature_columns

log = logging.getLogger(__name__)

PARAMS = dict(
    n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
    verbose=-1, random_state=42,
)
MIN_ROWS = 500


def _model() -> LGBMClassifier:
    return LGBMClassifier(**PARAMS)


def walk_forward(panel: pd.DataFrame, horizon: int, n_splits: int = 5,
                 test_frac: float = 0.4) -> pd.DataFrame:
    data = panel.dropna(subset=["y"])
    feats = feature_columns(data)
    dates = np.sort(data["date"].unique())
    test_dates = dates[int(len(dates) * (1 - test_frac)):]
    folds = np.array_split(test_dates, n_splits)
    rows = []
    for i, fold in enumerate(folds, 1):
        if len(fold) == 0:
            continue
        start = fold[0]
        cutoff_idx = np.searchsorted(dates, start) - horizon   # purge
        if cutoff_idx <= 0:
            continue
        train = data[data["date"] < dates[cutoff_idx]]
        test = data[data["date"].isin(fold)]
        if len(train) < MIN_ROWS or test["y"].nunique() < 2:
            continue
        m = _model().fit(train[feats], train["y"])
        prob = m.predict_proba(test[feats])[:, 1]
        majority = float(train["y"].mean() >= 0.5)
        top = test[prob >= np.quantile(prob, 0.8)]
        bottom = test[prob <= np.quantile(prob, 0.2)]
        rows.append({
            "fold": i,
            "test_start": pd.Timestamp(fold[0]).date(), "test_end": pd.Timestamp(fold[-1]).date(),
            "n_train": len(train), "n_test": len(test),
            "auc": roc_auc_score(test["y"], prob),
            "accuracy": accuracy_score(test["y"], prob >= 0.5),
            "baseline_acc": accuracy_score(test["y"], np.full(len(test), majority)),
            "top20_hit": top["y"].mean(),            # 상승확률 상위 20% 종목의 실제 상승 비율
            "top20_ret": top["fwd_ret"].mean(),      # 그들의 평균 수익률
            "bottom20_ret": bottom["fwd_ret"].mean(),
            "all_ret": test["fwd_ret"].mean(),
        })
    return pd.DataFrame(rows)


def train(panel: pd.DataFrame, horizon: int, target: str) -> dict:
    data = panel.dropna(subset=["y"])
    if len(data) < MIN_ROWS:
        raise ValueError(f"학습 데이터가 {len(data)}행뿐입니다(최소 {MIN_ROWS}). 먼저 update 로 데이터를 모으세요.")
    feats = feature_columns(data)
    cv = walk_forward(panel, horizon)
    model = _model().fit(data[feats], data["y"])
    model_id = datetime.now().strftime("%Y%m%d-%H%M")
    imp = (pd.Series(model.booster_.feature_importance("gain"), index=feats)
           .sort_values(ascending=False))
    meta = {
        "model_id": model_id, "horizon": horizon, "target": target, "features": feats,
        "n_rows": int(len(data)), "n_codes": int(data["code"].nunique()),
        "data_from": str(data["date"].min().date()), "data_to": str(data["date"].max().date()),
        "cv": json.loads(cv.to_json(orient="records", date_format="iso")),
        "cv_mean": {k: float(cv[k].mean()) for k in
                    ("auc", "accuracy", "baseline_acc", "top20_hit", "top20_ret",
                     "bottom20_ret", "all_ret")} if not cv.empty else {},
        "importance": {k: float(v) for k, v in imp.head(30).items()},
    }
    out = model_dir()
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "meta": meta}, out / f"model_h{horizon}.pkl")
    (out / f"model_h{horizon}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def load(horizon: int) -> dict | None:
    path = model_dir() / f"model_h{horizon}.pkl"
    return joblib.load(path) if path.exists() else None


def predict_latest(panel: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    """종목별 가장 최근 날짜 행으로 horizon 일 뒤 상승확률 계산."""
    feats = bundle["meta"]["features"]
    latest = panel.sort_values("date").groupby("code").tail(1).copy()
    for f in feats:                      # 학습 때 있던 특징이 지금 없으면 NaN 으로
        if f not in latest:
            latest[f] = np.nan
    latest["prob"] = bundle["model"].predict_proba(latest[feats])[:, 1]
    return latest[["date", "code", "close", "prob"]].reset_index(drop=True)
