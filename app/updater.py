"""사이드바 '🔄 지금 업데이트': update 를 별도 프로세스로 실행하고 진행 상황을 3초마다 보여준다."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

import mobile
from common import now_kst, refresh
from stocklab import background as bg
from stocklab import runlock
from stocklab.realtime import market_open


def _dur(sec: float) -> str:
    sec = int(max(sec, 0))
    return f"{sec // 60}분 {sec % 60:02d}초" if sec >= 60 else f"{sec}초"


def _finish() -> None:
    """실행이 끝났다: 캐시를 비우고 화면 전체를 새 데이터로 다시 그린다 (한 번만)."""
    st.session_state.pop("upd_watch", None)
    r = bg.last_result()
    if r and r["outcome"] in ("ok", "partial"):
        st.session_state["flash"] = "최신 데이터로 업데이트했습니다." + (
            " 일부 종목은 받지 못했습니다 — 사이드바 결과를 확인하세요." if r["outcome"] == "partial" else "")
    refresh()
    st.rerun()


@st.fragment(run_every=3)
def _running() -> None:
    s = bg.status()
    if not s["running"]:
        _finish()
        return
    elapsed = _dur((datetime.now() - s["started"]).total_seconds())
    if not s["ours"]:
        st.info(f"🔄 다른 곳에서 업데이트 실행 중 (update.bat 또는 예약 작업) · 경과 {elapsed}\n\n"
                "끝나면 자동으로 새 데이터를 보여줍니다.")
        return
    text = bg.read_log()
    p = bg.progress(text)
    count = f" {p['count'][0]}/{p['count'][1]}종목" if p["count"] else ""
    st.progress(p["frac"], text=f"🔄 {p['step']}{count}")
    st.caption(f"경과 {elapsed} · 그동안 대시보드는 계속 사용할 수 있습니다"
               + (f" · 경고 {p['warnings']}건" if p["warnings"] else ""))
    with st.expander("로그 보기"):
        st.code(bg.tail(text) or "시작 중…", language=None)


def _idle() -> None:
    if st.button("🔄 지금 업데이트", width="stretch", key="upd_start",
                 help="update.bat 과 같은 작업(주가·수급·거시지표·뉴스·공시 → 예측·추천)을 지금 실행합니다. "
                      "300종목 기준 약 5분, 첫 실행은 약 25분"):
        try:
            bg.start()
            st.session_state["upd_watch"] = True
        except runlock.AlreadyRunning as e:
            st.session_state["flash"] = str(e)
        except OSError as e:
            st.session_state["flash"] = f"업데이트를 시작하지 못했습니다: {e}"
        st.rerun()
    if market_open(now_kst()):
        st.caption("장중에는 오늘 일봉이 잠정값입니다 — 저녁 update 때 확정값으로 덮어씁니다.")
    r = bg.last_result()
    if not r or not r.get("finished") or (datetime.now() - r["finished"]).total_seconds() > 12 * 3600:
        return
    took = _dur((r["finished"] - r["started"]).total_seconds()) if r["started"] else ""
    when = f"{r['finished']:%H:%M} · {took}"
    if r["outcome"] == "ok":
        st.caption(f"✅ 지난 업데이트 완료 ({when})")
    elif r["outcome"] == "partial":
        st.caption(f"⚠️ 지난 업데이트: {r['message']} ({when})")
    else:
        st.error(f"지난 업데이트 실패 ({when}): {r['message']}")
        with st.expander("로그 보기"):
            st.code(bg.tail(bg.read_log(), 20), language=None)


def panel(demo: bool) -> None:
    """데모 데이터·보기 전용(비밀번호 없는 휴대폰)에서는 표시하지 않는다."""
    if demo or mobile.readonly():
        return
    if bg.status()["running"]:
        st.session_state["upd_watch"] = True
        _running()
    elif st.session_state.get("upd_watch"):
        _finish()              # 화면을 옮기는 사이에 끝난 경우
    else:
        _idle()
