"use strict";

const $ = (id) => document.getElementById(id);
const statuses = {
  critical_decay: ["Critical Decay", "#dc2626"],
  moderate_decay: ["Moderate Decay", "#d97706"],
  early_warning: ["Early Warning", "#a09324"],
  stable: ["Stable", "#94a3b8"],
  growing: ["Growing", "#16a34a"],
};
const drivers = {
  ranking_drop: "Потеря позиций", ctr_erosion: "Падение CTR", demand_shift: "Сдвиг спроса",
  mixed_decay: "Смешанная деградация", healthy: "Стабильный контент",
};
const strategies = {
  internal_linking_reanchor: "Усилить внутренние ссылки",
  canonical_or_deoptimize: "Развести интенты",
  "301_redirect_and_merge": "Объединить и настроить 301",
};
const datasets = { pages: "Страницы", queries: "Запросы" };
const integers = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const decimals = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const percents = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 2 });
const percentages = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" });
const compact = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const state = { projects: [], project: null, data: null, controller: null, charts: [], pending: false,
  actionSort: { field: "priority_score", order: -1 } };

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

const amount = (value) => escapeHTML(`${decimals.format(value)} ${state.project.currency}`);
const int = (value) => integers.format(value);
const dec = (value) => decimals.format(value);
const pct = (value) => percents.format(value);
const signed = (value, formatter = int) => (value > 0 ? "+" : "") + formatter(value);
const percentPoints = (value) => `${signed(value * 100, dec)} п.п.`;
const urlText = (value) => `<span class="url">${escapeHTML(value)}</span>`;
const empty = (message) => `<p class="empty">${escapeHTML(message)}</p>`;
const searchText = (value) => value.trim().toLowerCase();
const projectURL = (id = state.project?.id) => `/api/projects/${id}`;

async function request(path, { json, ...options } = {}) {
  if (json !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(json);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    const detail = Array.isArray(failure.detail)
      ? failure.detail.map((issue) => issue.msg).join(" · ") : failure.detail;
    throw new Error(detail || `Ошибка запроса (${response.status})`);
  }
  return response.json();
}

function notice(text = "", error = false, id = "global-message") {
  const node = $(id);
  node.hidden = !text;
  node.textContent = text;
  node.classList.toggle("error", error);
}

function setControls() {
  const ready = Boolean(state.project && state.data) && !state.pending;
  $("project-select").disabled = state.pending || !state.projects.length;
  $("reset-demos").disabled = state.pending;
  $("new-project-button").disabled = state.pending;
  for (const id of ["economics-form", "import-form"]) {
    for (const control of $(id).elements) control.disabled = !ready;
  }
  for (const format of ["xlsx", "csv", "md"]) {
    const link = $(`export-${format}`);
    link.setAttribute("aria-disabled", String(!ready));
    if (ready) link.href = `${projectURL()}/export/${format}`;
    else link.removeAttribute("href");
  }
}

function memoryProject(value) {
  try {
    if (value === undefined) return Number(localStorage.getItem("seo-audit-project"));
    localStorage.setItem("seo-audit-project", String(value));
  } catch {
    return null;
  }
  return null;
}

function setOptions(node, items, firstLabel, preserve = true) {
  const value = preserve ? node.value : "";
  node.replaceChildren(new Option(firstLabel, ""), ...items.map(([key, text]) => new Option(text, key)));
  node.value = items.some(([key]) => String(key) === value) ? value : "";
}

async function loadProjects(preferred = state.project?.id ?? memoryProject()) {
  state.projects = await request("/api/projects");
  const selected = state.projects.find((project) => project.id === preferred) || state.projects[0];
  $("project-select").replaceChildren(...(selected
    ? state.projects.map((project) => new Option(project.name, project.id))
    : [new Option("Нет проектов", "")]));
  if (selected) await loadProject(selected, true);
  else {
    state.project = null;
    state.data = null;
    $("workspace").hidden = true;
    $("workspace-empty").hidden = false;
    $("workspace-empty").textContent = "Создайте проект или восстановите демо-данные.";
    setControls();
  }
}

