"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSec = (ms) => (ms / 1000).toFixed(1).replace(".", ",") + " с";
const pct = (x) => Math.round(x * 100) + "%";

const STATUS_TEXT = { verified: "Проверено", likely: "Вероятно", unverified: "Не подтверждено" };

const state = { data: null, tab: "all", showHidden: false, source: null, timer: null, started: 0 };

/* ---------- Поиск ---------- */

const form = $("#search-form");
const input = $("#q");

form.addEventListener("submit", (e) => {
  e.preventDefault();
  hideSuggest();
  start(input.value.trim(), input.value.trim());
});

document.querySelectorAll(".chip[data-q]").forEach((b) =>
  b.addEventListener("click", () => { input.value = b.dataset.q; start(b.dataset.q, b.dataset.q); })
);

function start(query, label) {
  if (query.length < 2) { input.focus(); return; }
  if (state.source) state.source.close();
  const url = new URL(location.href);
  url.searchParams.set("q", label || query);
  history.replaceState(null, "", url);

  $("#go").disabled = true;
  $("#error").hidden = true;
  $("#result").hidden = true;
  $("#progress").hidden = false;
  $("#progress-title").textContent = `Собираем профиль: ${label || query}`;
  document.querySelectorAll(".stages li").forEach((li) => {
    li.className = "";
    li.querySelector(".st-detail").textContent = "";
  });

  state.started = performance.now();
  clearInterval(state.timer);
  state.timer = setInterval(() => { $("#timer").textContent = fmtSec(performance.now() - state.started); }, 100);

  let finished = false;
  const es = new EventSource(`/api/profile/stream?q=${encodeURIComponent(query)}`);
  state.source = es;

  es.addEventListener("stage", (ev) => {
    const d = JSON.parse(ev.data);
    const li = document.querySelector(`.stages li[data-stage="${d.stage}"]`);
    if (!li) return;
    li.className = d.state;
    li.querySelector(".st-detail").textContent = d.detail || "";
  });

  es.addEventListener("result", (ev) => {
    finished = true;
    es.close();
    const clientMs = performance.now() - state.started;
    stop();
    render(JSON.parse(ev.data), clientMs);
  });

  es.addEventListener("fail", (ev) => {
    finished = true;
    es.close();
    stop();
    showError(JSON.parse(ev.data));
  });

  es.onerror = () => {
    if (finished) return;
    es.close();
    stop();
    showError({ error: "network", message: "Соединение с сервером прервалось. Проверьте интернет и повторите поиск." });
  };
}

function stop() {
  clearInterval(state.timer);
  $("#go").disabled = false;
  $("#progress").hidden = true;
}

function showError(err) {
  const box = $("#error");
  let html = "";
  if (err.error === "not_found") {
    html = `<h2>Университет не найден</h2><p>${esc(err.message)}</p>`;
    if (err.suggestion) {
      html += `<p>Возможно, вы имели в виду: <button class="chip" data-q="${esc(err.suggestion)}">${esc(err.suggestion)}</button></p>`;
    }
    html += `<p>Подсказка: введите полное название или добавьте город, например «Satbayev University Almaty».</p>`;
  } else if (err.error === "source_unavailable") {
    html = `<h2>Источник данных недоступен</h2><p>${esc(err.message)}</p><p>Это внешняя проблема. Повторите запрос через минуту.</p>`;
  } else {
    html = `<h2>Не получилось собрать профиль</h2><p>${esc(err.message)}</p>`;
  }
  box.innerHTML = html;
  box.hidden = false;
  box.querySelectorAll(".chip[data-q]").forEach((b) =>
    b.addEventListener("click", () => { input.value = b.dataset.q; start(b.dataset.q, b.dataset.q); })
  );
}

/* ---------- Подсказки ---------- */

const suggestBox = $("#suggest");
let suggestTimer = null;
let suggestSeq = 0;
let activeIdx = -1;

