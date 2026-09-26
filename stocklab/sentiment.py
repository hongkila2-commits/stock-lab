"""뉴스 제목 감성 점수 (-1 부정 ~ +1 긍정).

기본은 가벼운 금융 용어 사전 방식. 설정에서 engine: finbert 로 바꾸면
KR-FinBERT(snunlp/KR-FinBert-SC) 를 쓴다 (pip install transformers torch 필요).
"""
from __future__ import annotations

import re

POSITIVE = [
    "상승", "급등", "강세", "반등", "신고가", "최고가", "돌파", "호실적", "어닝서프라이즈",
    "흑자", "흑자전환", "증가", "개선", "성장", "확대", "수주", "공급계약", "계약 체결",
    "최대", "상향", "목표가 상향", "매수", "순매수", "호재", "기대", "수혜", "승인",
    "자사주 매입", "자사주 소각", "배당 확대", "특허", "양산", "턴어라운드", "회복",
]
NEGATIVE = [
    "하락", "급락", "약세", "폭락", "신저가", "적자", "적자전환", "손실", "감소", "부진",
    "악화", "축소", "하향", "목표가 하향", "매도", "순매도", "악재", "우려", "쇼크",
    "어닝쇼크", "소송", "리콜", "유상증자", "전환사채", "횡령", "배임", "제재", "과징금",
    "압수수색", "상장폐지", "거래정지", "관리종목", "감자", "파업", "중단", "철회", "지연",
]
_TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    import html
    return html.unescape(_TAG.sub("", text or "")).strip()


def lexicon_score(text: str) -> float:
    t = clean(text)
    # 긴 표현을 먼저 세고 지워서 '흑자전환' 이 '흑자' 로 중복 집계되지 않게 한다
    pos = neg = 0
    for words, sign in ((sorted(POSITIVE, key=len, reverse=True), 1),
                        (sorted(NEGATIVE, key=len, reverse=True), -1)):
        for w in words:
            c = t.count(w)
            if c:
                t = t.replace(w, " ")
                if sign > 0:
                    pos += c
                else:
                    neg += c
    return 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)


class Scorer:
    def __init__(self, engine: str = "lexicon"):
        self.engine = engine
        self._pipe = None
        if engine == "finbert":
            from transformers import pipeline
            self._pipe = pipeline("text-classification", model="snunlp/KR-FinBert-SC",
                                  truncation=True)

    def score(self, texts: list[str]) -> list[float]:
        if self._pipe is None:
            return [lexicon_score(t) for t in texts]
        out = self._pipe([clean(t) for t in texts], batch_size=16)
        sign = {"positive": 1.0, "negative": -1.0}
        return [sign.get(o["label"].lower(), 0.0) * o["score"] for o in out]