function showProject(project, switching) {
  $("project-select").value = project.id;
  $("project-domain").textContent = project.domain;
  $("demo-label").hidden = !project.is_demo;
  $("economics-currency").textContent = `Параметры ROI · ${project.currency}`;
  const controls = $("economics-form").elements;
  controls.default_cpc.value = project.default_cpc;
  controls.conversion_rate.value = Number((project.conversion_rate * 100).toPrecision(15));
  controls.avg_lead_value.value = project.avg_lead_value;
  if (switching) {
    for (const id of ["action-search", "action-min", "decay-group", "decay-status",
      "decay-search", "cluster-search", "quarantine-batch", "quarantine-dataset"]) $(id).value = "";
    $("import-form").reset();
  }
}

async function loadProject(project, switching = false) {
  state.controller?.abort();
  const controller = new AbortController();
  state.controller = controller;
  state.project = project;
  memoryProject(project.id);
  if (switching) {
    state.data = null;
    clearReports();
  }
  $("workspace").hidden = false;
  $("workspace-empty").hidden = true;
  $("workspace").setAttribute("aria-busy", "true");
  showProject(project, switching);
  setControls();

  try {
    const endpoints = ["summary", "decay-pages", "ctr-opportunities", "cannibalization",
      "action-plan", "import/batches", "quarantine"];
    const [summary, pages, opportunities, clusters, plan, batches, quarantine] = await Promise.all(
      endpoints.map((path) => request(`${projectURL(project.id)}/${path}`, { signal: controller.signal })),
    );
    if (state.controller !== controller) return;
    state.data = { summary, pages, opportunities, clusters, plan, batches, quarantine };
    renderWorkspace();
  } catch (error) {
    if (error.name !== "AbortError" && state.controller === controller) {
      notice(`Не удалось обновить отчёты. ${error.message}`, true);
      if (!state.data) clearReports("Данные отчёта недоступны.");
    }
  } finally {
    if (state.controller === controller) {
      $("workspace").setAttribute("aria-busy", "false");
      setControls();
    }
  }
}

function clearReports(message = "Загрузка данных…") {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
  for (const id of ["kpis", "traffic-chart", "status-chart", "action-table", "decay-table",
    "ctr-table", "cluster-list", "quarantine-table"]) $(id).innerHTML = empty(message);
  for (const id of ["status-legend", "action-count", "decay-count", "last-batch"]) $(id).textContent = "";
}

function renderWorkspace() {
  const { summary, pages, opportunities, batches } = state.data;
  const groups = [...new Set(pages.map((page) => page.content_group).filter(Boolean))].sort();
  setOptions($("decay-group"), groups.map((group) => [group, group]), "Все группы");
  setOptions($("quarantine-batch"), batches.map((batch) => [batch.id, `#${batch.id} · ${batch.filename}`]), "Все загрузки");
  renderKpis(summary);
  renderCharts();
  renderAction();
  renderDecay();
  renderCtr(opportunities);
  renderClusters();
  renderBatch(batches[0]);
  renderQuarantine();
}

function renderKpis(summary) {
  const counts = summary.pages_by_status;
  const changeClass = summary.clicks_change_pct < 0 ? "negative" : summary.clicks_change_pct > 0 ? "positive" : "";
  const cards = [
    ["Органические клики", int(summary.curr_clicks),
      `Было ${int(summary.prev_clicks)} · <b class="${changeClass}">${percentages.format(summary.clicks_change_pct)}</b>`],
    ["Страницы в деградации", int(counts.critical_decay + counts.moderate_decay),
      `Критических ${counts.critical_decay} · умеренных ${counts.moderate_decay}`],
    ["Точки роста CTR", int(summary.ctr_opportunities_count), "Страницы в топ-10"],
    ["Кластеры каннибализации", int(summary.cannibalization_clusters_count), "Запросы с конкурирующими URL"],
    ["Возвращаемый трафик", int(summary.recoverable_clicks), `Клики · ${dec(summary.projected_leads)} прогнозных лидов`],
    ["Прогноз выручки", amount(summary.projected_revenue_impact),
      `Экв. рекламы ${amount(summary.equivalent_ad_value)}`, "money-kpi"],
  ];
  $("kpis").innerHTML = cards.map(([title, value, detail, kind = ""]) =>
    `<article class="surface kpi ${kind}"><p>${title}</p><strong>${value}</strong><span>${detail}</span></article>`).join("");
}

