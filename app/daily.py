"""하루 뉴스 정리 ('요약' 탭).

하루가 끝나면(자정 이후 첫 실행) 그날의 시간별 요약을 모아
  1) 중요한 기사 5~10개를 고르고
  2) 기사마다 (긍정)(중립)(부정)을 판단하고
  3) 그날 뉴스 상황을 몇 문장으로 정리해 저장한다.

API 키가 있으면 Claude가 판단하고, 없으면 규칙(보도량·단어 사전)으로 대신한다.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel

from . import claude, db, similar
from .topics import TOPICS

# 로그 기록용
log = logging.getLogger(__name__)

# 하루 정리에 넣을 중요 기사 수 (최소~최대)
MIN_ITEMS = 5
MAX_ITEMS = 10

# 기사 분위기 표시값. 화면 목록 앞에 (긍정)(중립)(부정)으로 붙는다.
SENTIMENTS = ("긍정", "중립", "부정")

# 기본 모드(키 없음)에서 분위기를 판단하는 단어 사전
# 제목·요약에 긍정 단어가 더 많으면 '긍정', 부정 단어가 더 많으면 '부정', 같으면 '중립'
POSITIVE_WORDS = [
    "상승", "급등", "반등", "증가", "호조", "최고", "흑자", "개선", "회복", "타결",
    "합의", "수주", "성장", "상향", "돌파", "강세", "확대", "지원", "인상",
]
NEGATIVE_WORDS = [
    "하락", "급락", "폭락", "감소", "부진", "우려", "공포", "붕괴", "적자", "손실",
    "사고", "화재", "사망", "위기", "논란", "제재", "관세", "약세", "하향", "중단",
    "투매", "사이드카", "서킷브레이커", "실각", "사임", "규제", "추락", "증발",
]

# 하루가 끝나지 않은 날(오늘)은 정리하지 않는다. 밀린 날은 최근 7일까지 다시 확인한다.
CATCH_UP_DAYS = 7


# ---------- Claude 응답 형식 ----------

class DailyItem(BaseModel):
    """Claude가 고른 중요 기사 한 건."""
    sentiment: Literal["긍정", "중립", "부정"]  # 분위기 판단
    topic: str                                   # 테마 (경제, 증시·투자 …)
    title: str                                   # 제목
    summary: str                                 # 2~3문장 요약
    summary_ids: list[int]                       # 근거로 쓴 시간별 요약 번호


class DailyResult(BaseModel):
    """Claude가 돌려주는 하루 정리 전체."""
    overview: str             # 그날 뉴스 상황 정리 (3~5문장)
    items: list[DailyItem]    # 중요 기사 5~10개


# Claude에게 주는 역할 설명
SYSTEM = (
    "당신은 한국 경제신문의 편집국장입니다. 하루 동안의 시간별 뉴스 요약을 읽고 "
    "그날 가장 중요했던 뉴스를 골라 독자에게 하루를 정리해 줍니다. "
    "요약에 없는 사실은 쓰지 않습니다."
)


def _prompt(day: str, summaries: list[dict]) -> str:
    """Claude에게 보낼 요청문. 그날의 시간별 요약 목록을 번호와 함께 넣는다."""
    lines = [
        f"{day} (한국시간) 하루 동안의 시간별 뉴스 요약 {len(summaries)}건입니다.",
        "",
        f"1) 그날 가장 중요한 뉴스를 {MIN_ITEMS}~{MAX_ITEMS}개 고르세요. 같은 사건을 다룬 요약은 하나로 합치세요.",
        "   중요도는 경제·시장·사회에 미치는 영향과 보도량(기사 수)으로 판단하세요.",
        "2) 고른 뉴스마다 경제·시장·사회 전반에 좋은 소식이면 '긍정', 나쁜 소식이면 '부정', 판단하기 어렵거나 영향이 중립적이면 '중립'으로 표시하세요.",
        f"3) 테마는 다음 중에서만 고르세요: {', '.join(TOPICS)}",
        "4) overview에는 그날 뉴스 상황을 3~5문장으로 정리하세요. 큰 흐름과 그 이유를 쓰세요.",
        "항목마다 sentiment, topic, title(40자 이내), summary(2~3문장), summary_ids(근거로 쓴 요약 번호)를 채우세요.",
        "",
        "시간별 요약 ([번호] 시각 (테마) 제목 — 요약 [기사 수]):",
    ]
    for s in summaries:
        lines.append(f"[{s['id']}] {s['hour']:02d}시 ({s['topic']}) {s['title']} — {s['summary']} [기사 {len(s['articles'])}건]")
    return "\n".join(lines)


# ---------- 기본 모드 (키 없음) ----------

def sentiment_of(text: str) -> str:
    """단어 사전으로 분위기를 판단한다. 긍정·부정 단어 수를 비교."""
    pos = sum(text.count(w) for w in POSITIVE_WORDS)
    neg = sum(text.count(w) for w in NEGATIVE_WORDS)
    if pos > neg:
        return "긍정"
    if neg > pos:
        return "부정"
    return "중립"


def _score(s: dict) -> float:
    """시간별 요약의 중요도 점수: 다룬 기사 수 + 속보·종합 가산점."""
    return len(s["articles"]) + similar.importance(s["title"])


def basic_daily(day: str, summaries: list[dict]) -> dict:
    """Claude를 쓸 수 없을 때의 하루 정리.

    - 보도량이 많은 순으로 5~10개를 고른다 (점수 2 이상은 최대 10개, 모자라면 5개까지 채움)
    - 같은 소식이 여러 시간대에 있으면 하나만 남긴다
    - 분위기는 단어 사전으로 판단하고, 상황 정리는 통계로 문장을 만든다
    """
    ranked = sorted(summaries, key=lambda s: (-_score(s), s["hour"]))
    picked: list[dict] = []  # 고른 시간별 요약
    for s in ranked:
        if len(picked) >= MAX_ITEMS:
            break
        if _score(s) < 2 and len(picked) >= MIN_ITEMS:
            break
        if any(similar.same_story(s["title"], p["title"]) for p in picked):
            continue
        picked.append(s)

    items = [{
        "sentiment": sentiment_of(f"{s['title']} {s['summary']}"),
        "topic": s["topic"],
        "title": s["title"],
        "summary": s["summary"],
        "article_ids": [a["id"] for a in s["articles"]],
    } for s in picked]
    return {"overview": basic_overview(day, items), "items": items}


def basic_overview(day: str, items: list[dict]) -> str:
    """기본 모드의 '오늘의 뉴스 상황' 문장. 분위기 개수와 많이 다룬 테마로 만든다."""
    if not items:
        return f"{day}에는 정리할 만한 주요 소식이 없었어요."
    counts = Counter(i["sentiment"] for i in items)          # 분위기별 개수
    pos, neu, neg = counts["긍정"], counts["중립"], counts["부정"]
    if neg > pos:
        tone = "부정적인 소식이 더 많은"
    elif pos > neg:
        tone = "긍정적인 소식이 더 많은"
    else:
        tone = "긍정과 부정이 엇갈린"
    top_topic, top_n = Counter(i["topic"] for i in items).most_common(1)[0]  # 가장 많이 나온 테마
    return (
        f"주요 소식 {len(items)}건 가운데 긍정 {pos}건, 중립 {neu}건, 부정 {neg}건으로 {tone} 하루였어요. "
        f"가장 많이 다뤄진 테마는 {top_topic}({top_n}건)이고, "
        f"가장 크게 보도된 소식은 '{items[0]['title']}'이에요."
    )


# ---------- Claude 모드 ----------

def claude_daily(day: str, summaries: list[dict]) -> dict | None:
    """Claude로 하루 정리. 실패하거나 키가 없으면 None (기본 모드로 대신)."""
    result = claude.ask(SYSTEM, _prompt(day, summaries), DailyResult, effort="medium")
    if result is None:
        return None
    by_id = {s["id"]: s for s in summaries}  # 요약 번호 → 시간별 요약
    items = []
    for it in result.items[:MAX_ITEMS]:
        used = [by_id[i] for i in dict.fromkeys(it.summary_ids) if i in by_id]
        if not used:
            continue  # 근거 없는 항목은 버린다
        items.append({
            "sentiment": it.sentiment,
            "topic": it.topic if it.topic in TOPICS else used[0]["topic"],
            "title": it.title.strip(),
            "summary": it.summary.strip(),
            "article_ids": list(dict.fromkeys(a["id"] for s in used for a in s["articles"])),
        })
    if not items:
        return None
    return {"overview": result.overview.strip(), "items": items}


# ---------- 실행 ----------

def summarize_day(day: str, force: bool = False) -> int:
    """day(YYYY-MM-DD)의 하루 정리를 만들어 저장한다. 저장한 기사 수를 돌려준다."""
    if not force and db.daily_done(day):
        return 0
    summaries = db.search_summaries(date=day, limit=10000)  # 그날의 시간별 요약 전체
    if not summaries:
        return 0
    out, method = claude_daily(day, summaries), "claude"
    if out is None:
        out, method = basic_daily(day, summaries), "basic"
    db.save_daily(day, method, out["overview"], out["items"])
    log.info("%s 하루 정리 %d건 저장 (%s)", day, len(out["items"]), method)
    return len(out["items"])


def catch_up_days(days: int = CATCH_UP_DAYS) -> int:
    """어제부터 최근 days일 중 아직 정리하지 않은 날을 정리한다. 오늘은 끝나지 않았으니 제외."""
    today = db.now_kst().date()
    done = 0
    for back in range(days, 0, -1):
        day = (today - timedelta(days=back)).isoformat()
        if summarize_day(day):
            done += 1
    return done


def latest_day() -> str | None:
    """하루 정리가 있는 가장 최근 날짜."""
    dates = db.daily_dates()
    return dates[0] if dates else None

