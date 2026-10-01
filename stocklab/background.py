"""대시보드에서 update 를 별도 프로세스로 실행하고 진행 상황을 읽는다.

update 는 300종목 기준 약 5분(첫 실행 25분) 걸린다. 대시보드 안에서 기다리면 화면이 멈추므로
`python -m stocklab update` 를 따로 띄우고, 그 출력(logs/update_run.log)을 읽어 진행률을 보여준다.
대시보드를 새로고침하거나 닫아도 update 는 계속 진행된다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime

from . import runlock
from .config import LOG_DIR, ROOT

RUN_LOG = LOG_DIR / "update_run.log"
RUN_INFO = LOG_DIR / "update_run.json"
_procs: dict[int, subprocess.Popen] = {}      # 이 대시보드가 띄운 프로세스 (끝났는지·종료 코드 확인용)

# (표시, 이름, 진행률 시작, 끝) — 주가·수급이 시간 대부분을 차지한다
STEPS = [("⓪", "전체 종목 목록", 0.00, 0.04), ("①", "주가 수집", 0.04, 0.55),
         ("②", "투자자 수급·업종", 0.55, 0.80), ("③", "거시지표", 0.80, 0.84),
         ("④", "외국인 수급 스크리닝", 0.84, 0.87), ("⑤", "뉴스·공시", 0.87, 0.92),
         ("⑥", "예측", 0.92, 0.98), ("⑦", "저녁 요약 알림", 0.98, 0.99)]
LINE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d (INFO|WARNING|ERROR) (.*)$")
COUNT = re.compile(r"\[(\d+)/(\d+)\]|수급 (\d+)/(\d+) 종목")


def start(popen=subprocess.Popen) -> int:
    """update 를 백그라운드로 시작하고 PID 를 돌려준다. 이미 실행 중이면 runlock.AlreadyRunning."""
    info = runlock.holder()
    if info:
        raise runlock.AlreadyRunning(info)
    cmd = [sys.executable, "-m", "stocklab", "update",
           *shlex.split(os.environ.get("STOCKLAB_UPDATE_ARGS", ""))]     # 시험용 인자 (예: --skip-market)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    kw: dict = {}
    if sys.platform == "win32":
        # 창 없이 실행 · 대시보드 창의 Ctrl+C 가 전달되지 않게 별도 그룹
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG, "w", encoding="utf-8") as out:
        proc = popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, env=env, **kw)
    _procs[proc.pid] = proc
    RUN_INFO.write_text(json.dumps({"pid": proc.pid, "started": datetime.now().isoformat(timespec="seconds")}),
                        encoding="utf-8")
    return proc.pid


def _run_info() -> dict | None:
    try:
        info = json.loads(RUN_INFO.read_text(encoding="utf-8"))
        info["started"] = datetime.fromisoformat(info["started"])
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def status() -> dict:
    """{'running', 'ours'(이 화면에서 띄운 것), 'started', 'returncode'}"""
    info = _run_info()
    rc = None
    if info:
        proc = _procs.get(info["pid"])
        if proc is not None:
            rc = proc.poll()                          # 끝난 자식 프로세스 정리도 겸한다
            alive = rc is None
        else:                                         # 대시보드를 다시 켠 경우
            alive = (datetime.now() - info["started"] < runlock.MAX_AGE
                     and runlock.pid_alive(info["pid"]))
        if alive:
            return {"running": True, "ours": True, "started": info["started"], "returncode": None}
    lock = runlock.holder()
    if lock:                                          # update.bat · 예약 작업이 실행 중
        return {"running": True, "ours": False, "started": datetime.fromisoformat(lock["started"]),
                "returncode": None}
    return {"running": False, "ours": False, "started": info["started"] if info else None,
            "returncode": rc}


def read_log() -> str:
    try:
        return RUN_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def progress(text: str) -> dict:
    """로그에서 현재 단계·진행률·결과를 읽는다.

    outcome: None(진행 중이거나 '완료' 없이 끝남) · 'ok' · 'partial'(일부 종목 실패) · 'fail'(Traceback)
    errors: ERROR 줄 목록 — 한 수집기 실패처럼 이어서 진행되는 오류도 있으므로 결과 판단은 '완료' 줄로 한다
    """
    step, frac, count, last, cur = "시작 준비", 0.0, None, "", (0.0, 0.0)
    errors, warnings, outcome, message = [], 0, None, ""
    lines = text.splitlines()
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        level, msg = m.groups()
        body = msg.strip()
        for mark, name, lo, hi in STEPS:
            if body.startswith(mark):
                step, frac, count = name, lo, None
                cur = (lo, hi)
                break
        else:
            c = COUNT.search(body)
            if c and step in ("주가 수집", "투자자 수급·업종"):
                n, total = (int(x) for x in (c.group(1) or c.group(3), c.group(2) or c.group(4)))
                count = (n, total)
                lo, hi = cur
                frac = lo + (hi - lo) * min(n / max(total, 1), 1)
        if level == "ERROR":
            errors.append(body)
        elif level == "WARNING":
            warnings += 1
        if body.startswith("완료"):
            outcome = "partial" if level == "WARNING" else "ok"
            message, frac = body, 1.0
        if body:
            last = body
    if outcome is None and any(ln.startswith("Traceback") for ln in lines):
        outcome = "fail"
        message = next((ln for ln in reversed(lines) if ln.strip()), "알 수 없는 오류").strip()
    return {"step": step, "frac": round(frac, 3), "count": count, "last": last,
            "outcome": outcome, "message": message, "errors": errors, "warnings": warnings}


def last_result() -> dict | None:
    """끝난 마지막 실행의 결과. 실행 중이거나 기록이 없으면 None."""
    st = status()
    if st["running"] or st["started"] is None:
        return None
    p = progress(read_log())
    if p["outcome"] is None:                    # '완료' 줄 없이 끝남 = 실패
        p["outcome"] = "fail"
        rc = st["returncode"]
        p["message"] = (p["errors"][-1] if p["errors"]
                        else f"업데이트가 중간에 끝났습니다 (종료 코드 {rc})" if rc not in (None, 0)
                        else "업데이트가 중간에 끝났습니다 (PC 가 꺼졌거나 절전 모드였을 수 있음)")
    try:
        p["finished"] = datetime.fromtimestamp(RUN_LOG.stat().st_mtime)
    except OSError:
        p["finished"] = None
    p["started"] = st["started"]
    return p


def tail(text: str, n: int = 15) -> str:
    return "\n".join(text.splitlines()[-n:])