function makeChart(holder, config, label) {
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", label);
  holder.replaceChildren(canvas);
  const chart = new Chart(canvas, config);
  state.charts.push(chart);
}

function trafficFallback(holder, rows) {
  const max = Math.max(...rows.flatMap((row) => [row.lost, row.recovered]), 1);
  holder.innerHTML = `<svg class="traffic-svg" viewBox="0 0 620 ${rows.length * 23 + 20}" role="img" aria-label="Потери и возврат кликов по страницам" preserveAspectRatio="xMinYMin meet">
    ${[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
      const x = 205 + 365 * fraction;
      return `<line x1="${x}" y1="0" x2="${x}" y2="${rows.length * 23}" stroke="#e2e8f0"/><text x="${x}" y="${rows.length * 23 + 15}" text-anchor="middle">${int(max * fraction)}</text>`;
    }).join("")}
    ${rows.map((row, index) => {
      const y = 23 * index;
      return `<g><title>${escapeHTML(row.url)} · потеряно ${int(row.lost)} · можно вернуть ${int(row.recovered)}</title>
        <text x="0" y="${y + 14}">${escapeHTML(row.label)}</text>
        <rect x="205" y="${y + 3}" width="${365 * row.lost / max}" height="6" rx="2" fill="#94a3b8"/>
        <rect x="205" y="${y + 11}" width="${365 * row.recovered / max}" height="6" rx="2" fill="#1e40af"/>
      </g>`;
    }).join("")}</svg>`;
  const chartTable = document.createElement("div");
  chartTable.className = "traffic-table";
  chartTable.innerHTML = table("Потери и возврат трафика", ["URL", "Потеряно", "Вернём"],
    rows.map((row) => `<tr><td>${urlText(row.url)}</td><td class="number">${int(row.lost)}</td><td class="number">${int(row.recovered)}</td></tr>`), "");
  holder.append(chartTable);
}

function statusFallback(holder, counts, colors, total) {
  let offset = 0;
  const segments = counts.map((count, index) => {
    const fraction = total ? 100 * count / total : 0;
    const segment = fraction ? `<circle cx="80" cy="80" r="62" pathLength="100" stroke="${colors[index]}"
      stroke-dasharray="${fraction} ${100 - fraction}" stroke-dashoffset="${-offset}"/>` : "";
    offset += fraction;
    return segment;
  });
  holder.innerHTML = `<svg viewBox="0 0 160 160" role="img" aria-label="Распределение статусов страниц">
    <g fill="none" stroke-width="16" transform="rotate(-90 80 80)">
      <circle cx="80" cy="80" r="62" stroke="#e2e8f0"/>${segments.join("")}
    </g></svg>`;
}

