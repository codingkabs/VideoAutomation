"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const init = { ...options };
  if (options.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.json);
  }
  const resp = await fetch(path, init);
  let data = null;
  try { data = await resp.json(); } catch { /* not json */ }
  if (!resp.ok) throw new Error((data && data.error) || `Request failed (${resp.status})`);
  return data;
}

let toastTimer = null;
function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const STATUS = {
  published: ["Posted", "ok"], reported: ["Reported", "ok"], draft: ["In drafts", "info"], handoff: ["To phone", "info"],
  scheduled: ["Scheduled", "warn"], queued: ["Queued", "warn"], pending: ["Queued", "warn"], running: ["Running", "warn"],
  submitted: ["Submitted", "warn"], dry_run: ["Preview", "info"], duplicate: ["Already done", ""],
  skipped: ["Skipped", ""], failed: ["Failed", "bad"],
};
const badge = (status) => {
  const [text, tone] = STATUS[status] || [status, ""];
  return el("span", { class: `badge ${tone}` }, text);
};

const ROUTES = {
  meta: "Meta API", zernio: "Zernio", bluesky: "Bluesky API", telegram: "Telegram bot", threads: "Threads API",
  mastodon: "Mastodon API", tiktok: "TikTok drafts", handoff: "hand-off to phone", browser: "browser automation",
  sau: "social-auto-upload", insights: "results check",
};
const DEBUG_NOTE = /^(backend|media|caption|title|tags|trial_graduation|thumb_offset_ms|content_type|draft|subreddit|board_id)=/;

// -------------------------------------------------------------------- state
const state = {
  status: null,
  upload: null,
  selected: new Set(),
  subtitlePath: null,
  busy: false,
};

// --------------------------------------------------------------------- tabs
const loaders = { queue: loadQueue, trials: loadTrials, setup: loadSetup, platforms: renderPlatformTable };
$$(".tabs button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (loaders[name]) loaders[name]();
  history.replaceState(null, "", `#${name}`);
}

// ------------------------------------------------------------------- status
async function loadStatus() {
  state.status = await api("/api/status");
  const postable = state.status.platforms.filter((p) => p.status !== "planned");
  if (state.selected.size === 0) postable.filter((p) => p.default).forEach((p) => state.selected.add(p.key));
  const ready = postable.filter((p) => p.ready && p.backend !== "handoff").length;
  $("#ready-pill").textContent = `${ready} platform${ready === 1 ? "" : "s"} ready`;
  const d = state.status.defaults;
  $("#opt-trial").checked = d.trial;
  $("#trial-options").hidden = !d.trial;
  $("#opt-delay-lo").value = d.trial_delay[0];
  $("#opt-delay-hi").value = d.trial_delay[1];
  $("#opt-graduation").value = d.graduation;
  $("#opt-rewrite").closest("label").title = d.claude ? "" : "Add ANTHROPIC_API_KEY in Setup to use this";
  renderPicker();
  renderMeters();
  renderOverrides();
}

// ---------------------------------------------------------- platform picker
const GROUPS = [
  ["UK core", (p) => p.tier === 1],
  ["Global", (p) => p.tier === 2 && p.status === "implemented"],
  ["Web uploaders (beta)", (p) => p.tier === 3 && p.status === "beta"],
  ["Phone hand-off", (p) => p.status === "handoff"],
  ["China (beta)", (p) => p.tier === 4 && p.status !== "planned"],
];

function chipClass(p) {
  if (p.status === "handoff" || p.backend === "handoff") return "chip handoff";
  if (!p.ready) return "chip";
  return p.status === "beta" ? "chip beta" : "chip ready";
}

