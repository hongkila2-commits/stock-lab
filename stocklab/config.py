"""설정 로딩: .env(비밀키) + config/settings.yaml(종목·모델 설정)."""
from __future__ import annotations

import os
import re
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"
LOG_DIR = ROOT / "logs"

load_dotenv(ROOT / ".env")


def _codes(d: dict | None) -> dict[str, str]:
    # YAML 에서 따옴표 없이 적은 005930 은 정수 5930 이 되므로 6자리로 복원한다.
    return {str(k).zfill(6): str(v) for k, v in (d or {}).items()}


@dataclass
class Settings:
    watchlist: dict[str, str]
    universe: dict[str, str]
    collect: dict = field(default_factory=dict)
    screener: dict = field(default_factory=dict)
    model: dict = field(default_factory=dict)
    sentiment: dict = field(default_factory=dict)
    universe_auto: dict = field(default_factory=dict)
    picks: dict = field(default_factory=dict)
    alerts: dict = field(default_factory=dict)
    realtime: dict = field(default_factory=dict)

    @property
    def all_stocks(self) -> dict[str, str]:
        return {**self.universe, **self.watchlist}

    @property
    def horizons(self) -> list[int]:
        """예측 기간들(거래일). 첫 번째가 기본(실시간 대상·신호 변화 알림 기준)."""
        hs = self.model.get("horizons") or [self.model["horizon"]]
        return list(dict.fromkeys(int(h) for h in hs))


def horizon_label(h: int) -> str:
    return {5: "1주", 10: "2주", 20: "1개월", 60: "3개월"}.get(int(h), f"{h}거래일")


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config" / "settings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(
        watchlist=_codes(raw.get("watchlist")),
        universe=_codes(raw.get("universe")),
        collect={"history_years": 3, "news_per_stock": 100, "dart_lookback_days": 365,
                 **(raw.get("collect") or {})},
        screener={"auto_add_top": 10, **(raw.get("screener") or {})},
        model={"horizon": 5, "horizons": [5, 20], "target": "excess", "bullish": 0.55, "bearish": 0.45,
               "retrain_every_days": 7, **(raw.get("model") or {})},
        sentiment={"engine": "lexicon", **(raw.get("sentiment") or {})},
        universe_auto={"top_market_cap": 300, **(raw.get("universe_auto") or {})},
        picks={"count": 30, "min_value_eok": 10, **(raw.get("picks") or {})},
        alerts={"kakao": True, "daily_summary": True, "move_pct": 3.0, "cooldown_min": 60,
                **(raw.get("alerts") or {})},
        realtime={"max_stocks": 40, "picks_top": 15, "poll_seconds": 30,
                  **(raw.get("realtime") or {})},
    )


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def db_path() -> Path:
    """STOCKLAB_DB 환경변수로 데모 DB 등 다른 파일을 가리킬 수 있다."""
    p = env("STOCKLAB_DB")
    if p:
        p = Path(p)
        return p if p.is_absolute() else ROOT / p
    return DATA_DIR / "market.sqlite"


def model_dir() -> Path:
    """데모 DB 로 학습한 모델이 실제 모델을 덮어쓰지 않도록 DB 별로 폴더를 나눈다."""
    p = db_path()
    return MODEL_DIR if p.name == "market.sqlite" else MODEL_DIR / p.stem


_KEY = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


def sync_env(env_path: Path | None = None, example_path: Path | None = None) -> list[str]:
    """.env.example 에 새로 생긴 항목을 .env 끝에 빈 값으로 추가한다 (기존 값은 건드리지 않음).

    .env 는 비밀키가 있어 GitHub 로 주고받지 않으므로, git pull 로 새 설정 항목이 생겨도
    .env 에는 자동으로 나타나지 않는다. update·check·setup 때 이 함수로 채운다.
    """
    env_path = env_path or ROOT / ".env"
    example_path = example_path or ROOT / ".env.example"
    if not env_path.exists() or not example_path.exists():
        return []
    have = {m.group(1) for line in env_path.read_text(encoding="utf-8-sig").splitlines()
            if (m := _KEY.match(line))}
    blocks, comments, added = [], [], []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        m = _KEY.match(line)
        if m:
            if m.group(1) not in have:
                blocks.append("\n".join(comments + [f"{m.group(1)}="]))
                added.append(m.group(1))
            comments = []
        elif line.strip().startswith("#"):
            comments.append(line)
        else:
            comments = []
    if blocks:
        text = env_path.read_text(encoding="utf-8-sig")
        sep = "" if text.endswith("\n") or not text else "\n"
        with env_path.open("a", encoding="utf-8") as fp:
            fp.write(f"{sep}\n# ── {date.today()} 새로 생긴 설정 항목 (자동 추가, 필요한 것만 값을 채우세요) ──\n")
            fp.write("\n".join(blocks) + "\n")
    return added