function renderCharts() {
  if (!state.data) return;
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
  const { pages, summary, plan } = state.data;
  const recovery = new Map(plan.map((row) => [row.url, row.recoverable_clicks]));
  const rows = pages.filter((page) => page.clicks_delta < 0)
    .sort((a, b) => a.clicks_delta - b.clicks_delta || a.url.localeCompare(b.url)).slice(0, 8)
    .map((page) => ({
      url: page.url, label: page.url.length > 30 ? `…${page.url.slice(-28)}` : page.url,
      lost: -page.clicks_delta, recovered: recovery.get(page.url) || 0,
    }));
  const names = Object.keys(statuses);
  const counts = names.map((name) => summary.pages_by_status[name] || 0);
  const colors = names.map((name) => statuses[name][1]);
  $("status-legend").innerHTML = names.map((name, i) =>
    `<div><span><i class="swatch" style="background:${colors[i]}"></i>${statuses[name][0]}</span><strong>${int(counts[i])}</strong></div>`).join("");

  const bar = $("traffic-chart");
  const donut = $("status-chart");
  if (typeof Chart !== "undefined") {
    try {
      if (rows.length) {
        makeChart(bar, {
          type: "bar",
          data: { labels: rows.map((row) => row.label), datasets: [
            { label: "Потеряно", data: rows.map((row) => row.lost), backgroundColor: "#94a3b8", borderRadius: 3, maxBarThickness: 7 },
            { label: "Можно вернуть", data: rows.map((row) => row.recovered), backgroundColor: "#1e40af", borderRadius: 3, maxBarThickness: 7 },
          ] },
          options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: false,
            scales: { y: { grid: { display: false }, ticks: { autoSkip: false, font: { size: 10, family: "monospace" } } },
              x: { beginAtZero: true, ticks: { callback: (value) => compact.format(value), maxTicksLimit: 5 } } },
            plugins: { legend: { display: false }, tooltip: { callbacks: {
              title: ([item]) => rows[item.dataIndex].url,
            } } } },
        }, "Потери и возврат кликов по страницам");
      }
      if (summary.pages_count) {
        makeChart(donut, {
          type: "doughnut",
          data: { labels: names.map((name) => statuses[name][0]), datasets: [
            { data: counts, backgroundColor: colors, borderWidth: 1, borderColor: "#fff" },
          ] },
          options: { cutout: "76%", responsive: true, animation: false, plugins: { legend: { display: false } } },
        }, "Распределение статусов страниц");
      }
    } catch {
      state.charts.forEach((chart) => chart.destroy());
      state.charts = [];
    }
  }
  if (!rows.length) bar.innerHTML = empty("Страниц с потерей кликов нет.");
  else if (!state.charts.some((chart) => chart.canvas.parentElement === bar)) trafficFallback(bar, rows);
  if (!state.charts.some((chart) => chart.canvas.parentElement === donut)) {
    statusFallback(donut, counts, colors, summary.pages_count);
  }
  donut.insertAdjacentHTML("beforeend", `<div class="donut-value"><strong>${int(summary.pages_count)}</strong>страниц</div>`);
}

function table(label, headers, rows, message) {
  if (!rows.length) return empty(message);
  return `<div class="table-scroll" tabindex="0" role="region" aria-label="${escapeHTML(label)}"><table>
    <thead><tr>${headers.map((header) => typeof header === "string" ? `<th scope="col">${header}</th>` : header.html).join("")}</tr></thead>
    <tbody>${rows.join("")}</tbody></table></div>`;
}

function milestonePage(page, url) {
  return `<td class="title-cell">${page?.page_title ? `<strong>${escapeHTML(page.page_title)}</strong><br>` : ""}${urlText(url)}
    ${page?.content_group ? `<span class="detail">${escapeHTML(page.content_group)}</span>` : ""}</td>`;
}

const badge = (status) => `<span class="badge ${escapeHTML(status)}">${escapeHTML(statuses[status]?.[0] || status)}</span>`;
const score = (value, label, type = "") => `<span class="score ${type}"><strong class="number">${dec(value)}</strong>
  <progress max="100" value="${value}" aria-label="${label}"></progress></span>`;

function sortable(label, field) {
  const active = state.actionSort.field === field;
  const down = state.actionSort.order < 0;
  return { html: `<th scope="col" aria-sort="${active ? (down ? "descending" : "ascending") : "none"}"><button type="button"
    data-sort="${field}">${label} ${active ? (down ? "↓" : "↑") : "↕"}</button></th>` };
}

function matchesPage(page, term, url) {
  return url.toLowerCase().includes(term) || (page?.page_title || "").toLowerCase().includes(term);
}

