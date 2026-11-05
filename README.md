# 데일리 뉴스요약

매시간 뉴스를 주제별로 3줄 요약해 보여주고, 요약에 쓰인 원문 기사를 저장해 검색할 수 있는 웹페이지입니다.
"실시간" 탭에서는 코스피·코스닥 지수 차트, 한국·미국 지수 선물, 지수가 움직이는 이유(AI 분석과 근거 뉴스), 실시간 뉴스를 볼 수 있습니다.

## 화면 구성

- **첫 화면**: "(날짜)의 뉴스요약". 시간대별로 주제마다 요약을 보여주고, 각 요약 아래에서 "요약에 쓰인 원문"을 펼쳐 볼 수 있습니다.
- **검색**: 단어, 날짜, 주제 검색을 함께 쓸 수 있습니다. 검색어는 요약 본문뿐 아니라 저장된 원문 기사에서도 찾습니다.
- **주제**: 전체 · 실시간 · 경제 · 증시·투자 · 부동산 · 산업·기업 · IT·과학 · 정치 · 국제. 주제를 고르면 추천 키워드가 나옵니다.
- **실시간 탭**
  1. 코스피·코스닥 현재 지수와 장중 차트
  2. 한국 지수 선물(코스피200 선물), 미국 지수 선물(S&P 500·나스닥100·다우·러셀2000)
  3. 지수는 왜 움직이고 있을까? (Claude 분석 + 근거 뉴스 링크)
  4. 실시간 뉴스: 기본값은 처음 조회한 시점의 뉴스만 보여주고, 5초·10초·30초·1분·5분·30분 중에서 갱신 주기를 고를 수 있습니다.

## 동작 방식

| 작업 | 주기 | 설명 |
|---|---|---|
| 뉴스 수집 | 1분 (`COLLECT_INTERVAL`) | `feeds.json`의 언론사 RSS를 읽어 새 기사만 SQLite에 저장 |
| 시간별 요약 | 매시 5분 | 직전 1시간의 기사를 Claude로 주제별 요약하고, 요약과 사용한 기사를 연결해 저장 |
| 시세 | 요청 시, 5초 캐시 (`MARKET_CACHE_TTL`) | 여러 사람이 5초마다 새로고침해도 외부 시세 서버에는 5초에 한 번만 요청 |
| 지수 분석 | 요청 시, 10분 캐시 (`ANALYSIS_TTL`) | Claude 호출 비용과 부하를 줄이기 위해 10분 동안 같은 분석 재사용 |

화면이 5초마다 새로고침해도 서버는 이미 저장된 기사 중 새로 들어온 것(`after_id` 이후)만 돌려주므로 부담이 거의 없습니다.

`ANTHROPIC_API_KEY`가 없으면 요약은 기사 소개문을 그대로 보여주는 기본 모드로, 지수 분석은 관련 최신 뉴스 목록으로 대신 표시됩니다.

## 웹 주소로 공개하기 (GitHub Pages)

서버 없이 GitHub만으로 누구나 휴대폰에서 볼 수 있는 주소를 만듭니다.
주소: **https://layla121212.github.io/daily_news/**

`.github/workflows/publish.yml`이 10분마다 자동으로 실행되어 뉴스 수집, 시간별 요약, 시세 조회, (평일 장중) 지수 분석을 하고 웹페이지를 다시 올립니다.
쌓인 데이터(`news.db`)는 `data` 브랜치에 보관합니다. 커밋 1개로 매번 덮어쓰므로 저장소가 계속 커지지 않습니다.

처음 한 번만 휴대폰 브라우저에서 설정하면 됩니다.

1. 이 PR을 `main`에 합칩니다 (자동 실행은 `main` 브랜치에서만 돌아갑니다).
2. 저장소 **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 바꿉니다.
3. (선택) AI 요약·분석을 쓰려면 **Settings → Secrets and variables → Actions → New repository secret**에서 이름 `ANTHROPIC_API_KEY`, 값에 Claude API 키를 넣습니다.
4. **Actions → publish → Run workflow**로 한 번 실행하면 몇 분 뒤 위 주소에 사이트가 열립니다.

정적 사이트 모드의 차이점:
- 시세와 뉴스는 약 10분마다 갱신됩니다. GitHub의 예약 실행은 늦어질 때가 있어 실제로는 10~20분 간격일 수 있습니다. 화면의 5초·10초 갱신은 그 사이에 새 데이터가 올라왔는지 확인합니다.
- 날짜 없이 단어로 검색하면 최근 7일의 요약·기사에서 찾습니다. 날짜를 고르면 그날 전체에서 찾습니다.
- 요약에 쓰이지 않은 기사는 30일 뒤 정리됩니다(`ARTICLE_RETENTION_DAYS`). 요약과 요약에 쓰인 기사는 계속 남습니다.
- 지수 분석은 평일 08:30~16:00(한국시간)에 30분마다 새로 만들고, 그 외 시간에는 마지막 분석을 보여줍니다.

### Claude API 비용 (대략)

API 키를 넣으면 요약은 하루 24번, 지수 분석은 평일 하루 약 15번 Claude를 호출합니다. 기사 양에 따라 다르지만 기본 모델(`claude-opus-5-5`)로 **월 수십~100달러 안팎**이 들 수 있습니다.
비용을 줄이려면 **Settings → Secrets and variables → Actions → Variables**에 `CLAUDE_MODEL` 변수를 만들어 `claude-sonnet-5-5`(약 절반) 또는 `claude-haiku-5-5`(훨씬 저렴)로 바꿀 수 있습니다. 키를 넣지 않으면 비용 없이 기본 모드(기사 소개문 요약)로 동작합니다.

## 실행 방법 (내 컴퓨터에서 서버로)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=...   # AI 요약·분석에 필요 (선택)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 http://localhost:8000 을 엽니다. 처음 켜면 1분 안에 기사를 모으기 시작하고, 매시 5분마다 요약이 쌓입니다.

### 설정 (환경 변수)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 없음 | Claude API 키 |
| `CLAUDE_MODEL` | `claude-opus-5-5` | 요약·분석에 쓸 모델 |
| `NEWS_DB_PATH` | `data/news.db` | SQLite 파일 위치 |
| `NEWS_FEEDS_PATH` | `feeds.json` | 수집할 RSS 목록 |
| `COLLECT_INTERVAL` | `60` | 뉴스 수집 주기(초) |
| `MARKET_CACHE_TTL` | `5` | 시세 캐시(초) |
| `ANALYSIS_TTL` | `600` | 지수 분석 캐시(초) |
| `ENABLE_JOBS` | `1` | `0`이면 수집·요약을 돌리지 않음 |

## API

| 주소 | 설명 |
|---|---|
| `GET /api/topics` | 주제와 추천 키워드 |
| `GET /api/summaries?date=&topic=&q=` | 시간별 요약 검색 (기본: 오늘) |
| `GET /api/articles?date=&topic=&q=` | 저장된 원문 기사 검색 |
| `GET /api/live/market` | 지수·선물 시세 |
| `GET /api/live/analysis` | 지수 변화 분석과 근거 뉴스 |
| `GET /api/live/news?after_id=` | 실시간 뉴스 (after_id 이후 새 기사만) |

## 데이터 출처와 주의사항

- 뉴스: 연합뉴스·한국경제 RSS (`feeds.json`에서 추가·변경 가능)
- 지수·미국 선물: Yahoo Finance 차트 API (`^KS11`, `^KQ11`, `ES=F`, `NQ=F`, `YM=F`, `RTY=F`)
- 코스피200 선물: 네이버 증권 모바일 API (`FUT`)
- 위 시세 API들은 공식 공개 API가 아니어서 예고 없이 바뀌거나 막힐 수 있습니다. 서비스로 운영하려면 증권사 Open API(예: 한국투자증권 KIS Developers) 같은 정식 시세 API로 바꾸는 것을 권장합니다.
- 기사 저작권은 각 언론사에 있습니다. 이 앱은 제목·소개문과 원문 링크만 저장합니다.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```
