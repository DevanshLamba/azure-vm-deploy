/* CloudTasks UI. Plain JS, no dependencies. User text is only ever written with textContent (no innerHTML). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const motionOK = () => !reduceMotion.matches;
const EASE = "cubic-bezier(.22,.8,.24,1)";
const SVG_NS = "http://www.w3.org/2000/svg";

const store = {
  get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

const COLORS = ["peach", "mint", "lavender", "butter", "sky", "blush"];
const PRIORITY_LABEL = { high: "High", medium: "Medium", low: "Low" };
const PRIORITY_RANK = { high: 0, medium: 1, low: 2 };

/* ================================================================ API */
async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error(errorMessage(res.status, data));
  return data;
}

function errorMessage(status, data) {
  if (status === 429) return "That's a lot of clicks. Give it a few seconds and try again.";
  const detail = data && data.detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0];
    const field = first.loc ? String(first.loc[first.loc.length - 1]) : "";
    const msg = String(first.msg || "Invalid value").replace(/^Value error, /, "");
    return field && field !== "body" ? `${field[0].toUpperCase()}${field.slice(1).replace("_", " ")}: ${msg}` : msg;
  }
  if (typeof detail === "string") return detail;
  if (status >= 500) return "The server had a hiccup. Please try again.";
  return `Something went wrong (${status}).`;
}

/* ================================================================ dates & formatting */
function isoDay(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function dueInfo(iso, done) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(`${iso}T00:00:00`);
  const diff = Math.round((day - today) / 86400000);
  const short = day.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  let label;
  if (diff === 0) label = "Today";
  else if (diff === 1) label = "Tomorrow";
  else if (diff === -1) label = "Yesterday";
  else if (diff > 1 && diff < 7) label = day.toLocaleDateString(undefined, { weekday: "long" });
  else label = short;
  let tone = "";
  if (!done && diff < 0) { tone = "overdue"; label = `Overdue · ${label}`; }
  else if (!done && diff === 0) tone = "today";
  return { label, tone, full: day.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }) };
}

function relTime(isoString) {
  const then = new Date(isoString);
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 86400 * 7) return `${Math.floor(secs / 86400)}d ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function fmtBytes(bytes) {
  const gib = bytes / 1024 ** 3;
  if (gib >= 10) return `${Math.round(gib)} GB`;
  if (gib >= 1) return `${gib.toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

function fmtDuration(totalSecs) {
  const d = Math.floor(totalSecs / 86400);
  const h = Math.floor((totalSecs % 86400) / 3600);
  const m = Math.floor((totalSecs % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  return `${m}m ${Math.floor(totalSecs % 60)}s`;
}

const REGION_NAMES = {
  centralindia: "Central India", southindia: "South India", westindia: "West India", jioindiawest: "Jio India West",
  eastus: "East US", eastus2: "East US 2", westus: "West US", westus2: "West US 2", westus3: "West US 3",
  centralus: "Central US", northcentralus: "North Central US", southcentralus: "South Central US", westcentralus: "West Central US",
  canadacentral: "Canada Central", canadaeast: "Canada East", brazilsouth: "Brazil South", mexicocentral: "Mexico Central",
  northeurope: "North Europe", westeurope: "West Europe", uksouth: "UK South", ukwest: "UK West",
  francecentral: "France Central", germanywestcentral: "Germany West Central", swedencentral: "Sweden Central",
  switzerlandnorth: "Switzerland North", norwayeast: "Norway East", polandcentral: "Poland Central",
  italynorth: "Italy North", spaincentral: "Spain Central", uaenorth: "UAE North", qatarcentral: "Qatar Central",
  israelcentral: "Israel Central", southafricanorth: "South Africa North", southeastasia: "Southeast Asia",
  eastasia: "East Asia", japaneast: "Japan East", japanwest: "Japan West", koreacentral: "Korea Central",
  australiaeast: "Australia East", australiasoutheast: "Australia Southeast", newzealandnorth: "New Zealand North",
  indonesiacentral: "Indonesia Central", malaysiawest: "Malaysia West",
};

/* ================================================================ masonry (shortest column first, FLIP animated) */
function columnCount(width) {
  if (width < 600) return 1;
  if (width < 900) return 2;
  if (width < 1240) return 3;
  return 4;
}

function layout(container, items, { animate = true } = {}) {
  const width = container.clientWidth;
  if (!width) return;
  const n = columnCount(width);
  const motion = animate && motionOK();
  const focused = document.activeElement;

  // FLIP step 1: remember where every existing item is.
  const first = new Map();
  if (motion) for (const el of items) if (el.isConnected) first.set(el, el.getBoundingClientRect());

  let cols = [...container.children].filter((c) => c.classList.contains("col"));
  if (cols.length !== n) {
    cols = Array.from({ length: n }, () => Object.assign(document.createElement("div"), { className: "col" }));
    container.replaceChildren(...cols);
  }

  const gap = parseFloat(getComputedStyle(container).columnGap) || 20;
  const heights = new Array(n).fill(0);
  const counts = new Array(n).fill(0);
  for (const el of items) {
    let c = 0;
    for (let i = 1; i < n; i++) if (heights[i] < heights[c] - 1) c = i;
    const col = cols[c];
    const at = col.children[counts[c]];
    if (at !== el) col.insertBefore(el, at || null); // only move nodes that are out of place
    counts[c]++;
    heights[c] += el.offsetHeight + gap;
  }
  // Anything left over is no longer wanted.
  cols.forEach((col, i) => {
    while (col.children.length > counts[i]) {
      const el = col.lastElementChild;
      el.remove();
      el._leaving = false;
      el.getAnimations().forEach((a) => a.cancel());
    }
  });

  if (focused && focused !== document.activeElement && focused.isConnected) focused.focus({ preventScroll: true });

  // FLIP step 2: animate from the old position to the new one.
  if (motion) {
    for (const [el, r] of first) {
      if (!el.isConnected) continue;
      const r2 = el.getBoundingClientRect();
      const dx = r.left - r2.left;
      const dy = r.top - r2.top;
      if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
        el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 520, easing: EASE });
      }
    }
  }
}

