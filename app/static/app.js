"use strict";
/*
 * 데일리 뉴스요약 화면 스크립트
 *
 * 화면(탭) 구성
 *   - 전체 / 주제별 : 시간별 요약 (renderSummaries)
 *   - 요약          : 하루 뉴스 정리 (renderDaily)
 *   - 실시간        : 지수·선물·분석·실시간 뉴스 (showLive)
 *
 * 데이터 읽는 방식
 *   - 서버 모드  : FastAPI 서버의 /api/... 주소를 호출
 *   - 정적 모드  : GitHub Pages에 올라간 data/*.json 파일을 읽어 서버 응답과 같은 모양으로 만든다
 */

/* ---------- 공통 도우미 ---------- */

// 선택자로 요소 하나 찾기
const $ = (s) => document.querySelector(s);
// HTML 특수문자 바꾸기 (기사 제목 등을 화면에 안전하게 넣기 위해)
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// http/https 주소만 링크로 허용
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
// 검색어를 노란 형광펜(<mark>)으로 표시
const hl = (s, q) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s));
// "2026-11-06" → "2026년 11월 6일"
const fmtDate = (d) => { const [y, m, dd] = d.split("-"); return `${y}년 ${+m}월 ${+dd}일`; };
// 숫자를 소수점 둘째 자리까지 (시세 표시용)
const n2 = (v) => (v == null ? "-" : v.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
// 시:분:초
const hms = (t) => t.toTimeString().slice(0, 8);

/* ---------- 데이터 읽기 (서버 모드 / 정적 모드) ---------- */

// 정적 모드 여부: GitHub Pages용 index.html에는 <html data-mode="static">이 붙는다
const STATIC = document.documentElement.dataset.mode === "static";
// 정적 모드에서 날짜 없이 검색할 때 살펴볼 최근 일수
const SEARCH_DAYS = 7;

// 서버 /api 호출 또는 정적 파일 읽기. 어느 쪽이든 같은 모양의 데이터를 돌려준다.
async function api(path, params = {}) {
  if (STATIC) return staticApi(path, params);
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const r = await fetch(`${path}${qs.toString() ? "?" + qs : ""}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// 정적 모드: data/meta.json 내용 (갱신 시각, 주제 목록, 날짜 목록 등)
let META = null;
// 정적 모드: 한 번 읽은 파일을 다시 받지 않도록 저장해 두는 곳
const jsonCache = new Map();

// data/ 아래 JSON 파일 읽기. fresh=true면 항상 새로 받는다 (실시간 데이터용).
async function getJSON(file, fresh = false) {
  const url = `data/${file}?v=${fresh ? Date.now() : encodeURIComponent(META?.updated_at || "")}`;
  if (!fresh && jsonCache.has(url)) return jsonCache.get(url);
  const p = fetch(url).then((r) => (r.ok ? r.json() : r.status === 404 ? null : Promise.reject(new Error(r.statusText))));
  if (!fresh) {
    jsonCache.set(url, p);
    p.catch(() => jsonCache.delete(url));
  }
  return p;
}

// meta.json 새로 읽기
async function loadMeta() {
  META = await getJSON("meta.json", true);
  return META;
}

// 문자열 s에 q가 들어 있는지
const has = (s, q) => String(s || "").includes(q);

// 정적 모드에서 서버 /api 주소를 흉내 낸다 (같은 모양의 응답을 만든다)
async function staticApi(path, p) {
  if (!META) await loadMeta();
  switch (path) {
    case "/api/topics": {
      const m = await loadMeta();
      return { today: m.today, topics: m.topics };
    }
    case "/api/summaries": {
      const d = p.date || (p.q ? null : META.today);
      const dates = d ? [d] : META.summary_dates.slice(0, SEARCH_DAYS);
      const days = await Promise.all(dates.map((x) => getJSON(`summaries/${x}.json`)));
      let items = days.flatMap((x) => x || []);
      if (p.topic) items = items.filter((s) => s.topic === p.topic);
      if (p.q) items = items.filter((s) => has(s.title, p.q) || has(s.summary, p.q) || s.articles.some((a) => has(a.title, p.q)));
      return { date: d, items, searched_days: d ? null : dates.length };
    }
    case "/api/daily": {
      // 하루 정리: 날짜를 고르지 않았으면 가장 최근 정리
      const d = p.date || (META.daily_dates || [])[0] || null;
      const data = d ? await getJSON(`daily/${d}.json`) : null;
      return data || { date: d, overview: null, items: [] };
    }
    case "/api/articles": {
      const dates = p.date ? [p.date] : META.article_dates.slice(0, SEARCH_DAYS);
      const days = await Promise.all(dates.map((x) => getJSON(`articles/${x}.json`)));
      let items = days.flatMap((x) => x || []);
      if (p.topic) items = items.filter((a) => a.topic === p.topic);
      if (p.q) items = items.filter((a) => has(a.title, p.q) || has(a.description, p.q));
      items.sort((a, b) => b.published_at.localeCompare(a.published_at));
      return { items: items.slice(0, p.limit || 50) };
    }
    case "/api/live/market":
      return getJSON("live/market.json", true);
    case "/api/live/analysis":
      return getJSON("live/analysis.json", true);
    case "/api/live/news": {
      const m = await loadMeta(); // 데이터가 언제 갱신됐는지 함께 알려준다
      const all = (await getJSON("live/news.json", true)) || [];
      return { items: all.filter((a) => a.id > (p.after_id || 0)).slice(0, p.limit || 30), data_updated_at: m.updated_at };
    }
  }
  throw new Error(`unknown ${path}`);
}

/* ---------- 화면 상태 ---------- */

// 주제별 추천 키워드 { "경제": ["금리", ...], ... } — 순서가 주제 버튼 순서
let TOPICS = {};
// 오늘 날짜 (YYYY-MM-DD, 한국시간)
let today = "";
// 지금 선택된 탭: "전체" / "요약" / "실시간" / 주제 이름
let topic = "전체";
// 주제가 아닌 특별한 탭 (주제 버튼 앞에 붙는다)
const SPECIAL_TABS = ["전체", "요약", "실시간"];

/* ---------- 주제 버튼 ---------- */

// 상단 버튼 줄과 추천 키워드 그리기
function renderChips() {
  const names = [...SPECIAL_TABS, ...Object.keys(TOPICS)];
  $("#chips").innerHTML = names
    .map((k) => `<button class="chip ${k === "실시간" ? "live" : ""} ${k === topic ? "on" : ""}" data-t="${esc(k)}">${esc(k)}</button>`)
    .join("");
  const kws = TOPICS[topic] || [];
  $("#kw").innerHTML = kws.length ? "추천 키워드: " + kws.map((k) => `<button data-k="${esc(k)}">#${esc(k)}</button>`).join(" ") : "";
}

// 목록을 key 기준으로 묶기 (처음 나온 순서 유지) → Map(key → 목록)
function groupBy(list, key) {
  const m = new Map();
  for (const x of list) {
    const k = key(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return m;
}

// 화면 위 주제 버튼 순서대로 정렬한 주제 이름 목록 (목록에 없는 주제는 뒤로)
function topicOrder(map) {
  const order = Object.keys(TOPICS);
  return [...map.keys()].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
}

/* ---------- 전체 / 주제별: 시간별 요약 ---------- */

// 검색어·날짜·주제에 맞는 시간별 요약을 그린다
async function renderSummaries() {
  const q = $("#q").value.trim();          // 검색어
  const d = $("#d").value;                 // 선택한 날짜 (없으면 빈 문자열)
  const t = topic === "전체" ? "" : topic; // 주제 필터
  $("#status").textContent = "불러오는 중…";
  let data;
  try {
    data = await api("/api/summaries", { q, date: d, topic: t });
  } catch (e) {
    $("#status").textContent = `오류: ${e.message}`;
    return;
  }
  $("#title").textContent = data.date ? `${fmtDate(data.date)}의 뉴스요약` : `"${q}" 검색 결과`;
  const filters = [q && `"${q}"`, t, d && fmtDate(d)].filter(Boolean); // 화면에 보여줄 검색 조건
  $("#status").textContent = filters.length
    ? `요약 ${data.items.length}건 · ${filters.join(" · ")}` + (data.searched_days ? ` · 최근 ${data.searched_days}일에서 검색 (날짜를 고르면 그날 전체를 찾아요)` : "")
    : "";

  if (!data.items.length) {
    $("#list").innerHTML = `<div class="empty">${
      filters.length ? "조건에 맞는 요약이 없어요." : `아직 오늘의 요약이 없어요. 매시 정각이 지나면 직전 1시간의 뉴스가 요약돼요${STATIC ? " (반영까지 10~20분 걸릴 수 있어요)" : ""}.`
    }</div>`;
  } else {
    // "날짜 시:00" 단위로 묶는다
    const groups = new Map();
    for (const s of data.items) {
      const key = `${s.date} ${String(s.hour).padStart(2, "0")}:00`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    $("#list").innerHTML = [...groups]
      .map(([key, items]) => {
        const label = key.startsWith(data.date || "-") ? key.slice(11) : key.slice(5);
        // 테마(주제) 순서대로 묶어서 보여준다
        const byTopic = groupBy(items, (s) => s.topic);
        return `<div class="hour"><div class="time">${esc(label)}</div><div class="card">${topicOrder(byTopic)
          .map(
            (t) => `<section class="tgroup"><div class="thead">${esc(t)}</div>${byTopic
              .get(t)
              .map(
                (s) => `<div class="item"><h3>${hl(s.title, q)}</h3>${s.summary ? `<p>${hl(s.summary, q)}</p>` : ""}
          <details class="src"><summary>${s.articles.length > 1 ? `같은 소식 기사 ${s.articles.length}건` : "원문 기사"}</summary><ul>${s.articles
            .map((a) => `<li><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${hl(a.title, q)}</a> · ${esc(a.source)}</li>`)
            .join("")}</ul></details></div>`
              )
              .join("")}</section>`
          )
          .join("")}</div></div>`;
      })
      .join("");
  }

  // 검색어가 있으면 저장된 원문 기사도 함께 보여준다
  if (q) {
    const arts = await api("/api/articles", { q, date: d, topic: t, limit: 30 }).catch(() => ({ items: [] }));
    $("#articleSection").classList.toggle("hidden", !arts.items.length);
    $("#articleList").innerHTML = arts.items
      .map((a) => `<li><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${hl(a.title, q)}</a>
        <span class="muted">· ${esc(a.source)} · ${esc(a.published_at.slice(0, 16).replace("T", " "))}</span></li>`)
      .join("");
  } else {
    $("#articleSection").classList.add("hidden");
  }
}

/* ---------- 요약 탭: 하루 뉴스 정리 ---------- */

// 분위기 표시값 → 화면용 CSS 클래스
const SENTIMENT_CLASS = { 긍정: "pos", 중립: "neu", 부정: "neg" };

// 하루 정리 그리기. 날짜 칸을 고르면 그날, 아니면 가장 최근 정리를 보여준다.
async function renderDaily() {
  const d = $("#d").value; // 선택한 날짜 (없으면 최근)
  $("#dailyStatus").textContent = "불러오는 중…";
  let data;
  try {
    data = await api("/api/daily", { date: d });
  } catch (e) {
    $("#dailyStatus").textContent = `오류: ${e.message}`;
    return;
  }
  $("#dailyTitle").textContent = data.date ? `${fmtDate(data.date)}의 하루 뉴스 정리` : "하루 뉴스 정리";

  // 정리가 아직 없을 때
  if (!data.overview) {
    $("#dailyStatus").textContent = "";
    $("#dailyOverview").innerHTML = `<div class="empty">${
      d ? `${fmtDate(d)}의 하루 정리가 없어요.` : "아직 하루 정리가 없어요. 하루가 끝나면(자정 이후) 그날의 뉴스가 정리돼요."
    }</div>`;
    $("#dailyList").innerHTML = "";
    $("#dailyListHead").classList.add("hidden");
    return;
  }

  // 분위기별 개수 (긍정·중립·부정)
  const counts = { 긍정: 0, 중립: 0, 부정: 0 };
  data.items.forEach((it) => (counts[it.sentiment] = (counts[it.sentiment] || 0) + 1));
  $("#dailyStatus").textContent = data.method === "claude" ? "Claude가 정리했어요" : "AI 키가 없어 보도량·단어 기준으로 자동 정리했어요";

  // 오늘의 뉴스 상황 (전체 흐름 정리)
  $("#dailyOverview").innerHTML = `<div class="ovhead"><b>오늘의 뉴스 상황</b>
      <span class="senti pos">긍정 ${counts.긍정}</span><span class="senti neu">중립 ${counts.중립}</span><span class="senti neg">부정 ${counts.부정}</span></div>
    <p>${esc(data.overview)}</p>`;

  // 중요 뉴스 목록: 앞에 (긍정)(중립)(부정)
  $("#dailyListHead").classList.remove("hidden");
  $("#dailyList").innerHTML = data.items
    .map(
      (it, i) => `<div class="item daily">
        <h3><span class="rank">${i + 1}</span><span class="senti ${SENTIMENT_CLASS[it.sentiment] || "neu"}">(${esc(it.sentiment)})</span>${esc(it.title)}</h3>
        <span class="tag">${esc(it.topic)}</span>
        ${it.summary ? `<p>${esc(it.summary)}</p>` : ""}
        ${
          it.articles.length
            ? `<details class="src"><summary>근거 기사 ${it.articles.length}건</summary><ul>${it.articles
                .map((a) => `<li><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a> · ${esc(a.source)}</li>`)
                .join("")}</ul></details>`
            : ""
        }
      </div>`
    )
    .join("");
}

/* ---------- 실시간 탭: 지수·선물·분석 ---------- */

// 장중 지수 차트(SVG). series: [{t: "09:00", v: 2800.1}, ...], prev: 전일 종가
function spark(series, prev) {
  if (!series.length) return `<div class="muted" style="padding:40px 0;text-align:center">차트 데이터 없음</div>`;
  const pts = series.map((p) => p.v);
  const w = 300, h = 120;
  const all = prev != null ? [...pts, prev] : pts;
  const min = Math.min(...all), max = Math.max(...all);
  const y = (v) => h - 6 - ((v - min) / (max - min || 1)) * (h - 12);
  const x = (i) => (pts.length > 1 ? (i / (pts.length - 1)) * w : w / 2);
  const col = prev == null || pts[pts.length - 1] >= prev ? "var(--up)" : "var(--down)";
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const base = prev != null ? `<line x1="0" x2="${w}" y1="${y(prev)}" y2="${y(prev)}" stroke="var(--sub)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>` : "";
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${base}
    <path d="${d}L${w},${h}L0,${h}Z" fill="${col}" opacity=".08"/>
    <path d="${d}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

// 등락 표시 (▲ 51.63 (+1.85%))
function chg(x) {
  if (x.change == null) return `<span class="chg muted">-</span>`;
  const up = x.change >= 0;
  return `<span class="chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${n2(Math.abs(x.change))} (${up ? "+" : ""}${n2(x.change_pct)}%)</span>`;
}

// 지수·선물 시세 그리기
async function loadMarket() {
  let m;
  try {
    m = await api("/api/live/market");
  } catch (e) {
    $("#idx").innerHTML = `<div class="card muted">시세를 불러오지 못했어요: ${esc(e.message)}</div>`;
    return;
  }
  $("#idx").innerHTML = m.indices
    .map((x) => {
      const s = x.series || [];
      return `<div class="card"><div class="name">${esc(x.name)}</div><div class="val">${n2(x.price)}</div>${chg(x)}
      ${spark(s, x.prev_close)}<div class="axis"><span>${esc(s[0]?.t || "")}</span><span>점선 = 전일 종가</span><span>${esc(s[s.length - 1]?.t || "")}</span></div></div>`;
    })
    .join("");
  // 선물 표 (종목 / 현재가 / 등락)
  const table = (rows) =>
    `<tr><th>종목</th><th class="r">현재가</th><th class="r">등락</th></tr>` +
    rows
      .map((f) => {
        const cls = f.change_pct == null ? "muted" : f.change_pct >= 0 ? "up" : "down";
        const pct = f.change_pct == null ? "-" : `${f.change_pct >= 0 ? "+" : ""}${n2(f.change_pct)}%`;
        return `<tr><td>${esc(f.name)}</td><td class="r">${n2(f.price)}</td><td class="r ${cls}">${pct}</td></tr>`;
      })
      .join("");
  $("#futKR").innerHTML = table(m.futures.kr);
  $("#futUS").innerHTML = table(m.futures.us);
  $("#liveLead").textContent = `${m.updated_at.slice(0, 16).replace("T", " ")} 기준 시세` + (m.errors.length ? ` · 일부 시세 조회 실패: ${m.errors.join(", ")}` : "") + (STATIC ? " · 시세와 뉴스는 약 10분마다 갱신돼요" : "");
}

// "지수는 왜 움직이고 있을까?" 분석 그리기
async function loadAnalysis() {
  let a;
  try {
    a = await api("/api/live/analysis");
  } catch (e) {
    $("#opinion").innerHTML = `<span class="muted">분석을 불러오지 못했어요: ${esc(e.message)}</span>`;
    return;
  }
  $("#opinion").innerHTML = `<div class="hd"><b>${esc(a.headline)}</b><small>${esc(a.generated_at.slice(11, 16))} 기준${a.method === "claude" ? " · Claude 분석" : ""}</small></div>
    <ol>${a.points
      .map(
        (p) => `<li><b>${esc(p.title)}</b> ${esc(p.detail)}${
          p.evidence.length
            ? `<div class="ev">근거: ${p.evidence.map((e) => `<a href="${safeUrl(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>`).join(" · ")}</div>`
            : ""
        }</li>`
      )
      .join("")}</ol>
    <div class="warn">※ 뉴스와 가격 흐름을 바탕으로 한 해석이며 투자 권유가 아닙니다.</div>`;
}

/* ---------- 실시간 탭: 뉴스 ---------- */

let feed = [];        // 지금 화면에 있는 기사 목록 (최신순)
let fresh = new Set(); // 마지막 새로고침에서 새로 들어온 기사 id (NEW 표시용)
let lastId = 0;       // 받은 기사 중 가장 큰 id (다음엔 이보다 새 기사만 받는다)
let lastAt = null;    // 마지막으로 조회한 시각
let lastNew = null;   // 마지막 새로고침에서 새로 들어온 기사 수
let dataAt = null;    // 정적 모드: 서버 데이터가 마지막으로 갱신된 시각
let timer = null;     // 자동 갱신 타이머
let ticker = null;    // "다음 갱신까지 N초" 표시 타이머
let nextAt = 0;       // 다음 자동 갱신 시각 (ms)
// 뉴스 보기 방식: "topic"(테마별) 또는 "time"(시간별)
let liveMode = "topic";

// 실시간 뉴스 받아오기. initial=true면 처음 조회(전체), 아니면 새 기사만.
async function loadNews(initial) {
  let r;
  try {
    r = await api("/api/live/news", { after_id: initial ? 0 : lastId, limit: 200 });
  } catch (e) {
    $("#meta").textContent = `뉴스 조회 실패: ${e.message}`;
    return;
  }
  fresh = new Set(initial ? [] : r.items.map((a) => a.id));
  const known = new Set(feed.map((a) => a.id)); // 이미 화면에 있는 기사
  feed = (initial ? r.items : [...r.items.filter((a) => !known.has(a.id)), ...feed])
    .sort((a, b) => b.published_at.localeCompare(a.published_at) || b.id - a.id)
    .slice(0, 300);
  lastNew = initial ? null : r.items.length;
  dataAt = r.data_updated_at || null;
  if (feed.length) lastId = Math.max(lastId, ...feed.map((a) => a.id));
  lastAt = new Date();
  drawFeed();
  meta();
}

// 보기 방식(테마별/시간별)에 맞춰 뉴스 목록을 다시 그린다
function drawFeed() {
  if (!feed.length) {
    $("#feed").innerHTML = `<div class="empty">아직 수집된 뉴스가 없어요.</div>`;
    return;
  }
  $("#feed").innerHTML = liveMode === "time" ? renderFeedByTime(feed) : renderFeed(feed);
}

// 테마 하나 안에서 보여줄 최대 소식 수
const LIVE_STORIES_PER_TOPIC = 5;

// 기사 목록을 테마별 묶음(section)으로 그린다. 테마 안에서는 같은 소식을 하나로 합친다 (최신 기사가 대표).
function renderTopicSections(list) {
  const byTopic = groupBy(list, (a) => a.topic);
  return topicOrder(byTopic)
    .map((t) => {
      const stories = [...groupBy(byTopic.get(t), (a) => a.story ?? a.id).values()].slice(0, LIVE_STORIES_PER_TOPIC);
      const newCount = byTopic.get(t).filter((a) => fresh.has(a.id)).length; // 이 테마의 새 기사 수
      return `<section class="tgroup"><div class="thead">${esc(t)}${newCount ? ` <span class="badge">새 기사 ${newCount}</span>` : ""}</div>${stories
        .map((g) => {
          const a = g[0];                              // 대표 기사 (가장 최신)
          const isNew = g.some((x) => fresh.has(x.id)); // 새로 들어온 기사가 있는 소식인지
          const more = g.slice(1);                      // 같은 소식의 나머지 기사
          return `<div class="row ${isNew ? "new" : ""}"><div class="t">${esc(a.published_at.slice(11, 16))}</div><div>
          <a class="ti" href="${safeUrl(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>${isNew ? '<span class="badge">NEW</span>' : ""}
          <div class="s">${esc(a.description.slice(0, 120))} <span class="muted">· ${esc(a.source)}</span></div>${
            more.length
              ? `<details class="src"><summary>같은 소식 ${more.length}건 더</summary><ul>${more
                  .map((x) => `<li><a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a> · ${esc(x.published_at.slice(11, 16))}</li>`)
                  .join("")}</ul></details>`
              : ""
          }</div></div>`;
        })
        .join("")}</section>`;
    })
    .join("");
}

// (테마별) 보기: 테마마다 최신 소식
function renderFeed(list) {
  return renderTopicSections(list);
}

// (시간별) 보기: 시간대(최신 시간부터)마다 테마별 소식
function renderFeedByTime(list) {
  const byHour = groupBy(list, (a) => a.published_at.slice(0, 13)); // "2026-11-06T10"
  return [...byHour]
    .map(([key, items]) => {
      const label = key.slice(0, 10) === today ? `${key.slice(11)}시` : `${key.slice(5, 10)} ${key.slice(11)}시`;
      return `<div class="hgroup"><div class="hhead">${esc(label)}</div>${renderTopicSections(items)}</div>`;
    })
    .join("");
}

// (시간별)(테마별) 버튼 눌림 상태 표시
function renderModeButtons() {
  document.querySelectorAll("#viewMode [data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === liveMode));
}

// 뉴스 목록 위 상태 문구 (마지막 조회, 새 기사 수, 다음 갱신까지)
function meta() {
  const iv = +$("#iv").value;
  let m = `마지막 조회 ${lastAt ? hms(lastAt) : "-"}`;
  if (lastNew != null) m += lastNew ? ` · 새 기사 ${lastNew}건` : " · 새 기사 없음";
  if (dataAt) m += ` · 데이터 업데이트 ${dataAt.slice(11, 16)}`;
  if (iv) m += ` · 다음 갱신까지 ${Math.max(0, Math.ceil((nextAt - Date.now()) / 1000))}초`;
  $("#meta").textContent = m;
}

// 뉴스·시세·분석 한 번에 새로고침
function refreshAll() {
  loadNews(false);
  loadMarket();
  loadAnalysis();
}

// 자동 갱신 멈추기
function stopTimers() {
  clearInterval(timer);
  clearInterval(ticker);
  timer = ticker = null;
}

// 고른 갱신 주기로 자동 갱신 다시 시작 (0이면 갱신 안 함)
function setInterval_() {
  stopTimers();
  const iv = +$("#iv").value; // 갱신 주기(초)
  try { localStorage.setItem("refreshInterval", String(iv)); } catch (_) {}
  if (iv) {
    nextAt = Date.now() + iv * 1000;
    timer = setInterval(() => {
      refreshAll();
      nextAt = Date.now() + iv * 1000;
    }, iv * 1000);
    ticker = setInterval(meta, 1000);
  }
  meta();
}

// 실시간 탭을 열 때
function showLive() {
  loadMarket();
  loadAnalysis();
  loadNews(true); // 처음 조회한 시점의 뉴스
  setInterval_();
}

/* ---------- 화면 전환 ---------- */

// 선택한 탭에 맞는 화면만 보이게 하고 그린다
function route() {
  const live = topic === "실시간";
  const daily = topic === "요약";
  $("#summaryView").classList.toggle("hidden", live || daily);
  $("#dailyView").classList.toggle("hidden", !daily);
  $("#liveView").classList.toggle("hidden", !live);
  if (live) return showLive();
  stopTimers();
  if (daily) renderDaily();
  else renderSummaries();
}

// 탭(주제) 버튼 클릭
$("#chips").addEventListener("click", (e) => {
  const t = e.target.closest("[data-t]")?.dataset.t;
  if (!t) return;
  topic = t;
  renderChips();
  route();
});

// 추천 키워드 클릭 → 그 단어로 검색
$("#kw").addEventListener("click", (e) => {
  const k = e.target.closest("[data-k]")?.dataset.k;
  if (!k) return;
  $("#q").value = k;
  renderSummaries();
});

// 검색 버튼 / 엔터
$("#searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (topic === "실시간" || (topic === "요약" && q)) {
    // 실시간 탭, 또는 요약 탭에서 단어 검색 → 전체 탭에서 검색 결과를 보여준다
    topic = "전체";
    renderChips();
    route();
  } else if (topic === "요약") {
    renderDaily(); // 요약 탭에서 날짜만 바꾼 경우 → 그날의 하루 정리
  } else renderSummaries();
});

// 날짜를 고르면 바로 검색
$("#d").addEventListener("change", () => $("#searchForm").requestSubmit());

// 로고 클릭 → 처음 화면 (오늘, 전체)
$("#home").addEventListener("click", () => {
  $("#q").value = "";
  $("#d").value = "";
  topic = "전체";
  renderChips();
  route();
});

// 갱신 주기 변경
$("#iv").addEventListener("change", setInterval_);

// 지금 새로고침 버튼
$("#now").addEventListener("click", () => {
  refreshAll();
  setInterval_();
});

// (시간별)(테마별) 버튼
$("#viewMode").addEventListener("click", (e) => {
  const m = e.target.closest("[data-mode]")?.dataset.mode;
  if (!m) return;
  liveMode = m;
  try { localStorage.setItem("liveMode", m); } catch (_) {}
  renderModeButtons();
  drawFeed();
});

// 처음 열 때: 저장해 둔 설정을 불러오고 주제 목록을 받아 화면을 그린다
(async function init() {
  try {
    const saved = localStorage.getItem("refreshInterval");
    if (saved && $(`#iv option[value="${saved}"]`)) $("#iv").value = saved;
    const mode = localStorage.getItem("liveMode");
    if (mode === "time" || mode === "topic") liveMode = mode;
  } catch (_) {}
  renderModeButtons();
  const t = await api("/api/topics");
  TOPICS = t.topics;
  today = t.today;
  $("#d").max = today;
  renderChips();
  route();
})();
