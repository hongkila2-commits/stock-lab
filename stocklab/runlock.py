"""update 중복 실행 방지 잠금.

대시보드의 '🔄 지금 업데이트' 와 예약 작업(18:10)·update.bat 이 겹치면 같은 종목을 두 번 받고
한국투자증권 호출 제한에 걸린다. 그래서 update 는 시작할 때 잠금 파일을 만들고 끝나면 지운다.

- 잠금 파일: DB 옆 `<DB이름>.update.lock` (예: data/market.update.lock) — 데모 DB 와 따로 잠긴다
- 프로세스가 강제 종료돼 남은 잠금은 PID 가 죽었거나 너무 오래됐으면 무시한다
- **Windows 에서 os.kill(pid, 0) 은 프로세스를 종료시킨다.** 그래서 생존 확인은 ctypes 로 한다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import db_path

MAX_AGE = timedelta(hours=6)     # 이보다 오래된 잠금은 PID 재사용 가능성 → 무시


class AlreadyRunning(RuntimeError):
    def __init__(self, info: dict):
        self.info = info
        super().__init__(f"이미 업데이트가 실행 중입니다 (PID {info.get('pid')}, "
                         f"시작 {str(info.get('started', '?'))[11:16]}).")


def lock_path() -> Path:
    p = db_path()
    return p.with_name(f"{p.stem}.update.lock")


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5           # 접근 거부 = 존재는 함
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def holder(path: Path | None = None) -> dict | None:
    """살아 있는 잠금의 {pid, started}. 없거나 죽은 잠금이면 None."""
    path = path or lock_path()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        started = datetime.fromisoformat(info["started"])
    except (KeyError, TypeError, ValueError):
        return None
    if datetime.now() - started > MAX_AGE or not pid_alive(info.get("pid")):
        return None
    return info


def acquire(path: Path | None = None) -> Path:
    """잠금을 잡는다. 다른 update 가 실행 중이면 AlreadyRunning."""
    path = path or lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds")})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = holder(path)
            if info:
                raise AlreadyRunning(info)
            path.unlink(missing_ok=True)         # 죽은 프로세스가 남긴 잠금
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return path
    raise AlreadyRunning({"pid": "?", "started": "?"})


def release(path: Path | None = None) -> None:
    path = path or lock_path()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass
