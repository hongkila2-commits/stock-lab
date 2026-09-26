"""휴대폰 접속: 비밀번호 확인 · 간단히 보기 · 접속 주소 QR."""
from __future__ import annotations

import streamlit as st

from stocklab import netinfo
from stocklab.config import env

MAX_TRIES = 5


def gate() -> None:
    """PC 자신이면 통과. 다른 기기면 비밀번호(설정돼 있을 때) 또는 보기 전용."""
    ip = st.context.ip_address
    if netinfo.is_local_client(ip):
        st.session_state["readonly"] = False
        return
    if not env("DASHBOARD_PASSWORD"):
        st.session_state["readonly"] = True       # 비밀번호가 없으면 보기만 (추가·삭제 불가)
        return
    if st.session_state.get("authed"):
        st.session_state["readonly"] = False
        return
    st.title("StockLab")
    tries = st.session_state.get("tries", 0)
    if tries >= MAX_TRIES:
        st.error("비밀번호를 여러 번 틀렸습니다. 브라우저를 닫았다가 다시 여세요.")
        st.stop()
    with st.form("login"):
        pw = st.text_input("접속 비밀번호", type="password")
        ok = st.form_submit_button("들어가기", type="primary", width="stretch")
    if ok:
        if netinfo.password_ok(pw):
            st.session_state["authed"] = True
            st.session_state["readonly"] = False
            st.rerun()
        st.session_state["tries"] = tries + 1
        st.error(f"비밀번호가 맞지 않습니다 ({tries + 1}/{MAX_TRIES})")
    st.caption("PC 의 .env 파일에 적은 DASHBOARD_PASSWORD 를 입력하세요.")
    st.stop()


def compact_default() -> None:
    """휴대폰 브라우저면 '간단히 보기'를 기본으로 (처음 한 번만 정하고, 이후는 토글 값을 따름)."""
    if "compact" not in st.session_state:
        st.session_state["compact"] = netinfo.is_mobile(st.context.headers.get("User-Agent"))


def compact() -> bool:
    return bool(st.session_state.get("compact"))


def readonly() -> bool:
    return bool(st.session_state.get("readonly"))


@st.cache_data(ttl=60)
def _qr_svg(url: str) -> str:
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")


def phone_panel() -> None:
    """사이드바: 휴대폰에서 열 주소와 QR 코드."""
    with st.expander("📱 휴대폰으로 보기"):
        found = netinfo.urls()
        if not found:
            st.caption("네트워크 주소를 찾지 못했습니다. 와이파이 연결을 확인하세요.")
        has_ts = any("Tailscale" in label for label, _ in found)
        for label, url in found:
            st.markdown(f"**{label}**  \n`{url}`")
            try:
                st.markdown(f"<div style='background:#fff;padding:6px;width:fit-content'>{_qr_svg(url)}</div>",
                            unsafe_allow_html=True)
            except ImportError:
                st.caption("QR 표시는 setup.bat 을 다시 실행하면 나옵니다 (qrcode 패키지).")
        if not has_ts:
            st.info("집 밖에서도 보려면 PC 와 휴대폰에 **Tailscale** 을 설치하고 같은 계정으로 로그인하세요 "
                    "(README '휴대폰으로 보기').")
        if not env("DASHBOARD_PASSWORD"):
            st.warning("`.env` 에 **DASHBOARD_PASSWORD** 가 없어 휴대폰에서는 보기만 가능합니다 "
                       "(관심종목 추가·해제 불가). 설정을 권장합니다.")
        st.caption("휴대폰에서 안 열리면 PC 에서 `mobile_setup.bat` 을 관리자 권한으로 한 번 실행하세요 (방화벽 허용).")
