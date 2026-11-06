"""주제와 추천 키워드, 키워드 기반 주제 분류."""
from __future__ import annotations

# 화면의 주제 버튼과 추천 키워드
TOPICS: dict[str, list[str]] = {
    "경제": ["금리", "환율", "물가", "한국은행", "GDP"],
    "증시·투자": ["코스피", "나스닥", "ETF", "비트코인", "공모주"],
    "부동산": ["아파트", "전세", "청약", "대출규제", "재건축"],
    "산업·기업": ["반도체", "배터리", "자동차", "실적", "M&A"],
    "IT·과학": ["AI", "스마트폰", "플랫폼", "우주", "보안"],
    "정치": ["국회", "대통령실", "선거", "정당", "법안"],
    "국제": ["미국", "중국", "일본", "유럽", "중동"],
}

# 분류용 단어 (추천 키워드 + 보조 단어)
_CLASSIFY_WORDS: dict[str, list[str]] = {
    "경제": TOPICS["경제"] + ["기준금리", "소비자물가", "수출", "무역수지", "기재부", "경기", "고용", "세수"],
    "증시·투자": TOPICS["증시·투자"] + ["코스닥", "증시", "주가", "외국인 순매수", "선물", "상장", "펀드", "다우", "S&P"],
    "부동산": TOPICS["부동산"] + ["집값", "분양", "주택", "임대", "재개발", "LTV", "DSR"],
    "산업·기업": TOPICS["산업·기업"] + ["삼성전자", "SK하이닉스", "현대차", "조선", "철강", "HBM", "2차전지", "영업이익"],
    "IT·과학": TOPICS["IT·과학"] + ["인공지능", "반도체 설계", "클라우드", "통신", "앱", "누리호", "해킹", "데이터센터"],
    "정치": TOPICS["정치"] + ["여당", "야당", "의원", "장관", "국무회의", "탄핵", "총리"],
    "국제": TOPICS["국제"] + ["트럼프", "연준", "백악관", "러시아", "우크라이나", "이스라엘", "EU", "유엔"],
}


def classify(text: str, default: str | None = None) -> str:
    """제목·요약 텍스트에서 가장 많이 걸리는 주제를 고른다. 아무것도 없으면 default."""
    best, best_score = None, 0
    for topic, words in _CLASSIFY_WORDS.items():
        score = sum(text.count(w) for w in words)
        if score > best_score:
            best, best_score = topic, score
    if best is None:
        return default or "경제"
    # 피드가 지정한 주제도 맞는 단어가 있으면 피드 주제를 우선한다
    if default and default in _CLASSIFY_WORDS:
        default_score = sum(text.count(w) for w in _CLASSIFY_WORDS[default])
        if default_score and default_score >= best_score:
            return default
    return best