/* ================================================================ elements & state */
const els = {
  board: $("#board"),
  tiles: $("#tiles"),
  search: $("#search"),
  sort: $("#sort"),
  summary: $("#summary"),
  greeting: $("#greeting"),
  stateEmpty: $("#state-empty"),
  stateNoMatch: $("#state-nomatch"),
  stateError: $("#state-error"),
  errorText: $("#error-text"),
  noMatchText: $("#nomatch-text"),
  samplesBtn: $("#load-samples"),
  fab: $("#fab"),
  composer: $("#composer"),
  form: $("#composer-form"),
  fTitle: $("#f-title"),
  fNote: $("#f-note"),
  fDue: $("#f-due"),
  counter: $("#f-title-count"),
  formError: $("#form-error"),
  submit: $("#composer-submit"),
  toasts: $("#toasts"),
  tpl: $("#card-tpl"),
  topbar: $(".topbar"),
  themeToggle: $("#theme-toggle"),
};

const state = {
  tasks: [],
  filter: ["all", "active", "done"].includes(store.get("ct-filter")) ? store.get("ct-filter") : "all",
  sort: store.get("ct-sort", "newest"),
  query: "",
  loaded: false,
  error: null,
  view: "tasks",
};

const cells = new Map(); // task id -> .cell element
// Layout always uses the logical order, never DOM order (DOM order is column-major after a layout).
const currentCells = () => visibleTasks().map((t) => cells.get(t.id)).filter(Boolean);
const byId = (id) => state.tasks.find((t) => t.id === id);

/* ================================================================ cards */
function cellFor(task) {
  let cell = cells.get(task.id);
  if (!cell) {
    cell = els.tpl.content.firstElementChild.cloneNode(true);
    cell.dataset.id = task.id;
    cell._isNew = true;
    cells.set(task.id, cell);
  }
  fillCard(cell, task);
  return cell;
}

function fillCard(cell, t) {
  const card = cell.firstElementChild;
  card.dataset.color = t.color;
  card.classList.toggle("is-done", t.done);

  $(".pdot", card).dataset.p = t.priority;
  $(".prio-text", card).textContent = PRIORITY_LABEL[t.priority];
  $(".prio", card).title = `${PRIORITY_LABEL[t.priority]} priority`;
  $(".card-title-text", card).textContent = t.title;

  const note = $(".card-note", card);
  note.textContent = t.note || "";
  note.hidden = !t.note;

  const due = $(".due", card);
  if (t.due_date) {
    const info = dueInfo(t.due_date, t.done);
    $(".due-text", due).textContent = info.label;
    due.dataset.tone = info.tone;
    due.title = `Due ${info.full}`;
    due.hidden = false;
  } else {
    due.hidden = true;
  }

  $(".card-meta", card).textContent = t.done ? `Done ${relTime(t.updated_at)}` : `Added ${relTime(t.created_at)}`;
  const check = $(".check", card);
  check.setAttribute("aria-checked", String(t.done));
  check.setAttribute("aria-label", `Mark "${t.title}" as ${t.done ? "not done" : "done"}`);
  $('[data-action="edit"]', card).setAttribute("aria-label", `Edit "${t.title}"`);
  $('[data-action="delete"]', card).setAttribute("aria-label", `Delete "${t.title}"`);
}

