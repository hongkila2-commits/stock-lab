"""git pull 뒤 .env 에 새 설정 항목 자동 추가."""
from stocklab.config import sync_env

EXAMPLE = """# 한국투자증권
KIS_APP_KEY=
KIS_ENV=paper

# 휴대폰 접속 비밀번호
# 비워 두면 보기 전용
DASHBOARD_PASSWORD=
DASHBOARD_URL=
"""


def test_adds_only_missing_keys_and_keeps_values(tmp_path):
    ex, env = tmp_path / ".env.example", tmp_path / ".env"
    ex.write_text(EXAMPLE, encoding="utf-8")
    env.write_bytes("﻿KIS_APP_KEY=내키123\nKIS_ENV=paper".encode("utf-8"))   # BOM·끝 줄바꿈 없음
    assert sync_env(env, ex) == ["DASHBOARD_PASSWORD", "DASHBOARD_URL"]
    text = env.read_text(encoding="utf-8-sig")
    assert text.startswith("KIS_APP_KEY=내키123\nKIS_ENV=paper\n")
    assert "# 비워 두면 보기 전용\nDASHBOARD_PASSWORD=\n" in text      # 설명 주석도 함께
    assert sync_env(env, ex) == []                                   # 두 번째는 아무것도 안 함
    assert text.count("DASHBOARD_PASSWORD=") == 1


def test_missing_files_are_ignored(tmp_path):
    assert sync_env(tmp_path / "none", tmp_path / "none2") == []