function renderPicker() {
  const box = $("#platform-picker");
  box.replaceChildren();
  const platforms = state.status.platforms;
  for (const [title, test] of GROUPS) {
    const items = platforms.filter((p) => p.status !== "planned" && test(p));
    if (!items.length) continue;
    const picked = items.filter((p) => state.selected.has(p.key)).length;
    if (!state.openGroups) state.openGroups = new Set(["UK core", "Global"]);
    if (picked) state.openGroups.add(title);
    const chips = el("div", { class: "chips" }, items.map((p) => el("button", {
      type: "button", class: chipClass(p), "aria-pressed": String(state.selected.has(p.key)),
      title: `${ROUTES[p.backend] || p.backend}: ${p.detail}`,
      onclick: () => {
        state.selected.has(p.key) ? state.selected.delete(p.key) : state.selected.add(p.key);
        renderPicker(); renderMeters(); renderOverrides(); updateButtons();
      },
    }, el("span", { class: "dot" }), p.name)));
    const group = el("details", { class: "pgroup", open: state.openGroups.has(title),
      ontoggle: (e) => { e.target.open ? state.openGroups.add(title) : state.openGroups.delete(title); } },
      el("summary", {}, el("h3", {}, title), el("span", { class: "count" }, picked ? `${picked} of ${items.length}` : `${items.length}`)),
      chips);
    box.append(group);
  }
  box.append(el("div", { class: "legend" },
    el("span", {}, el("i", { style: "background:var(--ok)" }), "ready"),
    el("span", {}, el("i", { style: "background:var(--warn)" }), "beta"),
    el("span", {}, el("i", { style: "background:var(--info)" }), "hand-off to phone"),
    el("span", {}, el("i", { style: "background:var(--off)" }), "needs setup")));
}

$$("[data-pick]").forEach((b) => b.addEventListener("click", () => {
  const all = state.status.platforms.filter((p) => p.status !== "planned");
  state.selected.clear();
  if (b.dataset.pick === "uk") all.filter((p) => p.tier === 1).forEach((p) => state.selected.add(p.key));
  if (b.dataset.pick === "ready") all.filter((p) => p.ready && p.backend !== "handoff").forEach((p) => state.selected.add(p.key));
  renderPicker(); renderMeters(); renderOverrides(); updateButtons();
}));

