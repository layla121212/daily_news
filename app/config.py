"""환경 변수 기반 설정."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# 한국 시간대 (모든 날짜·시각은 한국시간 기준)
KST = ZoneInfo("Asia/Seoul")

# 프로젝트 최상위 폴더
BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    """환경 변수를 정수로 읽는다. 없거나 잘못되면 default."""
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# SQLite 파일 위치
DB_PATH = Path(os.environ.get("NEWS_DB_PATH", BASE_DIR / "data" / "news.db"))

# 뉴스 피드 목록 파일 (JSON)
FEEDS_PATH = Path(os.environ.get("NEWS_FEEDS_PATH", BASE_DIR / "feeds.json"))

# 실시간 뉴스 수집 주기(초). 사용자가 5초로 보더라도 서버는 이 주기로만 언론사에 요청한다.
COLLECT_INTERVAL = _int("COLLECT_INTERVAL", 60)

# 지수·선물 시세 캐시 유지 시간(초). 이 시간 안의 요청은 캐시로 응답한다.
MARKET_CACHE_TTL = _int("MARKET_CACHE_TTL", 5)

# 지수 변화 분석(Claude) 재생성 최소 간격(초)
ANALYSIS_TTL = _int("ANALYSIS_TTL", 600)

# Claude 모델
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

# 백그라운드 작업(수집·요약) 실행 여부. 테스트에서는 끈다.
ENABLE_JOBS = os.environ.get("ENABLE_JOBS", "1") == "1"

# 외부 사이트(RSS·시세) 요청 제한 시간(초)
HTTP_TIMEOUT = 10.0
# 외부 사이트에 보내는 프로그램 이름
USER_AGENT = "Mozilla/5.0 (compatible; DailyNewsSummary/1.0)"


def has_claude_credentials() -> bool:
    """Claude API 키(또는 토큰)가 설정되어 있는지."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
