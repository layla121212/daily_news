"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const hl = (s, q) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s));
const fmtDate = (d) => { const [y, m, dd] = d.split("-"); return `${y}년 ${+m}월 ${+dd}일`; };
const n2 = (v) => (v == null ? "-" : v.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const hms = (t) => t.toTimeString().slice(0, 8);

// 서버 모드: FastAPI의 /api 호출. 정적 모드(GitHub Pages): data/*.json 파일을 읽어 같은 모양으로 돌려준다.
const STATIC = document.documentElement.dataset.mode === "static";
const SEARCH_DAYS = 7; // 정적 모드에서 날짜 없이 검색할 때 살펴볼 최근 일수

async function api(path, params = {}) {
  if (STATIC) return staticApi(path, params);
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const r = await fetch(`${path}${qs.toString() ? "?" + qs : ""}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

let META = null;
const jsonCache = new Map();
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
async function loadMeta() {
  META = await getJSON("meta.json", true);
  return META;
}
const has = (s, q) => String(s || "").includes(q);

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

let TOPICS = {};
let today = "";
let topic = "전체";

/* ---------- 주제 버튼 ---------- */
function renderChips() {
  const names = ["전체", "실시간", ...Object.keys(TOPICS)];
  $("#chips").innerHTML = names
    .map((k) => `<button class="chip ${k === "실시간" ? "live" : ""} ${k === topic ? "on" : ""}" data-t="${esc(k)}">${esc(k)}</button>`)
    .join("");
  const kws = TOPICS[topic] || [];
  $("#kw").innerHTML = kws.length ? "추천 키워드: " + kws.map((k) => `<button data-k="${esc(k)}">#${esc(k)}</button>`).join(" ") : "";
}

function groupBy(list, key) {
  const m = new Map();
  for (const x of list) {
    const k = key(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(x);
  }
  return m;
}
// 화면 위 주제 버튼 순서대로 (목록에 없는 주제는 뒤로)
function topicOrder(map) {
  const order = Object.keys(TOPICS);
  return [...map.keys()].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
}

/* ---------- 요약 화면 ---------- */
async function renderSummaries() {
  const q = $("#q").value.trim();
  const d = $("#d").value;
  const t = topic === "전체" ? "" : topic;
  $("#status").textContent = "불러오는 중…";
  let data;
  try {
    data = await api("/api/summaries", { q, date: d, topic: t });
  } catch (e) {
    $("#status").textContent = `오류: ${e.message}`;
    return;
  }
  $("#title").textContent = data.date ? `${fmtDate(data.date)}의 뉴스요약` : `"${q}" 검색 결과`;
  const filters = [q && `"${q}"`, t, d && fmtDate(d)].filter(Boolean);
  $("#status").textContent = filters.length
    ? `요약 ${data.items.length}건 · ${filters.join(" · ")}` + (data.searched_days ? ` · 최근 ${data.searched_days}일에서 검색 (날짜를 고르면 그날 전체를 찾아요)` : "")
    : "";

  if (!data.items.length) {
    $("#list").innerHTML = `<div class="empty">${
      filters.length ? "조건에 맞는 요약이 없어요." : `아직 오늘의 요약이 없어요. 매시 정각이 지나면 직전 1시간의 뉴스가 요약돼요${STATIC ? " (반영까지 10~20분 걸릴 수 있어요)" : ""}.`
    }</div>`;
  } else {
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

/* ---------- 실시간 화면 ---------- */
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
function chg(x) {
  if (x.change == null) return `<span class="chg muted">-</span>`;
  const up = x.change >= 0;
  return `<span class="chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${n2(Math.abs(x.change))} (${up ? "+" : ""}${n2(x.change_pct)}%)</span>`;
}

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

let feed = [];
let lastId = 0;
let lastAt = null;
let lastNew = null; // 마지막 새로고침에서 새로 들어온 기사 수
let dataAt = null; // 정적 모드: 서버 데이터가 마지막으로 갱신된 시각
let timer = null;
let ticker = null;
let nextAt = 0;

async function loadNews(initial) {
  let r;
  try {
    r = await api("/api/live/news", { after_id: initial ? 0 : lastId, limit: 200 });
  } catch (e) {
    $("#meta").textContent = `뉴스 조회 실패: ${e.message}`;
    return;
  }
  const fresh = new Set(initial ? [] : r.items.map((a) => a.id));
  const known = new Set(feed.map((a) => a.id));
  feed = (initial ? r.items : [...r.items.filter((a) => !known.has(a.id)), ...feed])
    .sort((a, b) => b.published_at.localeCompare(a.published_at) || b.id - a.id)
    .slice(0, 300);
  lastNew = initial ? null : r.items.length;
  dataAt = r.data_updated_at || null;
  if (feed.length) lastId = Math.max(lastId, ...feed.map((a) => a.id));
  lastAt = new Date();
  $("#feed").innerHTML = feed.length ? renderFeed(feed, fresh) : `<div class="empty">아직 수집된 뉴스가 없어요.</div>`;
  meta();
}

const LIVE_STORIES_PER_TOPIC = 5;

// 테마별로 묶고, 테마 안에서는 같은 소식을 하나로 합친다 (최신 기사가 대표)
function renderFeed(list, fresh) {
  const byTopic = groupBy(list, (a) => a.topic);
  return topicOrder(byTopic)
    .map((t) => {
      const stories = [...groupBy(byTopic.get(t), (a) => a.story ?? a.id).values()].slice(0, LIVE_STORIES_PER_TOPIC);
      const newCount = byTopic.get(t).filter((a) => fresh.has(a.id)).length;
      return `<section class="tgroup"><div class="thead">${esc(t)}${newCount ? ` <span class="badge">새 기사 ${newCount}</span>` : ""}</div>${stories
        .map((g) => {
          const a = g[0];
          const isNew = g.some((x) => fresh.has(x.id));
          const more = g.slice(1);
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

function meta() {
  const iv = +$("#iv").value;
  let m = `마지막 조회 ${lastAt ? hms(lastAt) : "-"}`;
  if (lastNew != null) m += lastNew ? ` · 새 기사 ${lastNew}건` : " · 새 기사 없음";
  if (dataAt) m += ` · 데이터 업데이트 ${dataAt.slice(11, 16)}`;
  if (iv) m += ` · 다음 갱신까지 ${Math.max(0, Math.ceil((nextAt - Date.now()) / 1000))}초`;
  $("#meta").textContent = m;
}

function refreshAll() {
  loadNews(false);
  loadMarket();
  loadAnalysis();
}

function stopTimers() {
  clearInterval(timer);
  clearInterval(ticker);
  timer = ticker = null;
}

function setInterval_() {
  stopTimers();
  const iv = +$("#iv").value;
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

function showLive() {
  loadMarket();
  loadAnalysis();
  loadNews(true); // 처음 조회한 시점의 뉴스
  setInterval_();
}

/* ---------- 화면 전환 ---------- */
function route() {
  const live = topic === "실시간";
  $("#summaryView").classList.toggle("hidden", live);
  $("#liveView").classList.toggle("hidden", !live);
  if (live) showLive();
  else {
    stopTimers();
    renderSummaries();
  }
}

$("#chips").addEventListener("click", (e) => {
  const t = e.target.closest("[data-t]")?.dataset.t;
  if (!t) return;
  topic = t;
  renderChips();
  route();
});
$("#kw").addEventListener("click", (e) => {
  const k = e.target.closest("[data-k]")?.dataset.k;
  if (!k) return;
  $("#q").value = k;
  renderSummaries();
});
$("#searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  if (topic === "실시간") {
    topic = "전체";
    renderChips();
    route();
  } else renderSummaries();
});
$("#d").addEventListener("change", () => $("#searchForm").requestSubmit());
$("#home").addEventListener("click", () => {
  $("#q").value = "";
  $("#d").value = "";
  topic = "전체";
  renderChips();
  route();
});
$("#iv").addEventListener("change", setInterval_);
$("#now").addEventListener("click", () => {
  refreshAll();
  setInterval_();
});

(async function init() {
  try {
    const saved = localStorage.getItem("refreshInterval");
    if (saved && $(`#iv option[value="${saved}"]`)) $("#iv").value = saved;
  } catch (_) {}
  const t = await api("/api/topics");
  TOPICS = t.topics;
  today = t.today;
  $("#d").max = today;
  renderChips();
  route();
})();