function visibleTasks() {
  const q = state.query.trim().toLowerCase();
  let list = state.tasks.filter((t) => state.filter === "all" || (state.filter === "done") === t.done);
  if (q) list = list.filter((t) => t.title.toLowerCase().includes(q) || (t.note || "").toLowerCase().includes(q));
  const sorters = {
    newest: (a, b) => b.id - a.id,
    oldest: (a, b) => a.id - b.id,
    due: (a, b) => (a.due_date || "9999").localeCompare(b.due_date || "9999") || b.id - a.id,
    priority: (a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] || b.id - a.id,
    title: (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
  };
  return list.sort(sorters[state.sort] || sorters.newest);
}

/* ================================================================ render */
let leaveTimer = null;
let firstPaint = true;

function render({ animate = true } = {}) {
  updateCounts();
  updateSummary();
  els.board.setAttribute("aria-busy", "false");
  els.stateError.hidden = true;

  const list = visibleTasks();
  const wanted = list.map(cellFor);
  const wantedSet = new Set(wanted);
  const motion = animate && motionOK() && !firstPaint;

  // Cards that are no longer shown fade out before the layout closes the gap.
  let leaving = false;
  for (const cell of $$(".cell", els.board)) {
    if (!wantedSet.has(cell) && !cell._leaving) {
      cell._leaving = true;
      leaving = true;
      if (motion) {
        cell.animate(
          [{ opacity: 1, transform: "none", filter: "blur(0)" }, { opacity: 0, transform: "scale(.9)", filter: "blur(3px)" }],
          { duration: 280, easing: "ease-in", fill: "forwards" },
        );
      }
    }
  }
  for (const cell of wanted) {
    if (cell._leaving) { cell._leaving = false; cell.getAnimations().forEach((a) => a.cancel()); }
  }

  const finish = () => {
    if (!els.board.querySelector(".col")) els.board.replaceChildren(); // drop skeletons
    layout(els.board, wanted, { animate: motion });
    const fresh = wanted.filter((c) => c._isNew);
    fresh.forEach((cell, i) => {
      cell._isNew = false;
      if (!motionOK()) return;
      if (firstPaint) {
        cell.animate([{ opacity: 0, transform: "translateY(16px)" }, { opacity: 1, transform: "none" }],
          { duration: 500, delay: Math.min(i, 16) * 45, easing: EASE, fill: "backwards" });
      } else {
        cell.animate(
          [
            { opacity: 0, transform: "scale(.82) translateY(14px)" },
            { opacity: 1, transform: "scale(1.03)", offset: 0.65 },
            { opacity: 1, transform: "none" },
          ],
          { duration: 520, delay: Math.min(i, 12) * 55, easing: EASE, fill: "backwards" },
        );
      }
    });
    firstPaint = false;

    const hasAny = state.tasks.length > 0;
    els.stateEmpty.hidden = hasAny;
    els.stateNoMatch.hidden = !hasAny || list.length > 0;
    if (hasAny && !list.length) {
      els.noMatchText.textContent = state.query.trim()
        ? `No ${state.filter === "all" ? "" : state.filter + " "}tasks match "${state.query.trim()}".`
        : state.filter === "done" ? "Nothing finished yet. You've got this." : "Everything is done. Time for a break!";
    }
  };

  clearTimeout(leaveTimer);
  if (leaving && motion) leaveTimer = setTimeout(finish, 290);
  else finish();
}

function updateCounts() {
  const done = state.tasks.filter((t) => t.done).length;
  const counts = { all: state.tasks.length, active: state.tasks.length - done, done };
  for (const el of $$("[data-count]")) el.textContent = counts[el.dataset.count];
  for (const chip of $$(".chip")) chip.setAttribute("aria-checked", String(chip.dataset.filter === state.filter));
}

function updateSummary() {
  const s = els.summary;
  if (!state.loaded) return;
  if (!state.tasks.length) { s.textContent = "Nothing on your board yet."; return; }
  const today = isoDay(0);
  const open = state.tasks.filter((t) => !t.done);
  const dueToday = open.filter((t) => t.due_date === today).length;
  const overdue = open.filter((t) => t.due_date && t.due_date < today).length;
  const done = state.tasks.length - open.length;
  const parts = [[open.length, open.length === 1 ? "open task" : "open tasks"]];
  if (dueToday) parts.push([dueToday, "due today"]);
  if (overdue) parts.push([overdue, "overdue"]);
  parts.push([done, "done"]);
  s.replaceChildren();
  parts.forEach(([n, label], i) => {
    if (i) s.append(" · ");
    const strong = document.createElement("strong");
    strong.textContent = n;
    s.append(strong, ` ${label}`);
  });
}

function showSkeleton() {
  const heights = [3, 5, 2, 4, 3, 2, 5, 3];
  const sk = heights.map((lines) => {
    const d = document.createElement("div");
    d.className = "cell skeleton";
    d.setAttribute("aria-hidden", "true");
    for (let i = 0; i < lines; i++) {
      const line = document.createElement("div");
      line.className = i === 1 ? "sk-line lg" : "sk-line";
      if (i > 1) line.style.width = `${88 - i * 9}%`;
      d.append(line);
    }
    const pad = document.createElement("div");
    pad.style.height = "12px";
    d.append(pad);
    return d;
  });
  els.board.setAttribute("aria-busy", "true");
  els.board.replaceChildren();
  layout(els.board, sk, { animate: false });
}

/* ================================================================ data actions */
async function loadTasks() {
  state.error = null;
  els.stateError.hidden = true;
  els.stateEmpty.hidden = true;
  if (!state.loaded) showSkeleton();
  try {
    state.tasks = await api("/api/tasks");
    state.loaded = true;
    els.board.replaceChildren();
    render();
  } catch (err) {
    state.error = err;
    els.board.replaceChildren();
    els.board.setAttribute("aria-busy", "false");
    els.errorText.textContent = err.message;
    els.stateError.hidden = false;
    els.summary.textContent = "Couldn't load your board.";
  }
}

async function toggleTask(id) {
  const t = byId(id);
  if (!t) return;
  const cell = cells.get(id);
  const card = cell.firstElementChild;
  t.done = !t.done;
  if (t.done && motionOK()) {
    card.classList.add("just-done");
    setTimeout(() => card.classList.remove("just-done"), 700);
  }
  fillCard(cell, t);
  updateCounts();
  updateSummary();
  // Let the check animation play before a filter hides the card.
  if (state.filter !== "all") setTimeout(() => render(), motionOK() ? 650 : 0);
  try {
    Object.assign(t, await api(`/api/tasks/${id}/toggle`, { method: "POST" }));
    fillCard(cell, t);
  } catch (err) {
    t.done = !t.done;
    fillCard(cell, t);
    render();
    toast(err.message, { tone: "error" });
  }
}

async function deleteTask(id) {
  const t = byId(id);
  if (!t) return;
  state.tasks = state.tasks.filter((x) => x.id !== id);
  cells.delete(id);
  render();
  try {
    await api(`/api/tasks/${id}`, { method: "DELETE" });
    toast("Task deleted", { action: { label: "Undo", fn: () => restoreTask(t) } });
  } catch (err) {
    state.tasks.push(t);
    render();
    toast(err.message, { tone: "error" });
  }
}

async function restoreTask(t) {
  try {
    let created = await api("/api/tasks", {
      method: "POST",
      body: { title: t.title, note: t.note, priority: t.priority, due_date: t.due_date, color: t.color },
    });
    if (t.done) created = await api(`/api/tasks/${created.id}/toggle`, { method: "POST" });
    state.tasks.push(created);
    render();
    toast("Task restored");
  } catch (err) {
    toast(err.message, { tone: "error" });
  }
}

async function loadSamples(btn) {
  if (btn) btn.disabled = true;
  try {
    const added = await api("/api/tasks/sample", { method: "POST" });
    state.tasks.push(...added);
    resetFilters();
    render();
    toast(`Added ${added.length} sample tasks`);
  } catch (err) {
    toast(err.message, { tone: "error" });
  } finally {
    if (btn) btn.disabled = false;
  }
}

function resetFilters() {
  state.filter = "all";
  state.query = "";
  els.search.value = "";
  store.set("ct-filter", "all");
}

/* ================================================================ composer */
let editing = null;
let lastFocus = null;

function openComposer(task = null) {
  if (els.composer.open) return;
  setView("tasks");
  editing = task;
  lastFocus = document.activeElement;
  els.form.reset();
  els.formError.hidden = true;
  els.fTitle.removeAttribute("aria-invalid");
  $("#composer-title").textContent = task ? "Edit task" : "New task";
  els.submit.textContent = task ? "Save changes" : "Add task";

  els.fTitle.value = task ? task.title : "";
  els.fNote.value = task ? task.note || "" : "";
  const prio = task ? task.priority : "medium";
  $(`input[name="priority"][value="${prio}"]`, els.form).checked = true;
  const maxId = state.tasks.reduce((m, t) => Math.max(m, t.id), 0);
  const color = task ? task.color : COLORS[maxId % COLORS.length];
  $(`input[name="color"][value="${color}"]`, els.form).checked = true;
  setDue(task ? task.due_date : null);
  updateCounter();

  els.composer.showModal();
  els.fab.classList.add("is-hidden");
  els.fTitle.focus();
  if (task) els.fTitle.select();
}

function closeComposer() {
  const dlg = els.composer;
  if (!dlg.open || dlg.classList.contains("closing")) return;
  if (!motionOK()) { dlg.close(); return; }
  dlg.classList.add("closing");
  const done = (e) => {
    if (e && e.target !== dlg) return;
    dlg.removeEventListener("animationend", done);
    dlg.classList.remove("closing");
    dlg.close();
  };
  dlg.addEventListener("animationend", done);
  setTimeout(done, 400); // safety net if animationend never fires
}

function setDue(iso) {
  els.fDue.value = iso || "";
  let matched = false;
  for (const btn of $$(".due-chip[data-due]", els.form)) {
    const v = btn.dataset.due;
    const on = v === "none" ? !iso : iso === isoDay(Number(v));
    btn.setAttribute("aria-pressed", String(on));
    matched ||= on;
  }
  $(".due-picker", els.form).classList.toggle("has-value", Boolean(iso) && !matched);
}

function updateCounter() {
  const len = els.fTitle.value.length;
  els.counter.textContent = `${len} / 120`;
  els.counter.classList.toggle("warn", len > 100);
}

async function submitComposer(e) {
  e.preventDefault();
  const title = els.fTitle.value.trim();
  if (!title) {
    els.fTitle.setAttribute("aria-invalid", "true");
    showFormError("Give your task a title first.");
    els.fTitle.focus();
    return;
  }
  const fd = new FormData(els.form);
  const body = {
    title,
    note: els.fNote.value.trim() || null,
    priority: fd.get("priority"),
    due_date: els.fDue.value || null,
    color: fd.get("color"),
  };
  els.submit.disabled = true;
  try {
    if (editing) {
      const updated = await api(`/api/tasks/${editing.id}`, { method: "PATCH", body });
      Object.assign(byId(editing.id) || editing, updated);
      render();
      toast("Changes saved");
    } else {
      const created = await api("/api/tasks", { method: "POST", body });
      state.tasks.push(created);
      if (!visibleTasks().some((t) => t.id === created.id)) resetFilters();
      render();
      toast("Task added");
      requestAnimationFrame(() => cells.get(created.id)?.scrollIntoView({ block: "nearest", behavior: motionOK() ? "smooth" : "auto" }));
    }
    closeComposer();
  } catch (err) {
    showFormError(err.message);
  } finally {
    els.submit.disabled = false;
  }
}

function showFormError(msg) {
  els.formError.textContent = msg;
  els.formError.hidden = false;
}

/* ================================================================ toasts */
function toast(message, { tone = "ok", action } = {}) {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.tone = tone;
  el.setAttribute("role", tone === "error" ? "alert" : "status");

  const icon = document.createElement("span");
  icon.className = "toast-icon";
  if (tone === "error") {
    icon.textContent = "!";
  } else {
    const svg = document.createElementNS(SVG_NS, "svg");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", "#i-check");
    svg.append(use);
    icon.append(svg);
  }
  const text = document.createElement("span");
  text.textContent = message;
  el.append(icon, text);

  let timer;
  const dismiss = () => {
    clearTimeout(timer);
    if (!el.isConnected || el.classList.contains("leaving")) return;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), motionOK() ? 260 : 0);
  };
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = action.label;
    btn.addEventListener("click", () => { dismiss(); action.fn(); });
    el.append(btn);
  }
  els.toasts.append(el);
  while (els.toasts.children.length > 3) els.toasts.firstElementChild.remove();
  timer = setTimeout(dismiss, action ? 6000 : 3200);
}

