"""대시보드 '지금 업데이트': 중복 실행 잠금 · 프로세스 생존 확인 · 로그 진행률 · 실행 명령."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from stocklab import background as bg
from stocklab import cli, runlock


@pytest.fixture
def lock(tmp_path):
    return tmp_path / "market.update.lock"


def test_acquire_and_release(lock):
    runlock.acquire(lock)
    assert json.loads(lock.read_text())["pid"] == os.getpid()
    assert runlock.holder(lock)["pid"] == os.getpid()
    runlock.release(lock)
    assert not lock.exists()


def test_second_acquire_is_refused(lock):
    runlock.acquire(lock)
    with pytest.raises(runlock.AlreadyRunning, match="이미 업데이트"):
        runlock.acquire(lock)


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_stale_lock_from_dead_process_is_ignored(lock):
    lock.write_text(json.dumps({"pid": dead_pid(), "started": datetime.now().isoformat()}))
    assert runlock.holder(lock) is None
    runlock.acquire(lock)
    assert runlock.holder(lock)["pid"] == os.getpid()


def test_very_old_lock_is_ignored(lock):
    old = (datetime.now() - runlock.MAX_AGE - timedelta(minutes=1)).isoformat()
    lock.write_text(json.dumps({"pid": os.getpid(), "started": old}))      # PID 재사용 대비
    assert runlock.holder(lock) is None


def test_broken_lock_file_is_ignored(lock):
    lock.write_text("{망가진")
    runlock.acquire(lock)


def test_release_keeps_other_process_lock(lock):
    lock.write_text(json.dumps({"pid": os.getpid() + 1, "started": datetime.now().isoformat()}))
    runlock.release(lock)
    assert lock.exists()


def test_pid_alive():
    assert runlock.pid_alive(os.getpid())
    assert not runlock.pid_alive(dead_pid())
    assert not runlock.pid_alive(0) and not runlock.pid_alive(None)


def test_cli_update_refused_while_running(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("STOCKLAB_DB", str(tmp_path / "market.sqlite"))
    runlock.acquire()                    # 다른 update 가 실행 중인 상황 (이 테스트 프로세스가 잡음)
    called = []
    monkeypatch.setattr(cli, "_update", lambda a: called.append(1) or 0)
    assert cli.main(["update"]) == 2 and not called
    assert "이미 업데이트가 실행 중" in caplog.text


def test_cli_update_releases_lock_even_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKLAB_DB", str(tmp_path / "market.sqlite"))

    def boom(a):
        raise RuntimeError("수집 중 오류")
    monkeypatch.setattr(cli, "_update", boom)
    with pytest.raises(RuntimeError):
        cli.main(["update"])
    assert not runlock.lock_path().exists()


# ── 로그 진행률 ─────────────────────────────────────────
def L(level, msg, t="10-01 18:10:00"):
    return f"{t} {level} {msg}"


def test_progress_tracks_steps_and_counts():
    log = [L("INFO", "⓪ 전체 종목 목록 (KOSPI·KOSDAQ)"),
           L("INFO", "① 주가 수집 (300종목) — 처음 받는 종목은 종목당 약 5초 걸립니다"),
           L("INFO", "  [1/300] 005930 일봉 3건"),
           L("WARNING", "  [150/300] 000660 일봉 실패: [NETWORK] 응답 없음")]
    p = bg.progress("\n".join(log))
    assert p["step"] == "주가 수집" and p["count"] == (150, 300)
    assert 0.25 < p["frac"] < 0.35 and p["outcome"] is None and p["warnings"] == 1

    log += [L("INFO", "② 투자자 수급 수집"), L("INFO", "  수급 30/300 종목 완료")]
    p = bg.progress("\n".join(log))
    assert p["step"] == "투자자 수급·업종" and p["count"] == (30, 300) and 0.55 < p["frac"] < 0.6

    log += [L("INFO", "③ 거시지표 수집"), L("INFO", "⑥ 예측"), L("INFO", "── 1주(5거래일) 예측")]
    p = bg.progress("\n".join(log))
    assert p["step"] == "예측" and p["count"] is None and p["last"].startswith("── 1주")


def test_progress_outcomes():
    ok = bg.progress("\n".join([L("INFO", "⑥ 예측"), L("INFO", "완료")]))
    assert ok["outcome"] == "ok" and ok["frac"] == 1.0
    partial = bg.progress(L("WARNING", "완료 — 단, 3건은 받지 못했습니다(서버 응답 지연 등)."))
    assert partial["outcome"] == "partial" and "3건" in partial["message"]
    crash = bg.progress("\n".join([L("INFO", "③ 거시지표 수집"), "Traceback (most recent call last):",
                                   '  File "x.py", line 1', "KeyError: 'close'"]))
    assert crash["outcome"] == "fail" and crash["message"] == "KeyError: 'close'"
    # '완료' 줄이 없고 ERROR 로 끝남 → 진행 중으로 보이되 오류 목록에 남는다 (끝났는지는 status 가 판단)
    err = bg.progress("\n".join([L("ERROR", "한국투자증권 연결 실패: [EGW00103] 유효하지 않은 AppKey")]))
    assert err["outcome"] is None and "AppKey" in err["errors"][-1]


# ── 실행 ───────────────────────────────────────────────
class FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append((cmd, kw))
        self.pid, self.rc = 4242, None
        kw["stdout"].write(L("INFO", "⓪ 전체 종목 목록 (KOSPI·KOSDAQ)") + "\n")

    def poll(self):
        return self.rc


@pytest.fixture
def runfiles(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKLAB_DB", str(tmp_path / "market.sqlite"))
    monkeypatch.setattr(bg, "RUN_LOG", tmp_path / "update_run.log")
    monkeypatch.setattr(bg, "RUN_INFO", tmp_path / "update_run.json")
    monkeypatch.setattr(bg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bg, "_procs", {})
    FakePopen.calls = []
    return tmp_path


def test_start_runs_update_in_background(runfiles, monkeypatch):
    monkeypatch.setenv("STOCKLAB_UPDATE_ARGS", "--skip-market")
    assert bg.start(popen=FakePopen) == 4242
    cmd, kw = FakePopen.calls[0]
    assert cmd == [sys.executable, "-m", "stocklab", "update", "--skip-market"]
    assert kw["env"]["PYTHONUTF8"] == "1" and kw["env"]["PYTHONUNBUFFERED"] == "1"
    assert kw["stderr"] == subprocess.STDOUT
    assert ("creationflags" in kw) if sys.platform == "win32" else kw["start_new_session"]
    assert "전체 종목 목록" in bg.read_log()

    s = bg.status()
    assert s["running"] and s["ours"]
    assert bg.last_result() is None                   # 실행 중에는 결과 없음

    bg._procs[4242].rc = 0                             # 끝남
    (runfiles / "update_run.log").write_text(L("INFO", "완료") + "\n", encoding="utf-8")
    assert not bg.status()["running"]
    assert bg.last_result()["outcome"] == "ok"


def test_failed_run_reports_last_error(runfiles, monkeypatch):
    monkeypatch.delenv("STOCKLAB_UPDATE_ARGS", raising=False)
    bg.start(popen=FakePopen)
    assert FakePopen.calls[0][0][-1] == "update"
    bg._procs[4242].rc = 1
    (runfiles / "update_run.log").write_text("\n".join([
        L("ERROR", "한국투자증권 연결 실패: [EGW00103] 유효하지 않은 AppKey"),
        L("ERROR", "→ .env 의 KIS_APP_KEY / KIS_APP_SECRET / KIS_ENV 를 확인하고 check.bat 을 실행하세요.")]),
        encoding="utf-8")
    r = bg.last_result()
    assert r["outcome"] == "fail" and "check.bat" in r["message"]


def test_start_refused_when_other_update_running(runfiles):
    runlock.acquire()                                  # update.bat 이 실행 중
    with pytest.raises(runlock.AlreadyRunning):
        bg.start(popen=FakePopen)
    assert not FakePopen.calls
    s = bg.status()
    assert s["running"] and not s["ours"]


def test_status_after_dashboard_restart(runfiles):
    """대시보드를 다시 켜서 Popen 객체가 없어도 PID 로 실행 여부를 안다."""
    (runfiles / "update_run.json").write_text(json.dumps(
        {"pid": os.getpid(), "started": datetime.now().isoformat()}))
    assert bg.status() == {"running": True, "ours": True, "started": bg.status()["started"],
                           "returncode": None}
    (runfiles / "update_run.json").write_text(json.dumps(
        {"pid": dead_pid(), "started": datetime.now().isoformat()}))
    assert not bg.status()["running"]