// ------------------------------------------------------------------ caption
const HASHTAG = /(?<![\w#])#\w+/gu;
function captionLength(text, unit) {
  if (unit === "utf16") return text.length;
  return [...text].length;
}

function renderMeters() {
  const box = $("#caption-meter");
  box.replaceChildren();
  if (!state.status) return;
  const text = $("#caption").value;
  const tags = new Set((text.match(HASHTAG) || []).map((t) => t.toLowerCase())).size;
  for (const key of state.selected) {
    const p = state.status.platforms.find((x) => x.key === key);
    if (!p || !p.caption.max_chars) continue;
    const override = $(`#override-${key}`);
    const own = override && override.value ? override.value : text;
    const len = captionLength(own, p.caption.unit);
    const tooLong = len > p.caption.max_chars;
    const tooManyTags = p.caption.max_hashtags !== null && p.caption.max_hashtags !== undefined && tags > p.caption.max_hashtags;
    let label = `${p.name} ${len}/${p.caption.max_chars}`;
    if (tooManyTags) label += ` · ${tags}/${p.caption.max_hashtags} #`;
    box.append(el("span", {
      class: `meter${tooLong || tooManyTags ? " over" : ""}`,
      title: tooLong ? "Will be shortened for this platform" : tooManyTags ? "Extra hashtags will be removed" : "",
    }, label));
  }
}

function renderOverrides() {
  const box = $("#override-fields");
  const previous = Object.fromEntries($$("textarea", box).map((t) => [t.dataset.key, t.value]));
  box.replaceChildren();
  for (const key of state.selected) {
    const p = state.status.platforms.find((x) => x.key === key);
    if (!p) continue;
    box.append(el("label", {}, p.name,
      el("textarea", { id: `override-${key}`, rows: "2", dataset: { key }, placeholder: "Leave empty to use the main caption",
        oninput: renderMeters }, previous[key] || "")));
  }
}
$("#caption").addEventListener("input", () => { renderMeters(); updateButtons(); });

// ------------------------------------------------------------------- upload
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

async function uploadFiles(files) {
  if (!files || !files.length) return;
  const form = new FormData();
  [...files].forEach((f) => form.append("files", f));
  $("#upload-status").textContent = `Uploading ${files.length} file(s)…`;
  state.upload = null;
  updateButtons();
  try {
    state.upload = await api("/api/upload", { method: "POST", body: form });
    renderPreviews();
    const f = state.upload.files;
    const video = f.find((x) => x.kind === "video");
    $("#upload-status").textContent = video
      ? `Video ${video.width}×${video.height}, ${video.duration.toFixed(1)}s`
      : `${f.length} photo${f.length === 1 ? "" : "s"}`;
  } catch (err) {
    $("#upload-status").textContent = "";
    toast(err.message);
  }
  updateButtons();
}

function renderPreviews() {
  const box = $("#file-previews");
  box.replaceChildren();
  const files = state.upload ? state.upload.files : [];
  dropzone.classList.toggle("has-files", files.length > 0);
  box.classList.toggle("single", files.length === 1);
  for (const f of files) {
    const media = f.kind === "video"
      ? el("video", { src: f.url, poster: f.poster, preload: "none", muted: true, playsinline: true, loop: true,
          onmouseenter: (e) => e.target.play(), onmouseleave: (e) => e.target.pause() })
      : el("img", { src: f.url, alt: f.name });
    box.append(el("div", { class: "thumb" }, media, el("span", {}, f.name)));
  }
}

// ------------------------------------------------------------------ options
$("#opt-trial").addEventListener("change", (e) => { $("#trial-options").hidden = !e.target.checked; });
$("#opt-zoom").addEventListener("input", (e) => { $("#zoom-value").textContent = `${Number(e.target.value).toFixed(2)}×`; });
$("#opt-when").addEventListener("change", async (e) => {
  $("#opt-at").hidden = e.target.value !== "custom";
  $("#best-note").textContent = "";
  if (e.target.value === "best") {
    try {
      const params = new URLSearchParams([...state.selected].map((p) => ["p", p]));
      $("#best-note").textContent = (await api(`/api/best-time?${params}`)).note;
    } catch (err) { $("#best-note").textContent = err.message; }
  }
});
$("#opt-subtitles").addEventListener("change", (e) => {
  if (e.target.value === "file") $("#subtitle-file").click();
  else state.subtitlePath = null;
});
$("#subtitle-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) { $("#opt-subtitles").value = ""; return; }
  const form = new FormData();
  form.append("file", file);
  try {
    state.subtitlePath = (await api("/api/subtitles", { method: "POST", body: form })).path;
    toast(`Subtitles: ${file.name}`);
  } catch (err) { toast(err.message); $("#opt-subtitles").value = ""; }
});

function collectOptions() {
  const whenMode = $("#opt-when").value;
  let at = null;
  if (whenMode === "best") at = "best";
  if (whenMode === "custom") {
    const value = $("#opt-at").value;
    if (!value) throw new Error("Pick a date and time");
    at = new Date(value).toISOString();
  }
  const subtitles = $("#opt-subtitles").value === "auto" ? "auto" : ($("#opt-subtitles").value === "file" ? state.subtitlePath : null);
  return {
    at, subtitles,
    trial: $("#opt-trial").checked,
    trial_delay: [Number($("#opt-delay-lo").value), Number($("#opt-delay-hi").value)],
    trial_mode: $("#opt-trial-mode").value,
    trial_zoom: Number($("#opt-zoom").value),
    trial_hook: $("#opt-hook").value.trim(),
    trial_mirror: $("#opt-mirror").checked,
    graduation: $("#opt-graduation").value,
    tiktok_draft: $("#opt-draft").checked,
    ig_story: $("#opt-story").checked,
    fit: $("#opt-fit").value,
    trim: $("#opt-trim").checked,
    force: $("#opt-force").checked,
    rewrite: $("#opt-rewrite").checked,
  };
}

// ------------------------------------------------------------ preview/post
function updateButtons() {
  const ok = !!state.upload && state.selected.size > 0 && !state.busy;
  $("#btn-preview").disabled = !ok;
  $("#btn-post").disabled = !ok;
  const whenMode = $("#opt-when").value;
  $("#btn-post").textContent = whenMode ? "Schedule" : `Post to ${state.selected.size}`;
}
$("#opt-when").addEventListener("change", updateButtons);
$("#btn-preview").addEventListener("click", () => startTask("preview"));
$("#btn-post").addEventListener("click", () => {
  const names = [...state.selected].map((k) => state.status.platforms.find((p) => p.key === k).name);
  if (confirm(`Post to ${names.length} platform(s)?\n\n${names.join(", ")}`)) startTask("post");
});

async function startTask(action) {
  let options;
  try { options = collectOptions(); } catch (err) { toast(err.message); return; }
  const overrides = Object.fromEntries($$("#override-fields textarea").map((t) => [t.dataset.key, t.value.trim()]));
  state.busy = true;
  updateButtons();
  const progress = $("#progress");
  progress.hidden = false;
  progress.replaceChildren(el("div", { class: "spin" }, action === "preview" ? "Preparing preview…" : "Posting…"));
  try {
    const { task_id } = await api("/api/tasks", { json: {
      action, upload_id: state.upload.upload_id, caption: $("#caption").value,
      platforms: [...state.selected], caption_overrides: overrides, options,
    } });
    const task = await pollTask(task_id, progress);
    if (task.state === "error") throw new Error(task.error);
    renderResults(task.result, action);
  } catch (err) {
    toast(err.message);
  } finally {
    state.busy = false;
    progress.hidden = true;
    updateButtons();
  }
}

async function pollTask(id, box) {
  for (;;) {
    const task = await api(`/api/tasks/${id}`);
    box.replaceChildren(...task.progress.map((m, i) => el("div", { class: i === task.progress.length - 1 && task.state === "running" ? "spin" : "" }, m)));
    if (task.state !== "running") return task;
    await new Promise((r) => setTimeout(r, 900));
  }
}

function renderResults(result, action) {
  const box = $("#results");
  box.hidden = false;
  box.replaceChildren();
  const shown = result.results.filter((r) => r.surface !== "trial_report");
  const report = result.results.find((r) => r.surface === "trial_report");
  const counts = {};
  shown.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  const notes = [...result.notes];
  if (report && report.run_at) notes.push(`Trial Reel results check: ${when(report.run_at)}`);
  box.append(el("div", { class: "card" },
    el("div", { class: "summary" },
      el("h2", { style: "margin:0" }, action === "preview" ? "Preview (nothing posted yet)" : "Results"),
      Object.entries(counts).map(([s, n]) => el("span", {}, badge(s), ` ${n}`))),
    notes.length ? el("ul", { class: "notes" }, notes.map((n) => el("li", {}, n))) : null));

  const byLabel = Object.fromEntries(result.jobs.map((j) => [j.label, j]));
  const cards = el("div", { class: "cards" });
  for (const r of shown) {
    const job = byLabel[r.label];
    let media = null;
    if (job && job.media.length) {
      const first = job.media[0];
      media = first.kind === "video"
        ? el("div", { class: "media" }, el("video", { src: first.url, poster: first.poster, controls: true, preload: "none", playsinline: true }))
        : el("div", { class: `media${job.media.length > 1 ? " grid-imgs" : ""}` }, job.media.slice(0, 4).map((m) => el("img", { src: m.url, alt: "" })));
    }
    const name = job ? job.name : r.platform;
    const surface = r.surface.replaceAll("_", " ");
    const route = job ? ROUTES[job.backend] || job.backend : "";
    const extraNotes = (r.notes || []).filter((n) => !DEBUG_NOTE.test(n));
    cards.append(el("div", { class: "rcard" }, media,
      el("div", { class: "body" },
        el("div", { class: "row" }, el("span", { class: "title" }, name, el("span", { class: "muted small" }, ` · ${surface}`)), badge(r.status)),
        route ? el("div", { class: "route" }, `via ${route}`) : null,
        r.run_at ? el("div", { class: "muted small" }, `at ${when(r.run_at)}`) : null,
        r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, "Open post ↗") : null,
        r.error ? el("div", { style: "color:var(--bad)" }, r.error) : null,
        job && job.title ? el("div", {}, el("b", {}, "Title: "), job.title) : null,
        job && job.caption ? el("div", { class: "caption" }, job.caption) : null,
        job && job.caption_limit ? el("div", { class: "muted small" }, `${[...job.caption].length}/${job.caption_limit} characters`) : null,
        extraNotes.length ? el("ul", {}, extraNotes.map((n) => el("li", {}, n))) : null)));
  }
  box.append(cards);
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

// -------------------------------------------------------------------- queue
async function loadQueue() {
  const box = $("#queue-list");
  box.replaceChildren(el("div", { class: "empty" }, "Loading…"));
  const rows = await api("/api/jobs");
  if (!rows.length) { box.replaceChildren(el("div", { class: "empty" }, "Nothing posted yet.")); return; }
  const body = rows.map((r) => el("tr", {},
    el("td", {}, when(r.run_at)),
    el("td", {}, r.name, r.surface.includes("trial") || r.surface === "story" ? el("div", { class: "muted small" }, r.surface.replaceAll("_", " ")) : null),
    el("td", {}, badge(r.status)),
    el("td", {}, r.url ? el("a", { href: r.url, target: "_blank", rel: "noopener" }, "Open ↗") : (r.error || (r.notes || [])[0] || "")),
    el("td", {},
      r.status === "pending" ? el("button", { class: "link", onclick: () => jobAction(r.id, "cancel") }, "Cancel") : null,
      ["failed", "skipped", "pending"].includes(r.status) && r.surface !== "trial_report" ? el("button", { class: "link", onclick: () => jobAction(r.id, "retry") }, r.status === "pending" ? "Post now" : "Retry") : null)));
  box.replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, ["When", "Platform", "Status", "Link / details", ""].map((h) => el("th", {}, h)))), el("tbody", {}, body)));
}
async function jobAction(id, action) {
  try {
    const r = await api(`/api/jobs/${id}/${action}`, { method: "POST" });
    toast(`${action === "cancel" ? "Cancelled" : "Result"}: ${(STATUS[r.status] || [r.status])[0]}${r.error ? ` (${r.error})` : ""}`);
  } catch (err) { toast(err.message); }
  loadQueue();
}
$("#refresh-queue").addEventListener("click", loadQueue);