/* ================================================================ views, theme, chrome */
function setView(view) {
  if (view !== "tasks" && view !== "status") view = "tasks";
  const changed = state.view !== view;
  state.view = view;
  $("#view-tasks").hidden = view !== "tasks";
  $("#view-status").hidden = view !== "status";
  for (const tab of $$(".view-tab")) {
    const on = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  moveGlider();
  els.fab.classList.toggle("is-hidden", view !== "tasks");
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  if (view === "status") {
    layoutTiles();
    startPolling();
  } else {
    stopPolling();
    if (changed && state.loaded) requestAnimationFrame(() => layout(els.board, currentCells(), { animate: false }));
  }
}

function moveGlider() {
  const tab = $('.view-tab[aria-selected="true"]');
  const glider = $(".view-tab-glider");
  if (!tab || !glider) return;
  glider.style.width = `${tab.offsetWidth}px`;
  glider.style.transform = `translateX(${tab.offsetLeft}px)`;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  store.set("ct-theme", theme);
  els.themeToggle.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  $('meta[name="theme-color"]').setAttribute("content", theme === "dark" ? "#1B1920" : "#FBF7F2");
}

function greet() {
  const h = new Date().getHours();
  els.greeting.textContent = h < 5 ? "Up late?" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : h < 22 ? "Good evening" : "Up late?";
}

/* ================================================================ VM status */
const HISTORY = 48;
const series = { cpu: [], mem: [], load: [], latency: [] };
let pollTimer = null;
let polling = false;
let infoLoaded = false;
let tilesReady = false;

const TILES = $$(".tile", els.tiles); // captured once, in HTML order
function layoutTiles() {
  layout(els.tiles, TILES, { animate: false });
}

function startPolling() {
  if (polling) return;
  polling = true;
  pollStatus();
}

function stopPolling() {
  polling = false;
  clearTimeout(pollTimer);
}

async function pollStatus() {
  if (!polling) return;
  if (document.hidden) { polling = false; return; }
  if (!infoLoaded) loadInfo();

  const healthCall = (async () => {
    const t0 = performance.now();
    const res = await fetch("/health", { cache: "no-store" });
    const ms = performance.now() - t0;
    const body = await res.json().catch(() => null);
    return { ok: res.ok, ms, body };
  })();
  const [m, h] = await Promise.allSettled([api("/api/metrics"), healthCall]);

  if (m.status === "fulfilled") {
    updateMetrics(m.value);
    setLive("live", "Live");
  } else {
    setLive("error", "Reconnecting…");
  }
  updateHealth(h.status === "fulfilled" ? h.value : null);

  if (!tilesReady && m.status === "fulfilled") {
    tilesReady = true;
    els.tiles.setAttribute("aria-busy", "false");
    layoutTiles();
  }
  if (polling) pollTimer = setTimeout(pollStatus, 2500);
}

function setLive(stateName, text) {
  $("#live-pill").dataset.state = stateName;
  $("#live-text").textContent = text;
}

function push(arr, v) {
  arr.push(v);
  if (arr.length > HISTORY) arr.shift();
}

function setGauge(name, percent, label) {
  const g = $(`[data-gauge="${name}"]`);
  const p = Math.max(0, Math.min(100, percent));
  $(".gauge-fill", g).style.strokeDashoffset = String(314.16 * (1 - p / 100));
  g.dataset.level = p >= 85 ? "high" : "ok";
  g.setAttribute("role", "img");
  g.setAttribute("aria-label", label);
}

function updateMetrics(m) {
  const cpu = m.cpu_percent;
  setGauge("cpu", cpu, `CPU ${Math.round(cpu)} percent used`);
  $("#cpu-val").textContent = `${Math.round(cpu)}%`;
  $("#cpu-cores").textContent = `${m.cpu_count} vCPU${m.cpu_count === 1 ? "" : "s"}`;
  push(series.cpu, cpu);
  drawSpark("cpu", series.cpu, Math.min(100, Math.max(25, ...series.cpu) * 1.3));

  const mem = m.memory;
  setGauge("mem", mem.percent, `Memory ${Math.round(mem.percent)} percent used`);
  $("#mem-val").textContent = `${Math.round(mem.percent)}%`;
  $("#mem-total").textContent = `${fmtBytes(mem.total)} RAM`;
  $("#mem-detail").textContent = `${fmtBytes(mem.used)} of ${fmtBytes(mem.total)} in use`;
  push(series.mem, mem.percent);
  drawSpark("mem", series.mem, 100);

  const disk = m.disk;
  setGauge("disk", disk.percent, `Disk ${Math.round(disk.percent)} percent full`);
  $("#disk-val").textContent = `${Math.round(disk.percent)}%`;
  $("#disk-total").textContent = fmtBytes(disk.total);
  $("#disk-detail").textContent = `${fmtBytes(disk.used)} used of ${fmtBytes(disk.total)}`;

  if (m.load_avg) {
    const [l1, l5, l15] = m.load_avg;
    $("#load-1").textContent = l1.toFixed(2);
    $("#load-5").textContent = l5.toFixed(2);
    $("#load-15").textContent = l15.toFixed(2);
    push(series.load, l1);
    drawSpark("load", series.load, Math.max(m.cpu_count, ...series.load) * 1.1);
  } else {
    for (const id of ["#load-1", "#load-5", "#load-15"]) $(id).textContent = "n/a";
  }

  $("#uptime-val").textContent = fmtDuration(m.uptime_seconds);
  const booted = new Date(Date.now() - m.uptime_seconds * 1000);
  $("#uptime-detail").textContent = `Booted ${booted.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`;
}

function updateHealth(h) {
  const orb = $("#health-orb");
  if (h && h.ok) {
    orb.dataset.state = "ok";
    $("#health-val").textContent = "Healthy";
    $("#health-detail").textContent = `${Math.round(h.ms)} ms · database ${h.body?.database ?? "ok"}`;
    push(series.latency, h.ms);
    drawSpark("latency", series.latency, Math.max(50, ...series.latency) * 1.2);
  } else {
    orb.dataset.state = "error";
    $("#health-val").textContent = "Unreachable";
    $("#health-detail").textContent = h ? `Status check failed (${h.body?.database ?? "error"})` : "No answer from /health";
  }
}

function drawSpark(name, values, max) {
  const host = $(`[data-spark="${name}"]`);
  if (!host._svg) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 100 34");
    svg.setAttribute("preserveAspectRatio", "none");
    const defs = document.createElementNS(SVG_NS, "defs");
    const grad = document.createElementNS(SVG_NS, "linearGradient");
    const gid = `spark-grad-${name}`;
    grad.id = gid;
    for (const [attr, val] of [["x1", 0], ["y1", 0], ["x2", 0], ["y2", 1]]) grad.setAttribute(attr, val);
    for (const [offset, cls] of [["0", "spark-stop-1"], ["1", "spark-stop-2"]]) {
      const stop = document.createElementNS(SVG_NS, "stop");
      stop.setAttribute("offset", offset);
      stop.setAttribute("class", cls);
      grad.append(stop);
    }
    defs.append(grad);
    const area = document.createElementNS(SVG_NS, "path");
    area.setAttribute("class", "spark-area");
    area.setAttribute("fill", `url(#${gid})`);
    const line = document.createElementNS(SVG_NS, "path");
    line.setAttribute("class", "spark-line");
    svg.append(defs, area, line);
    const dot = document.createElement("span");
    dot.className = "spark-dot";
    host.append(svg, dot);
    host._svg = { area, line, dot };
  }
  const { area, line, dot } = host._svg;
  const n = values.length;
  const pts = values.map((v, i) => [n === 1 ? 100 : (i / (n - 1)) * 100, 31 - (Math.min(v, max) / max) * 27]);
  if (n === 1) pts.unshift([0, pts[0][1]]);
  const d = smoothPath(pts);
  line.setAttribute("d", d);
  area.setAttribute("d", `${d} L100 34 L0 34 Z`);
  const last = pts[pts.length - 1];
  dot.style.left = `${last[0]}%`;
  dot.style.top = `${(last[1] / 34) * 100}%`;
}