function renderAction() {
  if (!state.data) return;
  const { plan, pages } = state.data;
  const byUrl = new Map(pages.map((row) => [row.url, row]));
  const term = searchText($("action-search").value);
  const threshold = $("action-min").valueAsNumber || 0;
  const { field, order } = state.actionSort;
  const filtered = plan.filter((row) => row.priority_score >= threshold && matchesPage(byUrl.get(row.url), term, row.url));
  filtered.sort((a, b) => order * (a[field] - b[field]) || a.url.localeCompare(b.url));
  $("action-count").textContent = `${int(filtered.length)} из ${int(plan.length)} задач`;
  $("action-table").innerHTML = table("План рерайта", [
    sortable("Приоритет", "priority_score"), "Страница", "Статус", sortable("Decay Score", "decay_score"),
    sortable("Вернём кликов", "recoverable_clicks"), sortable("Прогноз выручки", "projected_revenue_impact"), "Рекомендуемое действие",
  ], filtered.map((row) => {
    const page = byUrl.get(row.url);
    return `<tr><td>${score(row.priority_score, "Priority Score")}</td>${milestonePage(page, row.url)}
      <td>${badge(row.decay_status)}</td><td>${score(row.decay_score, "Decay Score", "decay")}</td>
      <td class="number">${int(row.recoverable_clicks)}</td><td class="number"><strong>${amount(row.projected_revenue_impact)}</strong>
      <span class="detail">Экв. рекламы ${amount(row.equivalent_ad_value)}</span></td>
      <td class="recommendation"><strong>${escapeHTML(drivers[page?.decay_driver] || "Аудит страницы")}</strong>
      <span class="detail">${escapeHTML(page?.recommended_action)}</span></td></tr>`;
  }), "По текущим фильтрам задач нет.");
}

function renderDecay() {
  if (!state.data) return;
  const { pages } = state.data;
  const status = $("decay-status").value;
  const group = $("decay-group").value;
  const term = searchText($("decay-search").value);
  const filtered = pages.filter((page) => (!status || page.decay_status === status) &&
    (!group || page.content_group === group) && matchesPage(page, term, page.url));
  $("decay-count").textContent = `${int(filtered.length)} из ${int(pages.length)} страниц`;
  $("decay-table").innerHTML = table("Деградация страниц", [
    "Страница", "Статус", "Decay Score", "Клики", "Показы", "CTR", "Позиция",
  ], filtered.map((row) => `<tr>${milestonePage(row, row.url)}<td>${badge(row.decay_status)}</td>
    <td>${score(row.decay_score, "Decay Score", "decay")}</td>
    <td class="number">${int(row.prev_clicks)} → ${int(row.curr_clicks)}<span class="detail">${signed(row.clicks_delta)} · ${percentages.format(row.clicks_change_pct)}</span></td>
    <td class="number">${int(row.prev_impressions)} → ${int(row.curr_impressions)}<span class="detail">${signed(row.impressions_delta)} · ${percentages.format(row.impressions_change_pct)}</span></td>
    <td class="number">${pct(row.prev_ctr)} → ${pct(row.curr_ctr)}<span class="detail">${percentPoints(row.ctr_delta)}</span></td>
    <td class="number">${dec(row.prev_position)} → ${dec(row.curr_position)}<span class="detail">${signed(row.position_delta, dec)}</span></td></tr>`), "По текущим фильтрам страниц нет.");
}

function renderCtr(rows) {
  $("ctr-table").innerHTML = table("Точки роста CTR", [
    "URL", "Позиция", "Показы", "Факт. CTR", "Бенчмарк", "Разрыв", "Доп. клики", "Snippet Score",
  ], rows.map((row) => `<tr><td class="title-cell">${urlText(row.url)}</td><td class="number">${dec(row.curr_position)}</td>
    <td class="number">${int(row.curr_impressions)}</td><td class="number">${pct(row.curr_ctr)}</td>
    <td class="number">${pct(row.expected_ctr)}</td><td class="number">${percentPoints(row.ctr_gap)}</td>
    <td class="number"><strong>+${int(row.potential_extra_clicks)}</strong></td><td>${score(row.snippet_opportunity_score, "Snippet Score")}</td></tr>`),
  "Точек роста CTR по текущим данным нет.");
}

