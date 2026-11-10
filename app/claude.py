"""Claude API 호출 공통부: 구조화된 JSON 응답을 받는다."""
from __future__ import annotations

import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from . import config

# 로그 기록용
log = logging.getLogger(__name__)

# 응답 형식 클래스 (pydantic 모델) 자리표시
T = TypeVar("T", bound=BaseModel)

# Claude API 클라이언트 (처음 쓸 때 만든다)
_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    """Claude API 클라이언트 (한 번 만들어 재사용)."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=120.0)
    return _client


def ask(system: str, prompt: str, schema: type[T], effort: str = "medium", max_tokens: int = 16000) -> T | None:
    """schema 형태로 답을 받는다. 키가 없거나 실패·거절되면 None (호출하는 쪽에서 대체 동작)."""
    if not config.has_claude_credentials():
        return None
    try:
        resp = client().beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
            # 안전 분류기에 의해 거절되면 서버가 권장 모델로 다시 시도한다
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        log.warning("Claude 요청 한도 초과: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        log.error("Claude API 오류 %s: %s", exc.status_code, exc)
        return None
    except anthropic.APIConnectionError as exc:
        log.error("Claude 연결 실패: %s", exc)
        return None
    if resp.stop_reason == "refusal":
        log.warning("Claude가 요청을 거절함: %s", resp.stop_details)
        return None
    if resp.stop_reason == "max_tokens":
        log.warning("Claude 응답이 max_tokens에서 잘림")
        return None
    return resp.parsed_output
