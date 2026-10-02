"""명령줄 진입점:  python -m stocklab <명령>

  check    API 키·연결 확인 (처음 한 번)
  update   데이터 수집 → 스크리닝 → (필요 시 재학습) → 예측   ← 매일 이것만 실행
  train    모델 재학습
  predict  최신 데이터로 예측만
  demo     가상 데이터로 데모 DB 생성 (키 없이 화면 확인용)
  realtime 장중 실시간 시세 (realtime.bat)
  kakao-login / kakao-test   카카오톡 알림 설정·시험
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd

from . import db, features, model, picks, screener
from .config import LOG_DIR, ROOT, env, horizon_label, load_settings, sync_env

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


def cmd_env_sync(args) -> int:
    added = sync_env()
    if added:
        print(f"[OK] .env 에 새 설정 항목 {len(added)}개를 추가했습니다: {', '.join(added)}")
        print("     메모장으로 .env 를 열어 필요한 항목의 값을 채우세요 (맨 아래에 있습니다).")
    else:
        print("[OK] .env 에 빠진 설정 항목이 없습니다.")
    return 0


def cmd_stop_dashboard(args) -> int:
    from .netinfo import stop_dashboard
    pids = stop_dashboard(args.port)
    if pids:
        print(f"이미 실행 중이던 대시보드를 종료했습니다 (PID {', '.join(map(str, pids))}) — 새 코드로 다시 시작합니다.")
        time.sleep(2)                                  # 포트가 풀릴 때까지
    return 0


def cmd_check(args) -> int:
    ok = True
    print(f"설정 파일: {ROOT / '.env'}")
    added = sync_env()
    if added:
        print(f"[추가] .env 맨 아래에 새 설정 항목을 넣었습니다: {', '.join(added)}")
    try:
        kis = _kis()
        kis.token()
        print(f"[OK] 한국투자증권 토큰 발급 ({kis.env})")
        df = kis.daily_prices("005930", date.today() - timedelta(days=10))
        print(f"[OK] 삼성전자 최근 일봉 {len(df)}건, 최근 종가 {df['close'].iloc[-1]:,.0f}원")
        fl = kis.investor_flow("005930")
        print(f"[OK] 투자자별 수급 {len(fl)}일치")
        q = kis.quote("005930")
        print(f"[OK] 현재가 조회: 삼성전자 {q['price']:,.0f}원, 시가총액 {q['market_cap']:,.0f}억, "
              f"PER {q['per']}, 업종 {q['sector'] or '-'}")
    except Exception as e:
        ok = False
        print(f"[실패] 한국투자증권: {e}")
    from .collectors.listing import fetch_listing
    try:
        li = fetch_listing()
        sam = li[li["code"] == "005930"]
        print(f"[OK] 전체 종목 목록: {li['source'].iloc[0]} {li['asof'].max()} 기준 "
              f"KOSPI {(li['market'] == 'KOSPI').sum():,} · KOSDAQ {(li['market'] == 'KOSDAQ').sum():,}종목")
        if len(sam):
            r = sam.iloc[0]
            print(f"     삼성전자 {r['close']:,.0f}원 · 시가총액 {r['market_cap']:,.0f}억원 "
                  f"(약 {r['market_cap'] / 10000:,.0f}조 — 실제와 크게 다르면 알려주세요)")
    except Exception as e:
        print(f"[실패] 전체 종목 목록 (선택 기능 — 없으면 settings.yaml 의 종목만 분석): {e}")
    from .kakao import Kakao
    print(f"[{'OK' if (ROOT / 'data' / 'kakao_token.json').exists() else '미설정'}] 카카오톡 알림 (선택): {Kakao().status()}")
    check_news()
    check_dart()
    for name, keys in {"공공데이터포털 시세": ["DATA_GO_KR_API_KEY"], "한국은행 ECOS": ["ECOS_API_KEY"]}.items():
        print(f"[{'OK' if all(env(k) for k in keys) else '미설정'}] {name} (선택)")
    return 0 if ok else 1


def check_news() -> bool:
    """네이버 뉴스 키를 실제 호출로 확인 (키가 '있는지'만 보면 틀린 키를 못 잡는다)."""
    from .collectors import news
    if not news.has_keys():
        print("[미설정] 네이버 뉴스 (선택) — .env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET")
        return False
    try:
        items = news.fetch_news('"삼성전자"', 1)
    except Exception as e:
        print(f"[실패] 네이버 뉴스: {e}")
        return False
    from .sentiment import clean
    title = clean(items[0]["title"])[:40] if items else "(기사 없음)"
    print(f"[OK] 네이버 뉴스 ({news.platform_name()}): 삼성전자 최신 기사 '{title}'")
    return True


def check_dart() -> bool:
    from .collectors import dart
    key = env("DART_API_KEY")
    if not key:
        print("[미설정] DART 공시 (선택) — .env 에 DART_API_KEY")
        return False
    try:
        rows = dart.fetch_list(key, "00126380", date.today() - timedelta(days=30))   # 삼성전자
    except Exception as e:
        print(f"[실패] DART 공시: {e}")
        return False
    print(f"[OK] DART 공시: 삼성전자 최근 30일 {len(rows)}건")
    return True


def _train_and_predict(conn, s, force_train: bool, allow_train: bool = True) -> None:
    """설정의 예측 기간마다(기본 5·20거래일) 학습(필요 시)·예측·추천·근거 저장."""
    for h in s.horizons:
        log.info("── %s(%d거래일) 예측", horizon_label(h), h)
        _train_and_predict_one(conn, s, h, force_train, allow_train)


def _train_and_predict_one(conn, s, h: int, force_train: bool, allow_train: bool) -> None:
    target = s.model["target"]
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM prices")]
    panel = features.build_panel(conn, codes, h, target)
    bundle = model.load(h)
    stale = True
    if bundle:
        trained = datetime.strptime(bundle["meta"]["model_id"], "%Y%m%d-%H%M")
        stale = (datetime.now() - trained).days >= int(s.model["retrain_every_days"])
        stale = stale or bundle["meta"].get("target") != target
    if force_train or (stale and allow_train):
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

    top = picks.select_picks(conn, int(s.picks["count"]), float(s.picks["min_value_eok"]), h)
    log.info("추천 %d종목 선정 (거래대금 %s억 이상·보통주)", len(top), s.picks["min_value_eok"])
    picks.save_explanations(conn, picks.explanations(panel, bundle, horizon=h))


def analysis_targets(conn, s) -> dict[str, str]:
    """분석 대상 = 시총 상위 N(자동) ∪ settings.universe ∪ settings.watchlist ∪ 대시보드 관심종목."""
    from .collectors.listing import auto_universe
    auto = auto_universe(conn, int(s.universe_auto["top_market_cap"])) \
        if int(s.universe_auto["top_market_cap"]) > 0 else {}
    li = db.query(conn, "SELECT code, name FROM listing")
    name_of = dict(zip(li["code"], li["name"]))
    user = {c: name_of.get(c, c) for c in db.user_watchlist(conn)}
    return {**auto, **s.universe, **user, **s.watchlist}


def add_stock(code: str) -> str:
    """대시보드 '관심종목 추가': 등록 → (이력이 없으면) 일봉·수급 수집 → 기존 모델로 예측."""
    from .collectors import market
    s = load_settings()
    with db.session() as conn:
        conn.execute("INSERT OR IGNORE INTO user_watchlist VALUES (?, ?)",
                     (code, datetime.now().isoformat(timespec="seconds")))
        li = conn.execute("SELECT name FROM listing WHERE code = ?", (code,)).fetchone()
        if li:
            conn.execute("INSERT OR IGNORE INTO stocks VALUES (?, ?, 'user')", (code, li[0]))
        conn.commit()
        if db.last_date(conn, "prices", code):
            return "관심종목에 추가했습니다."
        try:
            kis = _kis()
            market.update_prices(conn, kis, [code], int(s.collect["history_years"]))
            market.update_flows(conn, kis, [code])
        except Exception as e:
            return f"관심종목에 추가했습니다. 시세는 다음 update 때 받습니다 (지금 수집 실패: {e})"
        if not db.last_date(conn, "prices", code):
            return "관심종목에 추가했습니다. 시세를 받지 못했습니다 — 다음 update 때 다시 시도합니다."
        _train_and_predict(conn, s, force_train=False, allow_train=False)
    return "관심종목에 추가하고 시세·예측을 받았습니다."


def remove_stock(code: str) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM user_watchlist WHERE code = ?", (code,))


def cmd_update(args) -> int:
    from . import runlock
    try:
        lock = runlock.acquire()
    except runlock.AlreadyRunning as e:
        log.error("%s 끝난 뒤에 다시 실행하세요.", e)
        return 2
    try:
        return _update(args)
    finally:
        runlock.release(lock)


def _update(args) -> int:
    from .collectors import dart, listing, macro, market, news
    s = load_settings()
    failed: list[str] = []
    added = sync_env()
    if added:
        log.info(".env 에 새 설정 항목 추가: %s (값은 비어 있음)", ", ".join(added))
    with db.session() as conn:
        log.info("⓪ 전체 종목 목록 (KOSPI·KOSDAQ)")
        listing.update_listing(conn)
        stocks = analysis_targets(conn, s)
        mine = set(s.watchlist) | set(db.user_watchlist(conn))
        db.upsert(conn, "stocks", pd.DataFrame(
            {"code": list(stocks), "name": list(stocks.values()),
             "source": ["watchlist" if c in mine else "universe" for c in stocks]}))
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
            log.info("① 주가 수집 (%d종목) — 처음 받는 종목은 종목당 약 5초 걸립니다", len(stocks))
            res = market.update_prices(conn, kis, list(stocks), years)
            failed = list(res["failed"])
            if res["stopped"]:
                log.warning("  서버 응답이 없어 수급·업종 수집은 건너뜁니다 (저장된 데이터로 예측은 계속).")
            else:
                log.info("② 투자자 수급 수집")
                failed += market.update_flows(conn, kis, list(stocks))["failed"]
                from .sectors import update_sectors
                update_sectors(conn, kis, list(stocks))
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        if n_prices == 0:
            log.error("주가 데이터가 0건입니다. 위의 '수집 실패' 메시지를 확인하세요.")
            return 1
        log.info("③ 거시지표 수집")
        macro.update_macro(conn, years)

        log.info("④ 외국인 수급 스크리닝")
        ranked = screener.run(conn, list(stocks))
        top = ranked.head(int(s.screener["auto_add_top"]))["code"].tolist() if not ranked.empty else []
        targets = {**{c: stocks[c] for c in top}, **{c: stocks[c] for c in mine if c in stocks}}
        if top:
            log.info("  외국인 매수 강도 상위: %s", ", ".join(stocks[c] for c in top))

        # 분석 대상 전체에서 모은다 (화면의 시총·AI 추천 종목도 뉴스가 보이게, 모델 특징값도 고르게).
        # 관심종목·외국인 상위를 먼저 → 중간에 멈춰도 중요한 종목은 받아둔다.
        news_targets = {**{c: stocks[c] for c in stocks if c in mine}, **targets, **stocks}
        log.info("⑤ 뉴스·공시 수집 (%d종목)", len(news_targets))
        news.update_news(conn, news_targets, int(s.collect["news_per_stock"]), s.sentiment["engine"])
        dart.update_disclosures(conn, news_targets, int(s.collect["dart_lookback_days"]))

        log.info("⑥ 예측")
        _train_and_predict(conn, s, force_train=False)
        db.set_meta(conn, "last_update", datetime.now().isoformat(timespec="minutes"))

        if s.alerts["daily_summary"]:
            from . import alerts, kakao
            text = alerts.daily_summary(conn, s)
            if text:
                log.info("⑦ 저녁 요약 알림\n%s", text)
                kakao.notify(text, bool(s.alerts["kakao"]))
    if not args.skip_market and failed:
        log.warning("완료 — 단, %d건은 받지 못했습니다(서버 응답 지연 등). 다음 update.bat 때 자동으로 다시 시도합니다.",
                    len(set(failed)))
    else:
        log.info("완료")
    return 0


def cmd_realtime(args) -> int:
    from . import realtime
    s = load_settings()
    t = realtime.now_kst()
    if not args.force and t.weekday() >= 5:
        log.info("주말에는 실시간 시세를 실행하지 않습니다.")
        return 0
    if not args.force and t.time() >= realtime.STOP:
        log.info("오늘 장이 끝났습니다 (15:30). 내일 장 시작 전에 다시 실행하세요.")
        return 0
    while not args.force and t.time() < realtime.OPEN:
        log.info("장 시작(09:00) 전입니다. 기다리는 중… (지금 %s)", t.strftime("%H:%M"))
        time.sleep(min(300, max(5, (datetime.combine(t.date(), realtime.OPEN) - t).seconds)))
        t = realtime.now_kst()
    try:
        kis = _kis()
        kis.token()
    except Exception as e:
        log.error("한국투자증권 연결 실패: %s — check.bat 으로 확인하세요.", e)
        return 1
    conn = db.connect()
    try:
        codes = realtime.targets(conn, s)
        if not codes:
            log.error("실시간으로 볼 종목이 없습니다. 관심종목을 추가하거나 update 를 먼저 실행하세요.")
            return 1
        log.info("실시간 시세 대상 %d종목 (관심종목 + AI 추천 상위)", len(codes))
        runner = realtime.Runner(conn, kis, s, codes,
                                 stop_at=realtime.STOP if not args.force else dtime(23, 59))
        if args.poll:
            runner.run_polling()
        else:
            runner.run()
    finally:
        conn.close()
    log.info("실시간 종료")
    return 0


def cmd_kakao_login(args) -> int:
    from . import kakao
    try:
        print(kakao.login())
    except Exception as e:
        print(f"[실패] {e}")
        return 1
    return cmd_kakao_test(args)


def cmd_kakao_test(args) -> int:
    from . import kakao
    try:
        n = kakao.Kakao().send("[StockLab] 카카오톡 알림 테스트입니다.\n이 메시지가 보이면 설정 완료!")
        print(f"[OK] 테스트 메시지 {n}건 보냄 — 카카오톡 '나와의 채팅'을 확인하세요.")
        return 0
    except Exception as e:
        print(f"[실패] {e}")
        return 1


def cmd_train(args) -> int:
    with db.session() as conn:
        _train_and_predict(conn, load_settings(), force_train=True)
    return 0


def cmd_predict(args) -> int:
    s = load_settings()
    with db.session() as conn:
        bundle = model.load(s.horizons[0])
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
        has_listing = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]
        if args.if_empty and n and has_listing:
            log.info("데모 데이터가 이미 있습니다 (%d행) → %s", n, path)
            return 0
    # 예전 버전 데모(전체 종목 목록 없음)거나 새로 만들 때: 가상 데이터 파일을 지우고 처음부터
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        p.unlink(missing_ok=True)
    with db.session() as conn:
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
    r = sub.add_parser("realtime", help="장중 실시간 시세")
    r.add_argument("--poll", action="store_true", help="WebSocket 대신 REST 조회로")
    r.add_argument("--force", action="store_true", help="장 시간이 아니어도 실행 (시험용)")
    r.set_defaults(fn=cmd_realtime)
    sub.add_parser("kakao-login").set_defaults(fn=cmd_kakao_login)
    sub.add_parser("kakao-test").set_defaults(fn=cmd_kakao_test)
    sub.add_parser("env-sync", help=".env 에 새 설정 항목 추가").set_defaults(fn=cmd_env_sync)
    sd = sub.add_parser("stop-dashboard", help="이미 실행 중인 대시보드 종료 (dashboard.bat 이 사용)")
    sd.add_argument("--port", type=int, default=8501)
    sd.set_defaults(fn=cmd_stop_dashboard)
    args = p.parse_args(argv)
    return args.fn(args)