function renderClusters() {
  if (!state.data) return;
  const term = searchText($("cluster-search").value);
  const rows = state.data.clusters.filter((cluster) => cluster.normalized_query.includes(term)
    || cluster.competing_urls.some((row) => row.url.toLowerCase().includes(term)));
  $("cluster-list").innerHTML = rows.length ? rows.map((row, index) =>
    `<details ${index === 0 ? "open" : ""}><summary><strong>${escapeHTML(row.normalized_query)}</strong>
      <span class="badge primary-tag">Primary</span>${urlText(row.primary_url)}
      <span class="cluster-summary-metrics"><span>Потеря ${int(row.estimated_lost_clicks)} кл.</span><b class="number">${dec(row.cannibalization_score)}</b></span>
    </summary><div class="cluster-recommendation"><strong>${escapeHTML(strategies[row.resolution_strategy] || row.resolution_strategy)}</strong>
      <p>${escapeHTML(row.resolution_recommendation)}</p></div>
    ${table("Конкурирующие URL", ["URL", "Клики", "Доля кликов", "Показы", "Доля показов", "Позиция", "CTR"],
      row.competing_urls.map((item) => `<tr><td>${item.is_primary ? '<span class="badge primary-tag">Primary</span> ' : ""}${urlText(item.url)}</td>
        <td class="number">${int(item.curr_clicks)}</td><td class="number">${pct(item.click_share)}</td>
        <td class="number">${int(item.curr_impressions)}</td><td class="number">${pct(item.impression_share)}</td>
        <td class="number">${dec(item.curr_position)}</td><td class="number">${pct(item.curr_ctr)}</td></tr>`), "")}
    </details>`).join("") : empty("Кластеров по текущему поиску нет.");
}

function renderBatch(batch) {
  $("last-batch").innerHTML = batch ? `<strong>Последняя загрузка</strong> · ${escapeHTML(batch.filename)} ·
    строк ${int(batch.total_rows)} · принято <strong>${int(batch.valid_rows)}</strong> · карантин <strong>${int(batch.quarantined_rows)}</strong>`
    : "Загрузок пока нет.";
}

function renderQuarantine() {
  if (!state.data) return;
  const batchId = $("quarantine-batch").value;
  const dataset = $("quarantine-dataset").value;
  const rows = state.data.quarantine.filter((row) => (!batchId || row.batch_id === Number(batchId))
    && (!dataset || row.dataset_type === dataset));
  $("quarantine-table").innerHTML = table("Строки в карантине", ["Строка", "Пакет", "Набор", "Причина", "Исходные поля"],
    rows.map((row) => `<tr><td class="number">${int(row.row_number)}</td><td class="number">#${int(row.batch_id)}</td>
      <td>${escapeHTML(datasets[row.dataset_type])}</td><td>${escapeHTML(row.error_reason)}</td>
      <td class="quarantine-table-payload"><code>${escapeHTML(JSON.stringify(row.raw_payload))}</code></td></tr>`),
    "В карантине нет строк по выбранным фильтрам.");
}

async function mutate(task, errorTarget = "global-message") {
  if (state.pending) return;
  state.pending = true;
  setControls();
  notice("", false, errorTarget);
  try {
    await task();
  } catch (error) {
    notice(error.message, true, errorTarget);
  } finally {
    state.pending = false;
    setControls();
  }
}

$("project-select").addEventListener("change", () => {
  const project = state.projects.find((item) => item.id === Number($("project-select").value));
  if (project) {
    notice();
    loadProject(project, true);
  }
});

$("reset-demos").addEventListener("click", () => mutate(async () => {
  const selected = state.project;
  const demos = await request("/api/projects/reset-demos", { method: "POST" });
  const replacement = demos.find((demo) => demo.slug === selected?.slug);
  await loadProjects(replacement?.id ?? (selected && !selected.is_demo ? selected.id : demos[0]?.id));
  if (state.data) notice("Демо-проекты восстановлены.");
}));

