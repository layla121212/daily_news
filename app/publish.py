"""GitHub Pages용 정적 사이트 만들기.

GitHub Actions가 10분마다 실행한다:
  1) 뉴스 수집  2) 밀린 시간대 요약  3) 시세 조회  4) (장중) 지수 분석
  5) 웹페이지와 JSON 데이터 파일을 --out 폴더에 쓴다

사용법: python -m app.publish --out _site
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import analysis, config, daily, db, jobs, market, similar, sources, summarizer
from .topics import TOPICS

# 로그 기록용
log = logging.getLogger("publish")

# 화면 파일 폴더 (사이트로 복사한다)
STATIC = config.BASE_DIR / "app" / "static"

# 연결된 요약이 없는 기사는 이 기간이 지나면 지운다 (저장소 크기 관리)
ARTICLE_RETENTION_DAYS = config._int("ARTICLE_RETENTION_DAYS", 30)
# 지수 분석은 한국 장 시간(평일) 동안 이 간격으로만 새로 만든다 (Claude 비용 관리)
STATIC_ANALYSIS_TTL = config._int("STATIC_ANALYSIS_TTL", 1800)
LIVE_NEWS_PER_TOPIC = 20  # 실시간 탭: 테마별 최신 기사 수


def market_hours(now: datetime) -> bool:
    """평일 08:30~16:00 (한국시간)."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 8 * 60 + 30 <= hm <= 16 * 60


def _write(path: Path, data) -> None:
    """JSON 파일로 저장 (폴더가 없으면 만든다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def prune() -> int:
    """보관 기간이 지난 기사 중 요약에 쓰이지 않은 것을 지운다. 지운 수를 돌려준다."""
    cutoff = (db.now_kst() - timedelta(days=ARTICLE_RETENTION_DAYS)).isoformat(timespec="seconds")
    with db.connect() as conn:
        n = conn.execute(
            "DELETE FROM articles WHERE published_at < ? AND id NOT IN (SELECT article_id FROM summary_articles)",
            (cutoff,),
        ).rowcount
    if n:
        with db.connect() as conn:
            conn.execute("VACUUM")
    return n


def _analysis(state_path: Path, mkt: dict) -> dict:
    """저장된 분석이 아직 유효하면 재사용, 장중이고 오래됐으면 새로 만든다."""
    now = db.now_kst()
    prev = None
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            prev = None
    if prev:
        age = (now - datetime.fromisoformat(prev["generated_at"])).total_seconds()
        fresh = age < STATIC_ANALYSIS_TTL or (not market_hours(now) and prev.get("method") == "claude")
        if fresh:
            return prev
    out = analysis.build(mkt)
    if out["method"] == "claude" or not prev or prev.get("method") != "claude":
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    return prev  # 새 분석이 실패하면 이전 Claude 분석을 유지


def live_news() -> list[dict]:
    """실시간 탭용: 테마별 최신 기사에 같은 소식 묶음 정보를 붙인다."""
    items = [a for t in TOPICS for a in db.latest_articles(limit=LIVE_NEWS_PER_TOPIC, topic=t)]
    similar.assign_stories(items)
    items.sort(key=lambda a: (a["published_at"], a["id"]), reverse=True)
    return [{k: a[k] for k in ("id", "title", "url", "source", "topic", "published_at", "story", "story_size")}
            | {"description": a["description"][:160]} for a in items]


def export(out: Path, mkt: dict, ana: dict) -> None:
    """웹페이지와 data/*.json 파일을 out 폴더에 쓴다."""
    data = out / "data"
    with db.connect() as conn:
        dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM summaries ORDER BY date DESC")]
        art_dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT substr(published_at,1,10) d FROM articles ORDER BY d DESC")]
    for d in dates:
        _write(data / "summaries" / f"{d}.json", db.search_summaries(date=d, limit=10000))
    for d in art_dates:
        rows = db.search_articles(date=d, limit=100000)
        _write(data / "articles" / f"{d}.json", [
            {k: r[k] for k in ("id", "title", "url", "source", "topic", "published_at")}
            | {"description": r["description"][:160]} for r in rows
        ])
    _write(data / "live" / "market.json", mkt)
    _write(data / "live" / "analysis.json", ana)
    _write(data / "live" / "news.json", live_news())
    # '요약' 탭: 날짜별 하루 정리 파일
    daily_dates = db.daily_dates()
    for d in daily_dates:
        _write(data / "daily" / f"{d}.json", db.get_daily(d))
    _write(data / "meta.json", {
        "updated_at": db.now_kst().isoformat(timespec="seconds"),
        "today": db.now_kst().date().isoformat(),
        "topics": TOPICS,
        "summary_dates": dates,
        "article_dates": art_dates,
        "daily_dates": daily_dates,  # 하루 정리가 있는 날짜 (최신순)
    })
    # 웹페이지: 서버 없이 JSON 파일을 읽는 모드로 표시
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace('<html lang="ko">', '<html lang="ko" data-mode="static">', 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    shutil.copytree(STATIC, out / "static", dirs_exist_ok=True)
    (out / "static" / "index.html").unlink(missing_ok=True)
    (out / ".nojekyll").write_text("")


def main() -> None:
    """GitHub Actions가 실행하는 진입점: 수집 → 요약 → 하루 정리 → 시세·분석 → 사이트 생성."""
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True, help="사이트 파일을 쓸 폴더")
    p.add_argument("--state", type=Path, default=Path(config.DB_PATH).parent / "analysis.json",
                   help="지난 지수 분석 저장 위치")
    p.add_argument("--skip-collect", action="store_true", help="뉴스 수집·요약 없이 내보내기만")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db.init()
    log.info("삭제한 주제의 요약·기사 %d건 정리", db.drop_unknown_topics(list(TOPICS)))
    if not args.skip_collect:
        sources.collect()
        jobs.catch_up(hours=3)
        log.info("옛 방식 요약 %d개 시간대 다시 만듦", summarizer.rebuild_outdated())
        # 하루가 끝난 날(어제 등)의 '요약' 탭 정리. 시간별 요약이 끝난 뒤에 만들어야 23시까지 들어간다.
        log.info("하루 정리 %d일치 만듦", daily.catch_up_days())
        log.info("오래된 기사 %d건 정리", prune())
    mkt = market.fetch_market()
    ana = _analysis(args.state, mkt)
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    export(args.out, mkt, ana)
    log.info("사이트 생성 완료: %s", args.out)


if __name__ == "__main__":
    main()
