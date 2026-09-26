"""설치 진단 — 표준 라이브러리만 사용 (패키지가 차단돼도 실행된다).

    .venv\\Scripts\\python scripts\\doctor.py            진단만
    .venv\\Scripts\\python scripts\\doctor.py --unblock  인터넷 다운로드 표시(차단 원인) 제거

확인 항목
  1. 파이썬 버전
  2. Windows 스마트 앱 컨트롤(SAC) 상태
  3. 프로젝트 파일에 '인터넷에서 받은 파일' 표시(Zone.Identifier)가 남아 있는지
  4. 패키지를 하나씩 불러와 보고, 차단된 것이 있으면 이름과 실제 오류를 출력
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (불러올 모듈, 설치 이름, 필수 여부)
PACKAGES = [
    ("numpy", "numpy", True), ("pandas", "pandas", True), ("scipy", "scipy", True),
    ("sklearn", "scikit-learn", True), ("joblib", "joblib", True),
    ("requests", "requests", True), ("yaml", "pyyaml", True), ("dotenv", "python-dotenv", True),
    ("pyarrow", "pyarrow", True), ("plotly", "plotly", True), ("streamlit", "streamlit", True),
    ("websocket", "websocket-client", True),
    ("tzdata", "tzdata", os.name == "nt"),        # Windows 에는 시간대 정보가 없어서 필요
    ("lightgbm", "lightgbm", False),
]
BLOCK_HINTS = ("4551", "application control", "응용 프로그램 제어", "차단")


def sac_state() -> str:
    if os.name != "nt":
        return "Windows 아님"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            v, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
        return {0: "꺼짐", 1: "켜짐 (차단 중)", 2: "평가 모드 (일부 차단 가능)"}.get(v, f"알 수 없음({v})")
    except OSError:
        return "확인 불가 (Windows 10 이거나 설정 없음)"


def marked_files() -> list[Path]:
    """다운로드 표시가 붙은 파일 (NTFS 대체 데이터 스트림 Zone.Identifier)."""
    if os.name != "nt":
        return []
    out = []
    for p in ROOT.rglob("*"):
        if ".venv" in p.parts or not p.is_file():
            continue
        try:
            with open(f"{p}:Zone.Identifier", encoding="utf-8", errors="ignore"):
                out.append(p)
        except OSError:
            pass
    return out


def main() -> int:
    unblock = "--unblock" in sys.argv
    print("=" * 60)
    print(f"파이썬  {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 11):
        print("  [주의] 3.11 이상이 필요합니다.")
    elif sys.version_info >= (3, 14):
        print("  [주의] 너무 최신 버전이라 일부 패키지가 아직 지원하지 않을 수 있습니다. 3.12 권장.")
    print(f"스마트 앱 컨트롤  {sac_state()}")

    marked = marked_files()
    if marked:
        print(f"다운로드 표시가 남은 파일 {len(marked)}개 (예: {marked[0].name})")
        if unblock:
            for p in marked:
                try:
                    os.remove(f"{p}:Zone.Identifier")
                except OSError:
                    pass
            print("  → 표시를 제거했습니다.")
        else:
            print("  → 'doctor.py --unblock' 으로 제거할 수 있습니다.")
    print("-" * 60)

    failed_required, blocked, ok = [], [], set()
    for mod, pip_name, required in PACKAGES:
        try:
            m = importlib.import_module(mod)
            print(f"[OK]   {pip_name:<14} {getattr(m, '__version__', '')}")
            ok.add(pip_name)
        except Exception as e:  # ImportError, OSError(DLL 차단) 등
            msg = str(e).replace("\n", " ")
            is_block = any(h in msg.lower() for h in BLOCK_HINTS)
            tag = "[차단]" if is_block else "[실패]" if required else "[없음]"
            print(f"{tag} {pip_name:<14} {msg[:160]}")
            if is_block:
                blocked.append(pip_name)
            if required:
                failed_required.append(pip_name)
    print("=" * 60)

    if blocked:
        print(f"스마트 앱 컨트롤이 차단한 패키지: {', '.join(blocked)}")
        print("  README 의 '스마트 앱 컨트롤' 절을 참고하세요.")
    if failed_required:
        print(f"필수 패키지 문제: {', '.join(failed_required)}")
        return 1
    if "lightgbm" not in ok:
        print("LightGBM 없음 → 예측 모델은 scikit-learn 엔진으로 자동 대체됩니다 (정상).")
    print("모든 필수 패키지 정상입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
