"""같은 소식을 다룬 기사 묶기 (제목 유사도)."""
from __future__ import annotations

import re

# [속보], (종합), <사진> 같은 괄호 부분
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>")
# 한글·영문·숫자가 아닌 글자
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]")
# 속보·종합·2보처럼 여러 번 갱신되는 기사는 중요한 소식인 경우가 많다
_IMPORTANT = re.compile(r"\[속보\]|\[긴급\]|\(종합\d*보?\)|\(\d보\)|\(종합\)")

# 제목 유사도가 이 값 이상이면 같은 소식 후보
THRESHOLD = 0.4
# 같은 수치(5.5%, 819억원 …)가 들어 있으면 이 정도만 비슷해도 같은 소식으로 본다
NUMBER_THRESHOLD = 0.25
# 수치 표현 (5.5%, 819억, 300% …)
_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|억|조|만|원|달러|bp|배)")
# 뉴스성이 낮은 단신 (요약 후보에서 뺀다)
_MINOR = re.compile(r"^\s*\[(?:게시판|인사|부고|포토|사진|표|그래픽|영상|신간|알림|행사|시승기|모멘트|오늘의)[^\]]*\]|해외 뉴스 영상")


def _norm(title: str) -> str:
    """비교용 제목: 괄호 내용과 기호·공백을 뺀다."""
    return _NON_WORD.sub("", _BRACKETS.sub(" ", title))


def _bigrams(s: str) -> set[str]:
    """두 글자씩 자른 조각 집합 (유사도 계산용)."""
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _subject(title: str) -> str:
    """제목 첫 단어의 앞 두 글자 (주어: 회사·기관 이름 등)."""
    words = _BRACKETS.sub(" ", title).split()
    for w in words:
        w = _NON_WORD.sub("", w)
        if len(w) >= 2:
            return w[:2]
    return ""


def similarity(a: str, b: str) -> float:
    """두 제목의 유사도 (0~1, 두 글자 조각 겹침 비율)."""
    A, B = _bigrams(_norm(a)), _bigrams(_norm(b))
    if not A or not B:
        return 0.0
    return 2 * len(A & B) / (len(A) + len(B))


def _numbers(title: str) -> set[str]:
    """제목에 나온 수치 (5.5%, 819억 등)."""
    return {re.sub(r"\s", "", m) for m in _NUMBER.findall(title)}


def is_minor(title: str) -> bool:
    """게시판·인사·부고처럼 뉴스성이 낮은 단신인지."""
    return bool(_MINOR.search(title))


def same_story(a: str, b: str) -> bool:
    """제목이 비슷하고, 한쪽 제목의 주어가 다른 쪽 제목에 들어 있으면 같은 소식으로 본다.
    같은 수치가 함께 나오면 표현이 달라도 같은 소식으로 본다."""
    sim = similarity(a, b)
    shared = _numbers(a) & _numbers(b)
    # 5.5%, 819억원처럼 구체적인 수치가 같으면 기준을 더 낮춘다 (2%처럼 흔한 수치는 덜 믿는다)
    specific = any("." in n or sum(ch.isdigit() for ch in n) >= 3 for n in shared)
    if shared and sim >= (NUMBER_THRESHOLD - 0.05 if specific else NUMBER_THRESHOLD + 0.05):
        return True
    if sim < THRESHOLD:
        return False
    sa, sb = _subject(a), _subject(b)
    na, nb = _norm(a), _norm(b)
    return bool((sa and sa in nb) or (sb and sb in na))


def importance(title: str) -> int:
    """속보·종합 기사면 1, 아니면 0 (중요도 가산점)."""
    return 1 if _IMPORTANT.search(title) else 0


def cluster(articles: list[dict]) -> list[list[dict]]:
    """시간순 기사 목록을 같은 소식끼리 묶는다. 각 묶음의 첫 기사(대표)와만 비교해 엉뚱하게 번지지 않게 한다."""
    groups: list[list[dict]] = []
    for a in articles:
        for g in groups:
            if same_story(a["title"], g[0]["title"]):
                g.append(a)
                break
        else:
            groups.append([a])
    return groups


def assign_stories(articles: list[dict]) -> list[dict]:
    """테마 안에서 같은 소식끼리 묶어 각 기사에 story(대표 기사 id)와 story_size를 붙인다."""
    by_topic: dict[str, list[dict]] = {}
    for a in articles:
        by_topic.setdefault(a["topic"], []).append(a)
    for items in by_topic.values():
        for g in cluster(sorted(items, key=lambda a: (a["published_at"], a["id"]))):
            for a in g:
                a["story"] = g[0]["id"]
                a["story_size"] = len(g)
    return articles