input.addEventListener("input", () => {
  clearTimeout(suggestTimer);
  const q = input.value.trim();
  if (q.length < 3) { hideSuggest(); return; }
  suggestTimer = setTimeout(async () => {
    const seq = ++suggestSeq;
    try {
      const r = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      if (seq !== suggestSeq) return;
      showSuggest(d.items || []);
    } catch { hideSuggest(); }
  }, 350);
});

input.addEventListener("keydown", (e) => {
  const items = [...suggestBox.querySelectorAll("li")];
  if (suggestBox.hidden || !items.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    activeIdx = (activeIdx + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((li, i) => li.setAttribute("aria-selected", i === activeIdx));
  } else if (e.key === "Enter" && activeIdx >= 0) {
    e.preventDefault();
    items[activeIdx].click();
  } else if (e.key === "Escape") {
    hideSuggest();
  }
});

document.addEventListener("click", (e) => { if (!e.target.closest(".field-wrap")) hideSuggest(); });

function showSuggest(items) {
  activeIdx = -1;
  if (!items.length) { hideSuggest(); return; }
  suggestBox.innerHTML = items.map((it) =>
    `<li role="option" data-qid="${esc(it.qid)}" data-name="${esc(it.name)}">${esc(it.name)}<small>${esc(it.description)}</small></li>`
  ).join("");
  suggestBox.querySelectorAll("li").forEach((li) =>
    li.addEventListener("click", () => {
      input.value = li.dataset.name;
      hideSuggest();
      start(li.dataset.qid, li.dataset.name);
    })
  );
  suggestBox.hidden = false;
  input.setAttribute("aria-expanded", "true");
}

function hideSuggest() {
  suggestSeq++;
  suggestBox.hidden = true;
  input.setAttribute("aria-expanded", "false");
}

/* ---------- Профиль ---------- */

function render(data, clientMs) {
  state.data = data;
  state.tab = "all";
  state.showHidden = false;
  $("#show-hidden").checked = false;

  const u = data.university;
  $("#uni-name").textContent = u.name;
  $("#uni-desc").textContent = u.description || "";

  const facts = [u.city, u.country].filter(Boolean).join(", ");
  const dist = u.distance_to_center_km != null ? `до центра города ${String(u.distance_to_center_km).replace(".", ",")} км по прямой` : "";
  $("#uni-facts").textContent = [facts, dist].filter(Boolean).join("; ");

  const links = [];
  if (u.website) links.push(`<a href="${esc(u.website)}" target="_blank" rel="noopener">Официальный сайт</a>`);
  if (u.wikipedia_url) links.push(`<a href="${esc(u.wikipedia_url)}" target="_blank" rel="noopener">Википедия</a>`);
  links.push(`<a href="${esc(u.wikidata_url)}" target="_blank" rel="noopener">Wikidata ${esc(u.qid)}</a>`);
  if (u.lat != null) links.push(`<a href="https://www.openstreetmap.org/?mlat=${u.lat}&mlon=${u.lon}#map=16/${u.lat}/${u.lon}" target="_blank" rel="noopener">Кампус на карте</a>`);
  $("#uni-links").innerHTML = links.join("");

  const s = data.stats;
  const serverMs = data.timing.total_ms;
  const timeLabel = data.cached ? "Готово (из кэша)" : "Готово за";
  $("#stats").innerHTML = [
    [timeLabel, fmtSec(clientMs)],
    ["Проверено", `${s.verified} из ${s.shown}`],
    ["Найдено кандидатов", s.candidates],
    ["Отсеяно и дублей", s.excluded + s.duplicates + s.same_file_merged],
  ].map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#stats").title = data.cached
    ? `Профиль взят из кэша. Первоначальная сборка заняла ${fmtSec(data.timing.original_total_ms)}.`
    : `Время на сервере: ${fmtSec(serverMs)}`;

  const alt = $("#alternatives");
  if (data.alternatives?.length) {
    alt.innerHTML = "Не тот вуз? " + data.alternatives.map((a) =>
      `<button class="chip" data-qid="${esc(a.qid)}" data-name="${esc(a.name)}" title="${esc(a.description)}">${esc(a.name)}</button>`
    ).join("");
    alt.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => { input.value = b.dataset.name; start(b.dataset.qid, b.dataset.name); })
    );
    alt.hidden = false;
  } else {
    alt.hidden = true;
  }

  const warn = $("#warnings");
  if (data.warnings?.length) {
    warn.innerHTML = `<ul>${data.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
    warn.hidden = false;
  } else {
    warn.hidden = true;
  }

  $("#hidden-count").textContent = data.hidden.length ? `(${data.hidden.length})` : "(0)";
  $("#show-hidden").disabled = !data.hidden.length;

  renderTabs();
  renderGrid();
  renderSources();
  $("#result").hidden = false;
  $("#uni-name").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function currentPhotos() {
  const d = state.data;
  let list = d.photos;
  if (state.showHidden) list = list.concat(d.hidden);
  if (state.tab !== "all") list = list.filter((p) => p.category === state.tab);
  return list;
}

function renderTabs() {
  const d = state.data;
  const extra = state.showHidden ? d.hidden : [];
  const count = (k) => d.photos.filter((p) => p.category === k).length + extra.filter((p) => p.category === k).length;
  const all = d.photos.length + extra.length;
  const tabs = [{ key: "all", label: "Все", n: all }].concat(d.categories.map((c) => ({ key: c.key, label: c.label, n: count(c.key) })));
  $("#tabs").innerHTML = tabs.map((t) =>
    `<button class="tab" role="tab" data-tab="${t.key}" aria-selected="${t.key === state.tab}" ${t.n === 0 && t.key !== "all" ? "disabled" : ""}>${esc(t.label)}<span class="n">${t.n}</span></button>`
  ).join("");
  $("#tabs").querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => { state.tab = b.dataset.tab; renderTabs(); renderGrid(); })
  );
}

function stampHTML(p, extraClass = "") {
  return `<span class="stamp ${p.status} ${extraClass}">
    <span>${p.status === "verified" ? "✓ " : ""}${STATUS_TEXT[p.status]} ${pct(p.confidence)}</span>
    <span class="bar"><i style="width:${pct(p.confidence)}"></i></span>
  </span>`;
}

function sourceLine(p) {
  const src = p.source || {};
  const detail = src.detail ? `: ${src.detail}` : "";
  return `Источник: <a href="${esc(src.url || p.page)}" target="_blank" rel="noopener">${esc(src.label)}${esc(detail)}</a>`;
}

function renderGrid() {
  const list = currentPhotos();
  const grid = $("#grid");
  const empty = $("#empty");
  if (!list.length) {
    grid.innerHTML = "";
    empty.textContent = state.tab === "all"
      ? "Проверенных фото не нашлось. Мы не подставляем снимки других мест: попробуйте другое написание названия или включите показ непроверенных."
      : "В этой категории нет проверенных фото.";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  grid.innerHTML = list.map((p, i) => `
    <figure class="photo is-${p.status}">
      <button class="open" data-i="${i}" aria-label="Открыть фото: ${esc(p.title)}">
        <img src="${esc(p.thumb)}" alt="${esc(p.category_label)}: ${esc(p.description || p.title)}" loading="lazy" referrerpolicy="no-referrer">
        ${stampHTML(p)}
      </button>
      <figcaption>
        <span class="cat">${esc(p.category_label)}</span>
        <span class="src">${sourceLine(p)}</span>
        <span class="lic">${esc(p.license)}${p.author ? ", автор: " + esc(p.author) : ""}</span>
      </figcaption>
    </figure>`).join("");
  grid.querySelectorAll("button.open").forEach((b) =>
    b.addEventListener("click", () => openViewer(list[+b.dataset.i]))
  );
  grid.querySelectorAll("img").forEach((img) =>
    img.addEventListener("error", () => { img.closest(".photo").hidden = true; }, { once: true })
  );
}

$("#show-hidden").addEventListener("change", (e) => {
  state.showHidden = e.target.checked;
  renderTabs();
  renderGrid();
});

function renderSources() {
  const d = state.data;
  const rows = d.sources.map((s) =>
    `<tr><td>${esc(s.name)}</td><td class="${s.ok ? "ok" : "bad"}">${s.ok ? "ответил" : "недоступен"}</td><td>${s.count}</td><td>${esc(s.error || "")}</td></tr>`
  ).join("");
  const reasons = Object.entries(d.stats.excluded_reasons || {}).map(([k, v]) => `<li>${esc(k)}: ${v}</li>`).join("");
  const stages = { resolve: "Поиск вуза", search: "Источники", verify: "Проверка и загрузка превью", dedup: "Дубликаты", categorize: "AI и категории" };
  const timing = Object.entries(d.timing.stages).map(([k, v]) => `<li>${esc(stages[k] || k)}: ${fmtSec(v)}</li>`).join("");
  $("#sources-detail").innerHTML = `
    <div class="tbl"><table>
      <thead><tr><th>Источник</th><th>Статус</th><th>Файлов</th><th>Комментарий</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p>Одинаковых файлов из разных выборок объединено: ${d.stats.same_file_merged}; визуальных дубликатов удалено: ${d.stats.duplicates}.</p>
    ${reasons ? `<p>Отсеяно:</p><ul>${reasons}</ul>` : ""}
    <p>${esc(d.method)}.</p>
    ${timing ? `<p>Время на сервере по этапам:</p><ul>${timing}</ul>` : ""}
    <p>Статусы: «Проверено» от 75%, «Вероятно» от 50%. Показатель складывается из сигналов, перечисленных в карточке каждого фото.</p>`;
}

/* ---------- Просмотр ---------- */

const viewer = $("#viewer");

function openViewer(p) {
  $("#v-img").src = p.thumb;
  $("#v-img").alt = p.description || p.title;
  $("#v-stamp").innerHTML = stampHTML(p, "static");
  $("#v-title").textContent = p.title;
  $("#v-cat").textContent = `${p.category_label}. ${p.category_reason}`;
  $("#v-reasons").innerHTML = p.reasons.map((r) => {
    const cls = r.delta >= 0 ? "plus" : "minus";
    const sign = r.delta >= 0 ? "+" : "−";
    return `<li><span class="d ${cls}">${sign}${Math.round(Math.abs(r.delta) * 100)}</span><span>${esc(r.text)}</span></li>`;
  }).join("");
  const meta = [];
  if (p.author) meta.push(`Автор: ${esc(p.author)}`);
  if (p.license) meta.push(p.license_url ? `Лицензия: <a href="${esc(p.license_url)}" target="_blank" rel="noopener">${esc(p.license)}</a>` : `Лицензия: ${esc(p.license)}`);
  if (p.date) meta.push(`Дата съёмки: ${esc(p.date)}`);
  if (p.duplicates_removed) meta.push(`Скрыто похожих копий: ${p.duplicates_removed}`);
  if (p.description) meta.push(esc(p.description));
  $("#v-meta").innerHTML = meta.join("<br>");
  const all = (p.all_sources || []).map((s) =>
    `<a href="${esc(s.url || p.page)}" target="_blank" rel="noopener">${esc(s.label)}${s.detail ? ": " + esc(s.detail) : ""}</a>`
  );
  all.unshift(`<a href="${esc(p.page)}" target="_blank" rel="noopener">Страница файла</a>`);
  $("#v-links").innerHTML = all.join("");
  viewer.showModal();
}

viewer.addEventListener("click", (e) => { if (e.target === viewer) viewer.close(); });

/* ---------- Старт ---------- */

fetch("/api/health").then((r) => r.json()).then((d) => {
  $("#ai-status").textContent = d.vision
    ? "AI-анализ изображений включён (Google Gemini): он помогает определить категорию и отсеять нерелевантные снимки."
    : "AI-анализ изображений сейчас выключен: проверка идёт по источнику, геометке, тексту и лицензии.";
}).catch(() => {});

const initial = new URL(location.href).searchParams.get("q");
if (initial) { input.value = initial; start(initial, initial); }
