"""명령줄 진입점:  python -m stocklab <명령>

  check    API 키·연결 확인 (처음 한 번)
  update   데이터 수집 → 스크리닝 → (필요 시 재학습) → 예측   ← 매일 이것만 실행
  train    모델 재학습
  predict  최신 데이터로 예측만
  demo     가상 데이터로 데모 DB 생성 (키 없이 화면 확인용)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta

import pandas as pd

from . import db, features, model, screener
from .config import LOG_DIR, ROOT, env, load_settings

log = logging.getLogger("stocklab")


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_DIR / "stocklab.log", encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _kis():
    from .kis import KisClient
    return KisClient(env("KIS_APP_KEY"), env("KIS_APP_SECRET"), env("KIS_ENV", "paper"))


def cmd_check(args) -> int:
    ok = True
    print(f"설정 파일: {ROOT / '.env'}")
    try:
        kis = _kis()
        kis.token()
        print(f"[OK] 한국투자증권 토큰 발급 ({kis.env})")
        df = kis.daily_prices("005930", date.today() - timedelta(days=10))
        print(f"[OK] 삼성전자 최근 일봉 {len(df)}건, 최근 종가 {df['close'].iloc[-1]:,.0f}원")
        fl = kis.investor_flow("005930")
        print(f"[OK] 투자자별 수급 {len(fl)}일치")
    except Exception as e:
        ok = False
        print(f"[실패] 한국투자증권: {e}")
    for name, keys in {"네이버 뉴스": ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"],
                       "DART 공시": ["DART_API_KEY"], "한국은행 ECOS": ["ECOS_API_KEY"]}.items():
        print(f"[{'OK' if all(env(k) for k in keys) else '미설정'}] {name} (선택)")
    return 0 if ok else 1


def _train_and_predict(conn, s, force_train: bool) -> None:
    h, target = int(s.model["horizon"]), s.model["target"]
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM prices")]
    panel = features.build_panel(conn, codes, h, target)
    bundle = model.load(h)
    stale = True
    if bundle:
        trained = datetime.strptime(bundle["meta"]["model_id"], "%Y%m%d-%H%M")
        stale = (datetime.now() - trained).days >= int(s.model["retrain_every_days"])
        stale = stale or bundle["meta"].get("target") != target
    if force_train or stale:
        log.info("모델 학습 중… (%d행, %d종목)", panel["y"].notna().sum(), len(codes))
        try:
            meta = model.train(panel, h, target)
        except ValueError as e:
            log.warning("학습 건너뜀: %s", e)
            return
        cm = meta["cv_mean"]
        if cm:
            log.info("검증 결과  AUC %.3f | 정확도 %.1f%% (기준선 %.1f%%) | 상위20%% 평균수익 %.2f%% vs 전체 %.2f%%",
                     cm["auc"], cm["accuracy"] * 100, cm["baseline_acc"] * 100,
                     cm["top20_ret"] * 100, cm["all_ret"] * 100)
        bundle = model.load(h)
    if not bundle:
        return
    pred = model.predict_latest(panel, bundle)
    pred = pd.DataFrame({"asof": pred["date"].dt.strftime("%Y-%m-%d"), "code": pred["code"],
                         "horizon": h, "prob": pred["prob"].round(4),
                         "model_id": bundle["meta"]["model_id"]})
    db.upsert(conn, "predictions", pred)
    conn.commit()
    log.info("예측 %d종목 저장", len(pred))


def cmd_update(args) -> int:
    from .collectors import dart, macro, market, news
    s = load_settings()
    stocks = s.all_stocks
    with db.session() as conn:
        db.upsert(conn, "stocks", pd.DataFrame(
            {"code": list(stocks), "name": list(stocks.values()),
             "source": ["watchlist" if c in s.watchlist else "universe" for c in stocks]}))
        years = int(s.collect["history_years"])
        if not args.skip_market:
            # 키·연결 문제를 종목마다 반복하지 않도록 먼저 한 번 확인한다
            try:
                kis = _kis()
                kis.token()
            except Exception as e:
                log.error("한국투자증권 연결 실패: %s", e)
                log.error("→ .env 의 KIS_APP_KEY / KIS_APP_SECRET / KIS_ENV 를 확인하고 check.bat 을 실행하세요.")
                return 1
            log.info("① 주가 수집 (%d종목) — 첫 실행은 10분 정도 걸립니다", len(stocks))
            market.update_prices(conn, kis, list(stocks), years)
            log.info("② 투자자 수급 수집")
            market.update_flows(conn, kis, list(stocks))
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        if n_prices == 0:
            log.error("주가 데이터가 0건입니다. 위의 '수집 실패' 메시지를 확인하세요.")
            return 1
        log.info("③ 거시지표 수집")
        macro.update_macro(conn, years)

        log.info("④ 외국인 수급 스크리닝")
        ranked = screener.run(conn, list(stocks))
        top = ranked.head(int(s.screener["auto_add_top"]))["code"].tolist() if not ranked.empty else []
        targets = {**{c: stocks[c] for c in top}, **s.watchlist}
        if top:
            log.info("  외국인 매수 강도 상위: %s", ", ".join(stocks[c] for c in top))

        log.info("⑤ 뉴스·공시 수집 (%d종목)", len(targets))
        news.update_news(conn, targets, int(s.collect["news_per_stock"]), s.sentiment["engine"])
        dart.update_disclosures(conn, targets, int(s.collect["dart_lookback_days"]))

        log.info("⑥ 예측")
        _train_and_predict(conn, s, force_train=False)
        db.set_meta(conn, "last_update", datetime.now().isoformat(timespec="minutes"))
    log.info("완료")
    return 0


def cmd_train(args) -> int:
    with db.session() as conn:
        _train_and_predict(conn, load_settings(), force_train=True)
    return 0


def cmd_predict(args) -> int:
    s = load_settings()
    with db.session() as conn:
        bundle = model.load(int(s.model["horizon"]))
        if not bundle:
            log.error("학습된 모델이 없습니다. 먼저 train 을 실행하세요.")
            return 1
        _train_and_predict(conn, s, force_train=False)
    return 0


def cmd_demo(args) -> int:
    from . import demo
    from .config import db_path
    path = db_path()
    if path.name == "market.sqlite":
        log.error("실제 데이터 DB 를 덮어쓰지 않도록 멈췄습니다. demo.bat 으로 실행하세요.")
        return 1
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        if args.if_empty and n:
            log.info("데모 데이터가 이미 있습니다 (%d행) → %s", n, path)
            return 0
        stocks = demo.generate(conn)
        log.info("데모 종목 %d개 생성 → %s", len(stocks), path)
        screener.run(conn)
        _train_and_predict(conn, load_settings(), force_train=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    p = argparse.ArgumentParser(prog="stocklab")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    u = sub.add_parser("update")
    u.add_argument("--skip-market", action="store_true", help="한국투자증권 수집 생략")
    u.set_defaults(fn=cmd_update)
    sub.add_parser("train").set_defaults(fn=cmd_train)
    sub.add_parser("predict").set_defaults(fn=cmd_predict)
    d = sub.add_parser("demo")
    d.add_argument("--if-empty", action="store_true", help="데모 데이터가 없을 때만 생성")
    d.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    return args.fn(args)
