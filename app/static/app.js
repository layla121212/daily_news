"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const hl = (s, q) => (q ? esc(s).split(esc(q)).join(`<mark>${esc(q)}</mark>`) : esc(s));
const fmtDate = (d) => { const [y, m, dd] = d.split("-"); return `${y}년 ${+m}월 ${+dd}일`; };
const n2 = (v) => (v == null ? "-" : v.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const hms = (t) => t.toTimeString().slice(0, 8);

async function api(path, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const r = await fetch(`${path}${qs.toString() ? "?" + qs : ""}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
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
  $("#status").textContent = filters.length ? `요약 ${data.items.length}건 · ${filters.join(" · ")}` : "";

  if (!data.items.length) {
    $("#list").innerHTML = `<div class="empty">${
      filters.length ? "조건에 맞는 요약이 없어요." : "아직 오늘의 요약이 없어요. 매시 5분에 직전 1시간의 뉴스가 요약돼요."
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
        return `<div class="hour"><div class="time">${esc(label)}</div><div class="card">${items
          .map(
            (s) => `<div class="item"><span class="tag">${esc(s.topic)}</span>
          <h3>${hl(s.title, q)}</h3><p>${hl(s.summary, q)}</p>
          <details class="src"><summary>요약에 쓰인 원문 ${s.articles.length}건</summary><ul>${s.articles
            .map((a) => `<li><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${hl(a.title, q)}</a> · ${esc(a.source)}</li>`)
            .join("")}</ul></details></div>`
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
  $("#liveLead").textContent = `${m.updated_at.slice(0, 16).replace("T", " ")} 기준 시세` + (m.errors.length ? ` · 일부 시세 조회 실패: ${m.errors.join(", ")}` : "");
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
let timer = null;
let ticker = null;
let nextAt = 0;

async function loadNews(initial) {
  let r;
  try {
    r = await api("/api/live/news", { after_id: initial ? 0 : lastId, limit: 30 });
  } catch (e) {
    $("#meta").textContent = `뉴스 조회 실패: ${e.message}`;
    return;
  }
  const fresh = new Set(initial ? [] : r.items.map((a) => a.id));
  feed = (initial ? r.items : [...r.items, ...feed]).slice(0, 50);
  if (feed.length) lastId = Math.max(lastId, ...feed.map((a) => a.id));
  lastAt = new Date();
  $("#feed").innerHTML = feed.length
    ? feed
        .map(
          (a) => `<div class="row ${fresh.has(a.id) ? "new" : ""}"><div class="t">${esc(a.published_at.slice(11, 16))}</div><div>
        <a class="ti" href="${safeUrl(a.url)}" target="_blank" rel="noopener"><span class="tag">${esc(a.topic)}</span>${esc(a.title)}</a>${
            fresh.has(a.id) ? '<span class="badge">NEW</span>' : ""
          }
        <div class="s">${esc(a.description.slice(0, 120))} <span class="muted">· ${esc(a.source)}</span></div></div></div>`
        )
        .join("")
    : `<div class="empty">아직 수집된 뉴스가 없어요.</div>`;
  meta();
}

function meta() {
  const iv = +$("#iv").value;
  let m = `마지막 조회 ${lastAt ? hms(lastAt) : "-"}`;
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