// ------------------------------------------------------------------- trials
async function loadTrials() {
  const box = $("#trials-list");
  box.replaceChildren(el("div", { class: "empty" }, "Loading…"));
  const items = await api("/api/trials");
  if (!items.length) { box.replaceChildren(el("div", { class: "empty" }, "No Trial Reels yet. Tick “Instagram Trial Reel” when you post a video.")); return; }
  box.replaceChildren(...items.map((t) => {
    const keys = ["views", "reach", "likes", "comments", "shares", "saved"].filter((k) => (t.main || {})[k] !== undefined || (t.trial || {})[k] !== undefined);
    const bars = keys.length ? el("div", { class: "bars" }, keys.map((k) => {
      const a = (t.main || {})[k] || 0, b = (t.trial || {})[k] || 0, max = Math.max(a, b, 1);
      return [el("span", {}, k), el("div", { class: "bar" },
        el("div", { class: "main", style: `width:${(a / max) * 100}%` }), el("span", {}, `main ${a}`),
        el("div", { class: "trialbar", style: `width:${(b / max) * 100}%` }), el("span", {}, `trial ${b}`))];
    })) : null;
    return el("div", { class: "trial" },
      el("div", { class: "row card-head" }, el("b", {}, `Post ${t.post_id}`), t.winner ? el("span", { class: `badge ${t.winner === "trial" ? "ok" : "info"}` }, `${t.winner} wins`) : null),
      el("div", { class: "small" },
        t.main_url ? el("a", { href: t.main_url, target: "_blank", rel: "noopener" }, "Main reel ↗") : null, " ",
        t.trial_url ? el("a", { href: t.trial_url, target: "_blank", rel: "noopener" }, "Trial reel ↗") : null),
      bars,
      (t.notes || []).length ? el("ul", { class: "notes" }, t.notes.map((n) => el("li", {}, n))) : null);
  }));
}
$("#refresh-trials").addEventListener("click", loadTrials);

