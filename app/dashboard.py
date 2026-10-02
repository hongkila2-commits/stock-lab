"""StockLab 대시보드.  실행: dashboard.bat  (또는 streamlit run app/dashboard.py)"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="StockLab", page_icon="📈", layout="wide")
sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPT = Path(__file__).resolve()
PROJECT = SCRIPT.parent.parent


def _project_modules() -> dict[str, float]:
    """메모리에 올라온 이 프로젝트의 모듈(stocklab·app) → 지금 파일 수정 시각."""
    out = {}
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        path = Path(f).resolve()
        if name == "__main__" or path == SCRIPT:       # 이 화면 자체는 Streamlit 이 관리 (건드리면 멈춤)
            continue
        if PROJECT in path.parents and ".venv" not in path.parts:
            try:
                out[name] = path.stat().st_mtime
            except OSError:
                pass
    return out


def _drop_changed_code() -> None:
    """git pull 로 코드가 바뀌었으면 메모리의 옛 모듈을 버린다 → 아래 import 가 새 파일을 읽는다.

    Streamlit 은 이 화면(dashboard.py)만 매번 새로 읽고, stocklab/ 같은 다른 모듈은 처음 읽은 것을 계속 쓴다.
    그래서 대시보드를 켠 채 git pull 하면 새 화면 코드가 옛 stocklab 을 불러 ImportError 가 났다.
    하나라도 바뀌었으면 서로 맞물려 있으므로 프로젝트 모듈을 전부 버린다.
    """
    first = "_stocklab_mtimes" not in sys.__dict__     # 이 검사가 없던 예전 코드가 모듈을 읽어 둔 경우도 포함
    seen = sys.__dict__.setdefault("_stocklab_mtimes", {})
    now = _project_modules()
    if (first and now) or any(name in seen and seen[name] != t for name, t in now.items()):
        for name in now:
            sys.modules.pop(name, None)
        seen.clear()
        st.cache_data.clear()


def _remember_code() -> None:
    seen = sys.__dict__.setdefault("_stocklab_mtimes", {})
    seen.update({k: v for k, v in _project_modules().items() if k not in seen})


_drop_changed_code()

from common import (DOWN, H, PAGES, ROOT, S, TARGET_LABEL, UP, listing, my_watchlist, names,  # noqa: E402
                    now_kst, open_stock, prices, q, refresh, rt_alive, rt_status, stats)
from stocklab.config import db_path, horizon_label, reload_env  # noqa: E402
from views import detail, flows, macro, model, sectors, watch  # noqa: E402
import mobile  # noqa: E402
import updater  # noqa: E402
from stocklab import netinfo  # noqa: E402
_remember_code()

# .env 를 고쳤으면 대시보드를 다시 켜지 않아도 새 키를 쓴다
reload_env()

# 휴대폰 등 다른 기기에서 접속하면 비밀번호 확인 (PC 자신은 통과)
mobile.gate()
mobile.compact_default()

# 자동 갱신 영역(fragment)에서 누른 종목으로 이동 — 위젯을 그리기 전에 처리해야 한다
if "goto" in st.session_state:
    open_stock(st.session_state.pop("goto"))

# PC 화면에서만 사이드바를 조금 넓혀 전체 종목 표가 잘리지 않게 (휴대폰에선 기본 접이식 그대로)
st.markdown("<style>@media (min-width: 900px){section[data-testid='stSidebar']{min-width:400px;}}</style>",
            unsafe_allow_html=True)

meta = q("SELECT key, value FROM meta")
META = dict(zip(meta["key"], meta["value"])) if not meta.empty else {}
DEMO = META.get("demo") == "1"


def search_options() -> list[str]:
    """검색 대상: 전체 목록(시총 큰 순) + 목록에 없지만 시세가 있는 종목."""
    li = listing().sort_values("market_cap", ascending=False)
    codes = list(li.index)
    p = prices()
    if not p.empty:
        codes += [c for c in p["code"].unique() if c not in li.index]
    return codes


def search_label(code: str) -> str:
    li, s = listing(), stats()
    name = names().get(code, code)
    if code in s.index:
        price, chg = s.at[code, "close"], s.at[code, "r1"] * 100
    elif code in li.index:
        price, chg = li.at[code, "close"], li.at[code, "change_pct"]
    else:
        return f"{name} ({code})"
    market = li.at[code, "market"] if code in li.index else ""
    arrow = "▲" if chg > 0 else "▼" if chg < 0 else "-"
    return f"{name} · {market} · {price:,.0f} {arrow}{abs(chg):.1f}%"


def _on_search():
    code = st.session_state.get("search")
    if code:
        open_stock(code)


def market_list() -> None:
    """사이드바: 전체 종목 시세 (시장·이름 필터, 행 클릭 → 상세)."""
    li = listing()
    if li.empty:
        st.caption("전체 종목 목록 없음 — update 실행 후 표시")
        return
    mk = st.radio("시장", ["전체", "KOSPI", "KOSDAQ"], horizontal=True, key="mk",
                  label_visibility="collapsed")
    text = st.text_input("이름 필터", key="flt", placeholder="이름 일부 (예: 전자)",
                         label_visibility="collapsed")
    df = li if mk == "전체" else li[li["market"] == mk]
    if text:
        df = df[df["name"].str.contains(text, case=False, regex=False)]
    df = df.sort_values("market_cap", ascending=False).head(3000)
    view = pd.DataFrame({"종목": df["name"].to_numpy(), "현재가": df["close"].to_numpy(),
                         "등락%": df["change_pct"].to_numpy()})
    codes = df.index.tolist()
    styled = (view.style.map(lambda v: f"color: {UP}" if v > 0 else f"color: {DOWN}" if v < 0 else "",
                             subset=["등락%"])
              .format({"현재가": "{:,.0f}", "등락%": "{:+.2f}"}, na_rep="-"))

    def go():
        rows = st.session_state["t_market"].selection.rows
        if rows:
            open_stock(codes[rows[0]])

    st.dataframe(styled, hide_index=True, height=420, width="stretch", on_select=go,
                 selection_mode="single-row", key="t_market", column_config={
                     "종목": st.column_config.TextColumn(width=130),
                     "현재가": st.column_config.Column(width=78),
                     "등락%": st.column_config.Column(width=58)})
    st.caption(f"{len(df):,}종목 · {li['asof'].max()} 기준 · {li['source'].iloc[0]}"
               + (" (전 거래일 종가)" if li["source"].iloc[0] == "공공데이터포털" else ""))


def snapshot_button() -> None:
    """realtime.bat 이 꺼져 있을 때 관심종목 현재가를 한 번 받아오기 (평일 09:00 이후, 편집 가능한 사용자만)."""
    t = now_kst()
    if DEMO or mobile.readonly() or rt_alive() or t.weekday() >= 5 or t.hour < 9:
        return
    codes = my_watchlist()[:20]
    if not codes:
        return
    if st.button(f"💹 현재가 받기 ({len(codes)}종목)", width="stretch",
                 help="실시간(realtime.bat)이 꺼져 있을 때, 내 관심종목의 지금 가격을 한국투자증권에서 한 번 받아옵니다"):
        with st.spinner(f"관심종목 {len(codes)}개 현재가 조회 중… (약 {len(codes) * 0.6:.0f}초)"):
            try:
                from stocklab import db as _db
                from stocklab.cli import _kis
                from stocklab.realtime import snapshot
                conn = _db.connect()
                try:
                    n = snapshot(conn, _kis(), codes)
                finally:
                    conn.close()
                st.session_state["flash"] = f"{n}종목 현재가를 받았습니다 ({now_kst():%H:%M:%S})."
            except Exception as e:
                st.session_state["flash"] = f"현재가를 받지 못했습니다: {e}"
        refresh()
        st.rerun()


@st.fragment(run_every=10)
def rt_badge() -> None:
    """실시간 상태: realtime.bat 이 30초 안에 신호를 남겼으면 '연결됨'."""
    m = rt_status()
    hb = pd.to_datetime(m.get("rt_heartbeat"), errors="coerce")
    alive = pd.notna(hb) and (now_kst() - hb).total_seconds() < 90
    mode = m.get("rt_mode", "off")
    if mode == "demo":
        st.caption("📡 실시간: 데모 (가상 장중 데이터)")
    elif alive and mode in ("websocket", "poll"):
        how = "실시간 연결" if mode == "websocket" else f"{S.realtime['poll_seconds']}초 조회"
        st.caption(f"🟢 {how} · {m.get('rt_count', '?')}종목 · {hb:%H:%M:%S}")
    else:
        last = f" (마지막 {hb:%m/%d %H:%M})" if pd.notna(hb) else ""
        st.caption(f"⚪ 실시간 꺼짐{last} — 장중에 `realtime.bat` 실행")


def empty_help() -> None:
    """데이터가 없을 때: 무엇을 읽고 있는지, 무엇이 비었는지, 최근 오류는 무엇인지 보여준다."""
    path = db_path()
    st.warning("표시할 데이터가 없습니다.")
    st.markdown(f"**읽고 있는 파일**: `{path}`")
    if not path.exists():
        st.markdown("→ 파일이 아직 없습니다. 수집을 한 번도 실행하지 않았습니다.")
    else:
        counts = {t: q(f"SELECT COUNT(*) AS n FROM {t}")["n"].iloc[0]
                  for t in ("listing", "prices", "flows", "macro", "news", "predictions")}
        st.markdown("**저장된 행 수**: " + " · ".join(f"{k} {v:,}" for k, v in counts.items()))
    log_file = ROOT / "logs" / "stocklab.log"
    if log_file.exists():
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        bad = [ln for ln in lines if " ERROR " in ln or " WARNING " in ln][-12:]
        if bad:
            st.markdown("**최근 오류·경고** (`logs/stocklab.log`)")
            st.code("\n".join(bad), language=None)
    st.markdown(
        "**해결 방법**\n"
        "1. 실제 데이터: `check.bat` 으로 연결이 `[OK]` 인지 확인 → `update.bat` 실행 "
        "(첫 실행 약 25분, 창에 `완료` 가 나올 때까지 닫지 마세요) → 여기서 **새로고침**\n"
        "2. 가상 데이터로 화면만 보기: 이 창을 닫고 `demo.bat` 실행\n\n"
        "`update.bat` 과 `dashboard.bat` 은 서로 다른 창입니다. 수집이 끝난 뒤 사이드바의 **새로고침**을 누르세요.")
    st.stop()


# ── 사이드바 ─────────────────────────────────────────────
with st.sidebar:
    st.title("StockLab")
    if DEMO:
        st.warning("**데모 모드** — 가상 데이터입니다.")
    st.radio("메뉴", PAGES, key="page", label_visibility="collapsed")
    rt_badge()
    st.toggle("📱 간단히 보기", key="compact", help="휴대폰처럼 좁은 화면용: 표의 열을 줄이고 차트를 작게")
    if mobile.readonly():
        st.caption("👀 보기 전용 (PC 의 .env 에 DASHBOARD_PASSWORD 를 설정하면 추가·해제 가능)")
    st.divider()

    st.markdown("**종목 검색**")
    opts = search_options()
    st.selectbox("종목 검색", opts, index=None, key="search", format_func=search_label,
                 placeholder=f"이름 또는 코드 ({len(opts):,}종목)", on_change=_on_search,
                 label_visibility="collapsed")
    with st.expander(f"전체 종목 시세", expanded=False):
        market_list()
    st.divider()

    if netinfo.is_local_client(st.context.ip_address):
        mobile.phone_panel()

    c1, c2 = st.columns([1, 1])
    if c1.button("새로고침", width="stretch",
                 help="저장된 값을 다시 읽습니다. 새 데이터 받기는 아래 🔄 지금 업데이트, "
                      "장중 현재가는 realtime.bat(자동) 또는 💹 현재가 받기"):
        refresh()
        st.rerun()
    c2.caption(f"업데이트\n{META.get('last_update', '없음').replace('T', ' ')}")
    updater.panel(DEMO)
    snapshot_button()
    st.caption(f"예측: {'·'.join(horizon_label(h) for h in S.horizons)} 뒤 {TARGET_LABEL} · "
               f"강세 ≥ {S.model['bullish']:.2f} · "
               f"약세 ≤ {S.model['bearish']:.2f}\n\n⚠️ 참고용 통계 모델입니다. 투자 판단과 책임은 본인에게 있습니다.")

# ── 본문 ────────────────────────────────────────────────
flash = st.session_state.pop("flash", None)
if flash:
    st.success(flash)
if prices().empty and listing().empty:
    empty_help()

page = st.session_state.get("page", PAGES[0])
if mobile.compact():
    # 휴대폰: 사이드바를 열지 않아도 되게 본문 맨 위에 메뉴 줄
    def _nav():
        st.session_state["page"] = st.session_state.get("nav_m") or page
    st.session_state["nav_m"] = page
    st.pills("메뉴", PAGES, key="nav_m", on_change=_nav, label_visibility="collapsed")
if page == "관심종목":
    watch.render()
elif page == "종목 상세":
    detail.render(DEMO)
elif page == "업종 수급":
    sectors.render()
elif page == "외국인 수급":
    flows.render()
elif page == "예측 모델":
    model.render()
else:
    macro.render()