// Catmull-Rom to Bezier, with control points clamped so the curve never overshoots the box.
function smoothPath(pts) {
  const clampY = (y) => Math.max(1, Math.min(33, y));
  let d = `M${pts[0][0].toFixed(2)} ${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, clampY(p1[1] + (p2[1] - p0[1]) / 6)];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, clampY(p2[1] - (p3[1] - p1[1]) / 6)];
    d += ` C${c1[0].toFixed(2)} ${c1[1].toFixed(2)} ${c2[0].toFixed(2)} ${c2[1].toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  return d;
}

async function loadInfo() {
  try {
    const i = await api("/api/info");
    infoLoaded = true;
    const regionName = i.region ? REGION_NAMES[i.region] || i.region : null;

    $("#app-version").textContent = `v${i.version}`;
    $("#status-sub").textContent = i.on_azure
      ? `Live numbers from ${i.vm_name || i.hostname} in ${regionName}`
      : `Live numbers from ${i.hostname}`;

    $("#region-val").textContent = regionName || "Local machine";
    $("#region-detail").textContent = i.region ? `${i.region} · Microsoft Azure` : "Not on Azure: no instance metadata";
    $("#vm-size").textContent = i.vm_size ? i.vm_size.replace("Standard_", "") : "local";

    const ipBtn = $("#ip-copy");
    if (i.public_ip) {
      $("#ip-val").textContent = i.public_ip;
      $("#ip-detail").textContent = "From the Azure instance metadata service";
      ipBtn.hidden = false;
      ipBtn.onclick = async () => {
        try { await navigator.clipboard.writeText(i.public_ip); toast("IP address copied"); }
        catch { toast("Couldn't copy. Select the IP and copy it manually.", { tone: "error" }); }
      };
    } else {
      $("#ip-val").textContent = "Not available";
      $("#ip-detail").textContent = "Shown when running on an Azure VM";
      ipBtn.hidden = true;
    }

    const rows = [
      ["Hostname", i.hostname],
      i.vm_name && ["VM", i.vm_name],
      ["OS", i.os],
      ["Container", i.container || "not containerised"],
      ["Python", i.python],
      ["Deployed", `${new Date(i.deployed_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} (${relTime(i.deployed_at)})`],
    ].filter(Boolean);
    const kv = $("#host-kv");
    kv.replaceChildren(...rows.map(([k, v]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = k;
      dd.textContent = v;
      row.append(dt, dd);
      return row;
    }));
    layoutTiles();
  } catch {
    infoLoaded = false; // retried on the next poll
  }
}

/* ================================================================ events */
function bindEvents() {
  // Card actions (event delegation)
  els.board.addEventListener("click", (e) => {
    const cell = e.target.closest(".cell[data-id]");
    if (!cell) return;
    const id = Number(cell.dataset.id);
    if (e.target.closest(".check")) toggleTask(id);
    else if (e.target.closest('[data-action="edit"]')) openComposer(byId(id));
    else if (e.target.closest('[data-action="delete"]')) deleteTask(id);
  });
  els.board.addEventListener("dblclick", (e) => {
    const cell = e.target.closest(".cell[data-id]");
    if (cell && !e.target.closest("button")) openComposer(byId(Number(cell.dataset.id)));
  });

  // Toolbar
  let searchTimer;
  els.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = els.search.value; render(); }, 120);
  });
  for (const chip of $$(".chip")) {
    chip.addEventListener("click", () => {
      state.filter = chip.dataset.filter;
      store.set("ct-filter", state.filter);
      render();
    });
  }
  $(".chips").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const chips = $$(".chip");
    const i = chips.indexOf(document.activeElement);
    const next = chips[(i + (e.key === "ArrowRight" ? 1 : chips.length - 1)) % chips.length];
    next.focus();
    next.click();
  });
  els.sort.value = state.sort;
  if (els.sort.value !== state.sort) { state.sort = "newest"; els.sort.value = "newest"; }
  els.sort.addEventListener("change", () => {
    state.sort = els.sort.value;
    store.set("ct-sort", state.sort);
    render();
  });
  els.samplesBtn.addEventListener("click", () => loadSamples(els.samplesBtn));

  // Empty / error state buttons
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".state [data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "new") openComposer();
    else if (action === "samples") loadSamples(btn);
    else if (action === "retry") loadTasks();
    else if (action === "clear-filters") { resetFilters(); render(); }
  });

  // FAB + composer
  els.fab.addEventListener("click", () => openComposer());
  els.form.addEventListener("submit", submitComposer);
  els.fTitle.addEventListener("input", () => {
    updateCounter();
    if (els.fTitle.value.trim()) { els.fTitle.removeAttribute("aria-invalid"); els.formError.hidden = true; }
  });
  for (const btn of $$('[data-action="close"]', els.composer)) btn.addEventListener("click", closeComposer);
  for (const btn of $$(".due-chip[data-due]", els.form)) {
    btn.addEventListener("click", () => setDue(btn.dataset.due === "none" ? null : isoDay(Number(btn.dataset.due))));
  }
  els.fDue.addEventListener("change", () => setDue(els.fDue.value || null));
  els.composer.addEventListener("cancel", (e) => { e.preventDefault(); closeComposer(); });
  els.composer.addEventListener("click", (e) => { if (e.target === els.composer) closeComposer(); });
  els.composer.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); els.form.requestSubmit(); }
  });
  els.composer.addEventListener("close", () => {
    editing = null;
    if (state.view === "tasks") els.fab.classList.remove("is-hidden");
    if (lastFocus && lastFocus.isConnected) lastFocus.focus({ preventScroll: true });
  });

  // Global keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || els.composer.open) return;
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "/" && !typing) {
      e.preventDefault();
      setView("tasks");
      els.search.focus();
      els.search.select();
    } else if ((e.key === "n" || e.key === "N") && !typing) {
      e.preventDefault();
      openComposer();
    } else if (e.key === "Escape" && e.target === els.search) {
      els.search.value = "";
      state.query = "";
      render();
      els.search.blur();
    }
  });

  // Tabs
  for (const tab of $$(".view-tab")) tab.addEventListener("click", () => setView(tab.dataset.view));
  $(".view-tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const next = state.view === "tasks" ? "status" : "tasks";
    setView(next);
    $(`.view-tab[data-view="${next}"]`).focus();
  });
  window.addEventListener("hashchange", () => setView(location.hash.slice(1)));

  // Theme
  els.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  // Chrome
  window.addEventListener("scroll", () => els.topbar.classList.toggle("scrolled", scrollY > 4), { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.view === "status") startPolling();
  });

  // Re-flow the masonry when the available width changes.
  const widths = new WeakMap();
  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = Math.round(entry.contentRect.width);
      if (!w || widths.get(entry.target) === w) continue;
      widths.set(entry.target, w);
      if (entry.target === els.board && state.loaded) layout(els.board, currentCells(), { animate: false });
      if (entry.target === els.tiles) layoutTiles();
    }
    moveGlider();
  });
  ro.observe(els.board);
  ro.observe(els.tiles);
  document.fonts?.ready.then(() => {
    moveGlider();
    if (state.loaded) layout(els.board, currentCells(), { animate: false });
    layoutTiles();
  });
}

/* ================================================================ boot */
applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
greet();
bindEvents();
setView(location.hash.slice(1) || "tasks");
loadTasks();