// -------------------------------------------------------------------- setup
async function loadChecks(online = false) {
  const box = $("#checks");
  box.replaceChildren(el("div", { class: "empty" }, online ? "Testing connections…" : "Checking…"));
  try {
    const checks = await api(`/api/checks${online ? "?online=1" : ""}`);
    const marks = { ok: "✓", warn: "!", fail: "✗", off: "·" };
    let area = null;
    const out = [];
    for (const c of checks) {
      if (c.area !== area) { area = c.area; out.push(el("div", { class: "check-area" }, area)); }
      out.push(el("div", { class: "check" },
        el("span", { class: `mark ${c.status}` }, marks[c.status] || "?"), el("b", {}, c.name),
        el("span", { class: "detail" }, c.detail), c.fix ? el("span", { class: "fix" }, `Fix: ${c.fix}`) : null));
    }
    box.replaceChildren(el("div", { class: "checks" }, out));
  } catch (err) { box.replaceChildren(el("div", { class: "empty" }, err.message)); }
}

let settingsInitial = {};
async function loadSettings() {
  const data = await api("/api/settings");
  settingsInitial = Object.fromEntries(data.groups.flatMap((g) => g.fields.map((f) => [f.key, f.value])));
  $("#settings-path").textContent = data.path || "";
  const form = $("#settings-form");
  form.replaceChildren(...data.groups.map((g) => el("details", { class: "fgroup",
      open: g.id === "general" || g.fields.some((f) => f.set) },
    el("summary", {}, g.title, el("span", { class: "count" }, `${g.fields.filter((f) => f.set).length} set`)),
    el("div", { class: "fields" }, g.fields.map((f) => {
      let input;
      if (f.kind === "bool") {
        input = el("select", { name: f.key }, ["", "true", "false"].map((v) => el("option", { value: v, selected: f.value === v }, v || "default")));
      } else if (f.kind === "select") {
        input = el("select", { name: f.key }, ["", ...f.choices].map((v) => el("option", { value: v, selected: f.value === v }, v || "default")));
      } else {
        input = el("input", { name: f.key, value: f.value, type: f.secret ? "password" : "text", autocomplete: "off",
          placeholder: f.secret && f.set ? "saved" : "" });
      }
      return el("label", { class: "field" }, f.label, input,
        f.help ? el("small", {}, f.help) : null,
        f.locked ? el("span", { class: "lock" }, "Set in your environment, which overrides this file") : null);
    })))));
}