$("economics-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const { elements } = event.currentTarget;
  const json = {
    default_cpc: elements.default_cpc.valueAsNumber,
    conversion_rate: elements.conversion_rate.valueAsNumber / 100,
    avg_lead_value: elements.avg_lead_value.valueAsNumber,
  };
  mutate(async () => {
    const project = await request(projectURL(), { method: "PATCH", json });
    state.projects = state.projects.map((item) => item.id === project.id ? project : item);
    await loadProject(project);
    if (state.data && $("global-message").hidden) notice("Экономические показатели пересчитаны.");
  });
});

$("import-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const { dataset, preset, file } = form.elements;
  if (!file.files.length) return;
  const body = new FormData();
  body.append("file", file.files[0]);
  const path = `${projectURL()}/import/${dataset.value}?${new URLSearchParams({ preset: preset.value })}`;
  mutate(async () => {
    const batch = await request(path, { method: "POST", body });
    form.reset();
    await loadProject(state.project);
    if ($("global-message").hidden) notice(`Импорт завершён. Принято ${batch.valid_rows}, карантин ${batch.quarantined_rows}.`);
  });
});

const dialog = $("project-dialog");
$("new-project-button").addEventListener("click", () => {
  $("project-form").reset();
  notice("", false, "dialog-error");
  dialog.showModal();
});
dialog.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => {
  if (!state.pending) dialog.close();
}));
dialog.addEventListener("cancel", (event) => { if (state.pending) event.preventDefault(); });
$("project-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const json = Object.fromEntries(new FormData(form));
  json.description = json.description.trim() || null;
  mutate(async () => {
    form.querySelector('[type="submit"]').disabled = true;
    try {
      const project = await request("/api/projects", { method: "POST", json });
      dialog.close();
      notice();
      try {
        await loadProjects(project.id);
      } catch (error) {
        notice(`Проект создан. Не удалось обновить список проектов. ${error.message}`, true);
      }
    } finally {
      form.querySelector('[type="submit"]').disabled = false;
    }
  }, "dialog-error");
});

const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
function selectTab(tab) {
  for (const item of tabs) {
    const active = item === tab;
    item.tabIndex = active ? 0 : -1;
    item.setAttribute("aria-selected", String(active));
    $(item.getAttribute("aria-controls")).hidden = !active;
  }
}
tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const choices = { ArrowLeft: (i + tabs.length - 1) % tabs.length, ArrowRight: (i + 1) % tabs.length,
      Home: 0, End: tabs.length - 1 };
    if (!Object.hasOwn(choices, event.key)) return;
    event.preventDefault();
    const target = tabs[choices[event.key]];
    selectTab(target);
    target.focus();
  });
});

$("action-table").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sort]");
  if (!button) return;
  const field = button.dataset.sort;
  state.actionSort = { field, order: state.actionSort.field === field ? -state.actionSort.order : -1 };
  renderAction();
  $("action-table").querySelector(`button[data-sort="${field}"]`)?.focus();
});
setOptions($("decay-status"), Object.entries(statuses).map(([key, [label]]) => [key, label]), "Все статусы");
for (const id of ["action-search", "action-min"]) $(id).addEventListener("input", renderAction);
for (const id of ["decay-status", "decay-group", "decay-search"]) $(id).addEventListener("input", renderDecay);
for (const id of ["quarantine-batch", "quarantine-dataset"]) $(id).addEventListener("change", renderQuarantine);
$("cluster-search").addEventListener("input", renderClusters);

const chartScript = document.createElement("script");
chartScript.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.9/dist/chart.umd.min.js";
chartScript.integrity = "sha384-b0GXujLkk9eYYSmcSfoyZbfyElGAQnDyY0skCHSG6w3JgTMFnz11ggrTAr7seu9f";
chartScript.crossOrigin = "anonymous";
chartScript.async = true;
chartScript.addEventListener("load", renderCharts);
document.head.append(chartScript);

clearReports();
loadProjects().catch((error) => {
  $("workspace").hidden = true;
  notice(`Не удалось загрузить проекты. ${error.message}`, true);
});