async function loadSetup() {
  loadChecks();
  loadSettings().catch((err) => toast(err.message));
}
$("#test-online").addEventListener("click", () => loadChecks(true));
$("#save-settings").addEventListener("click", async (e) => {
  e.preventDefault();
  const values = Object.fromEntries([...new FormData($("#settings-form")).entries()]
    .filter(([key, value]) => value !== (settingsInitial[key] ?? "")));
  try {
    const r = await api("/api/settings", { json: { values } });
    $("#settings-status").textContent = r.changed.length ? `Saved ${r.changed.length} setting(s)` : "No changes";
    await loadStatus();
    loadSetup();
  } catch (err) { toast(err.message); }
});

// ---------------------------------------------------------------- platforms
function renderPlatformTable() {
  const box = $("#platform-table");
  if (!state.status) return;
  const route = { implemented: "Official API", beta: "Beta automation", handoff: "Phone hand-off", planned: "Planned" };
  const rows = state.status.platforms.map((p) => el("tr", {},
    el("td", {}, `T${p.tier}`), el("td", {}, el("b", {}, p.name), el("div", { class: "muted small" }, (p.regions || []).join(", "))),
    el("td", {}, route[p.status] || p.status),
    el("td", {}, p.status === "planned" ? el("span", { class: "muted" }, p.detail) : [badge(p.ready ? "published" : "skipped"), " ", el("span", { class: "muted small" }, `${p.backend}: ${p.detail}`)]),
    el("td", { class: "small" }, p.video && p.video.max_s ? `≤${p.video.max_s}s` : p.video && p.video.max_mb ? "any length" : "",
      p.caption && p.caption.max_chars ? el("div", { class: "muted" }, `${p.caption.max_chars} chars`) : null)));
  rows.forEach((r) => {
    const cell = r.children[3];
    const b = cell.querySelector(".badge");
    if (b) b.textContent = b.classList.contains("ok") ? "Ready" : "Needs setup";
  });
  box.replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, ["Tier", "Platform", "Route", "Status", "Limits"].map((h) => el("th", {}, h)))), el("tbody", {}, rows)));
}

// ----------------------------------------------------- shared from the phone
async function loadSharedUpload() {
  const params = new URLSearchParams(location.search);
  const uploadId = params.get("upload");
  if (params.get("caption") && !$("#caption").value) $("#caption").value = params.get("caption");
  if (!uploadId) return;
  try {
    state.upload = await api(`/api/uploads/${encodeURIComponent(uploadId)}`);
    renderPreviews();
    const video = state.upload.files.find((x) => x.kind === "video");
    $("#upload-status").textContent = video
      ? `Shared video ${video.width}×${video.height}, ${video.duration.toFixed(1)}s`
      : `${state.upload.files.length} shared photo(s)`;
    toast("Got it. Add your caption, pick platforms, then Preview or Post.");
  } catch (err) { toast(err.message); }
  history.replaceState(null, "", location.pathname + location.hash);
  renderMeters();
  updateButtons();
}

// --------------------------------------------------------------------- init
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* installing is optional */ });
}
loadStatus().then(async () => {
  await loadSharedUpload();
  updateButtons();
  const tab = location.hash.replace("#", "");
  if (tab && $(`#tab-${tab}`)) showTab(tab);
}).catch((err) => toast(err.message));
