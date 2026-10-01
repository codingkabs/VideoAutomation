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
  // Times are shown in your vauto time zone (VAUTO_TIMEZONE), the one schedules use.
  const opts = { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  const tz = state.status && state.status.defaults.timezone;
  if (tz) {
    opts.timeZone = tz;
    if (tz !== Intl.DateTimeFormat().resolvedOptions().timeZone) opts.timeZoneName = "short";
  }
  try { return new Date(iso).toLocaleString(undefined, opts); } catch { return new Date(iso).toLocaleString(); }
}

const STATUS = {
  published: ["Posted", "ok"], reported: ["Reported", "ok"], draft: ["In drafts", "info"], handoff: ["To phone", "info"],
  scheduled: ["Scheduled", "warn"], queued: ["Queued", "warn"], pending: ["Queued", "warn"], running: ["Running", "warn"],
  submitted: ["Submitted", "warn"], dry_run: ["Preview", "info"], duplicate: ["Already done", ""],
  scheduled_past: ["Should be live", "ok"], held: ["Draft", "info"],
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
const DEBUG_NOTE = /^(backend|media|caption|title|tags|trial_graduation|thumb_offset_ms|content_type|draft|hold|subreddit|board_id)=/;

// -------------------------------------------------------------------- state
const state = {
  status: null,
  upload: null,
  selected: new Set(),
  subtitlePath: null,
  busy: false,
};

// --------------------------------------------------------------------- tabs
const loaders = { posts: loadPosts, stats: loadStats, setup: loadSetup, platforms: renderPlatformTable };
$$(".tabs button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
$$("[data-goto]").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.goto)));
function showTab(name, push = true) {
  if (!$(`#tab-${name}`)) name = "post";
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (loaders[name]) loaders[name]();
  if (location.hash !== `#${name}`) history[push ? "pushState" : "replaceState"](null, "", `#${name}`);
  window.scrollTo(0, 0);
}
// Back/forward (and the phone's back gesture) move between tabs.
window.addEventListener("popstate", () => showTab(location.hash.replace("#", "") || "post", false));
window.addEventListener("hashchange", () => showTab(location.hash.replace("#", "") || "post", false));

// ------------------------------------------------------------------- status
async function loadStatus() {
  state.status = await api("/api/status");
  const postable = state.status.platforms.filter((p) => p.status !== "planned");
  if (state.selected.size === 0) postable.filter((p) => p.default).forEach((p) => state.selected.add(p.key));
  const ready = postable.filter((p) => p.ready && p.backend !== "handoff").length;
  $("#ready-pill").textContent = `${ready} platform${ready === 1 ? "" : "s"} ready`;
  $("#first-run").hidden = ready > 0;
  const pick = $("#posts-platform");
  if (pick.options.length <= 1) {
    postable.forEach((p) => pick.append(el("option", { value: p.key }, p.name)));
  }
  const d = state.status.defaults;
  $("#opt-trial").checked = d.trial;
  $("#opt-drafts").checked = !!d.drafts;
  $("#opt-comment").value = d.first_comment_mode || "off";
  if (d.first_comment_mode === "mine" && !$("#opt-comment-text").value) $("#opt-comment-text").value = d.first_comment_text || "";
  renderCommentBox();
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
  if ($("#opt-comment").value === "claude") $("#opt-comment-text").value = "";  // new video, new comment
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

// ------------------------------------------------------------ first comment
function renderCommentBox() {
  const mode = $("#opt-comment").value;
  const box = $("#opt-comment-text");
  box.hidden = mode === "off";
  box.placeholder = mode === "claude"
    ? "Claude writes it when you tap Preview. You can edit it here before posting."
    : "e.g. What would you have done? 👇";
  const noKey = mode === "claude" && state.status && !state.status.defaults.claude;
  $("#comment-hint").textContent = noKey
    ? "Add an Anthropic API key in Setup → Extras so Claude can write it."
    : mode === "off" ? "" : "TikTok doesn't let apps post comments, so it's skipped there.";
}
$("#opt-comment").addEventListener("change", renderCommentBox);

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
    drafts: $("#opt-drafts").checked,
    first_comment_mode: $("#opt-comment").value,
    first_comment: $("#opt-comment").value === "off" ? "" : $("#opt-comment-text").value.trim(),
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
  const drafts = $("#opt-drafts").checked;
  $("#btn-post").textContent = drafts ? `Save ${state.selected.size} draft${state.selected.size === 1 ? "" : "s"}`
    : whenMode ? "Schedule" : `Post to ${state.selected.size}`;
  $("#opt-when").disabled = drafts;  // drafts are saved now; you pick the time when you post them
}
$("#opt-when").addEventListener("change", updateButtons);
$("#opt-drafts").addEventListener("change", updateButtons);
$("#btn-preview").addEventListener("click", () => startTask("preview"));
$("#btn-post").addEventListener("click", () => {
  const names = [...state.selected].map((k) => state.status.platforms.find((p) => p.key === k).name);
  const ask = $("#opt-drafts").checked
    ? `Save as drafts on ${names.length} platform(s)? Nothing is published yet.\n\n${names.join(", ")}`
    : `Post to ${names.length} platform(s)?\n\n${names.join(", ")}`;
  if (confirm(ask)) startTask("post");
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
  if (action === "preview" && result.first_comment && $("#opt-comment").value === "claude" && !$("#opt-comment-text").value.trim()) {
    $("#opt-comment-text").value = result.first_comment;  // posting uses this exact text; edit it if you like
    toast("Claude wrote the first comment. Edit it under the caption if you like.");
  }
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
      Object.entries(counts).map(([s, n]) => el("span", {}, badge(s), ` ${n}`)),
      action === "post" ? el("button", { class: "link", onclick: () => showTab("posts") }, "See it in My posts →") : null),
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

// ----------------------------------------------------------------- numbers
const fmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const num = (n) => fmt.format(Math.round(n || 0));
const NUMS = [["views", "views"], ["likes", "likes"], ["comments", "comments"], ["shares", "shares"], ["saved", "saves"]];
function numbersLine(m) {
  const parts = NUMS.filter(([k]) => m && m[k]).map(([k, word]) => `${num(m[k])} ${word}`);
  return parts.join(" · ");
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
function thumbEl(url, kind, cls = "pthumb") {
  return url ? el("img", { class: cls, src: url, alt: "", loading: "lazy" })
    : el("div", { class: `${cls} none`, "aria-hidden": "true" }, kind === "photos" ? "▦" : "▶");
}

// ------------------------------------------------------------------ tooltip
// One tooltip for every chart mark; values also sit in labels/tables, so it never gates anything.
const tip = $("#viz-tip");
function attachTip(node, lines) {
  node.tabIndex = 0;
  if (!node.getAttribute("role")) node.setAttribute("role", "img");  // a named, focusable chart mark
  node.setAttribute("aria-label", lines.join(", "));
  const show = (x, y) => {
    tip.replaceChildren(el("strong", {}, lines[0]), ...lines.slice(1).map((l) => el("div", {}, l)));
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(Math.max(8, x - r.width / 2), innerWidth - r.width - 8)}px`;
    tip.style.top = `${Math.max(8, y - r.height - 12)}px`;
  };
  node.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("pointerleave", () => { tip.hidden = true; });
  node.addEventListener("focus", () => { const b = node.getBoundingClientRect(); show(b.left + b.width / 2, b.top); });
  node.addEventListener("blur", () => { tip.hidden = true; });
}

// ----------------------------------------------------------------- my posts
const postsState = { show: "all", q: "", platform: "", offset: 0, open: new Set() };

async function loadPosts(append = false) {
  const box = $("#posts-list");
  if (!append) { postsState.offset = 0; box.classList.add("loading"); }
  const params = new URLSearchParams({ show: postsState.show, q: postsState.q, platform: postsState.platform,
    limit: "20", offset: String(postsState.offset) });
  let data;
  try { data = await api(`/api/posts?${params}`); } catch (err) { toast(err.message); box.classList.remove("loading"); return; }
  box.classList.remove("loading");
  if (!append) box.replaceChildren();
  if (!data.total) {
    const filtered = postsState.q || postsState.platform || postsState.show !== "all";
    box.replaceChildren(el("div", { class: "empty" }, filtered ? "No posts match." :
      el("span", {}, "Nothing posted yet. Your posts show up here with their links and numbers. ",
        el("button", { class: "link inline-link", onclick: () => showTab("post") }, "Post something"))));
  }
  data.posts.forEach((p) => box.append(postItem(p)));
  postsState.offset += data.posts.length;
  $("#posts-more").hidden = postsState.offset >= data.total;
  loadUpcoming();
}

function statusDot(status) {
  const tone = (STATUS[status] || [status, ""])[1];
  return el("span", { class: `sdot ${tone}`, "aria-hidden": "true" });
}

function postItem(p) {
  const posted = p.posted_at ? when(p.posted_at) : "";
  const chips = el("div", { class: "pchips" },
    p.jobs.map((j) => el("span", { class: "pchip", title: (STATUS[j.status] || [j.status])[0] }, statusDot(j.status),
      j.surface === "trial_reel" ? `${j.name} trial` : j.surface === "story" ? `${j.name} story` : j.name,
      el("span", { class: "sr-only" }, `: ${(STATUS[j.status] || [j.status])[0]}`))),
    p.rejected.map((r) => el("span", { class: "pchip", title: r.error }, statusDot("failed"), `${r.name} (not posted)`)));
  const t = p.totals;
  const nums = t.views || t.likes ? el("span", { class: "pnums" },
    t.views ? el("span", {}, el("b", {}, num(t.views)), " views") : null,
    t.likes ? el("span", {}, el("b", {}, num(t.likes)), " likes") : null) : null;
  const detail = el("div", { class: "pdetail", hidden: !postsState.open.has(p.post_id) });
  const head = el("button", { class: "phead", type: "button", "aria-expanded": String(postsState.open.has(p.post_id)),
    onclick: () => toggleDetail(p, head, detail) },
    thumbEl(p.thumb, p.kind),
    el("div", { class: "pmain" },
      el("div", { class: "pcaption" }, p.caption || el("span", { class: "muted" }, "(no caption)")),
      el("div", { class: "pmeta" }, el("span", {}, posted), nums,
        p.state === "upcoming" ? el("span", { class: "badge warn" }, "Scheduled") : null,
        p.needs_you ? el("span", { class: "badge warn" }, "Needs you") : null),
      chips));
  const item = el("article", { class: "pitem", id: `post-${p.post_id}` }, head, detail);
  if (!detail.hidden) renderPostDetail(p, detail);
  return item;
}

function toggleDetail(p, head, detail, force) {
  const open = force ?? detail.hidden;
  detail.hidden = !open;
  head.setAttribute("aria-expanded", String(open));
  open ? postsState.open.add(p.post_id) : postsState.open.delete(p.post_id);
  if (open && !detail.childElementCount) renderPostDetail(p, detail);
}

function renderPostDetail(p, box) {
  const rows = p.jobs.map((j) => jobRow(p, j));
  p.rejected.forEach((r) => rows.push(el("div", { class: "jrow" },
    el("div", { class: "jtop" }, el("span", { class: "jname" }, r.name), badge("failed")),
    el("div", { class: "jerr" }, `Not posted: ${r.error}`),
    el("div", { class: "muted small" }, "Tip: “Post again” and tick “Trim to each platform's max length”, or pick a different video."))));
  box.replaceChildren(...rows, el("div", { class: "pactions" },
    p.drafts ? el("button", { class: "primary", onclick: () => publishDrafts(p) }, `Post drafts (${p.drafts})`) : null,
    el("button", { class: "secondary", onclick: () => refreshNumbers(p.post_id) }, "Update numbers"),
    el("button", { class: "secondary", onclick: () => postAgain(p.post_id) }, "Post again"),
    el("button", { class: "link danger", onclick: () => forgetPost(p) }, "Remove from history")));
}

function jobRow(p, j) {
  const route = ROUTES[j.backend] || j.backend;
  const status = j.status;
  const numbers = numbersLine(j.metrics);
  const row = el("div", { class: "jrow" },
    el("div", { class: "jtop" },
      el("span", { class: "jname" }, j.name, el("span", { class: "muted small" }, ` · ${j.surface.replaceAll("_", " ")} · via ${route}`)),
      badge(status)),
    status === "pending" || status === "scheduled" ? el("div", { class: "muted small" }, `Goes out ${when(j.run_at)}`) : null,
    numbers ? el("div", { class: "jnums" }, numbers,
      el("span", { class: "muted small" }, j.stats_source === "you" ? " · typed by you" : ` · updated ${ago(j.stats_at)}`)) : null,
    j.url ? el("a", { href: j.url, target: "_blank", rel: "noopener", class: "jlink" }, "Open post ↗") : null,
    j.error ? el("div", { class: "jerr" }, j.error) : null,
    status === "handoff" ? el("div", { class: "muted small" }, "Waiting for you to post it from your phone. When it's up, mark it as posted so it counts.") : null,
    status === "draft" ? el("div", { class: "muted small" }, j.platform === "youtube"
      ? "Uploaded as Private: in the YouTube app, set it to Public when you're ready. Then mark it as posted."
      : "In your TikTok drafts: open TikTok, add a sound and post. Then mark it as posted.") : null,
    status === "held" ? el("div", { class: "muted small" }, j.surface === "trial_reel"
      ? "Goes out 1–2 hours after the main Reel, once you post the drafts."
      : "Ready and waiting. Tap Post drafts below, or save the video and post it yourself from the app.") : null,
    status === "held" && (j.media || []).length && j.media[0].url
      ? el("a", { href: j.media[0].url, download: "", class: "jlink" }, "Save video ↓") : null);
  const actions = el("div", { class: "jactions" });
  const act = (label, fn, cls = "link") => actions.append(el("button", { class: cls, onclick: fn }, label));
  if (status === "pending") act("Cancel", () => jobAction(j.id, "cancel"));
  if (status === "held") act("Discard draft", () => jobAction(j.id, "cancel"));
  if (["failed", "skipped", "pending"].includes(status)) act(status === "pending" ? "Post now" : "Retry", () => jobAction(j.id, "retry"));
  if (["handoff", "draft", "failed", "submitted", "held"].includes(status)) act("Mark as posted", () => openForm(row, "posted", j));
  if (!j.auto_stats && ["published", "handoff", "draft", "submitted", "scheduled_past"].includes(status)) act("Enter numbers", () => openForm(row, "numbers", j));
  if (actions.childElementCount) row.append(actions);
  return row;
}

function openForm(row, kind, j) {
  row.querySelector(".jform")?.remove();
  const form = el("form", { class: "jform" });
  if (kind === "posted") {
    form.append(el("label", {}, "Link to the post (optional)", el("input", { name: "url", type: "url", inputmode: "url", placeholder: "https://…" })));
  } else {
    form.append(el("div", { class: "numgrid" }, NUMS.slice(0, 4).map(([k, word]) => el("label", {}, word[0].toUpperCase() + word.slice(1),
      el("input", { name: k, type: "number", min: "0", inputmode: "numeric", value: j.metrics[k] ?? "" })))));
  }
  form.append(el("div", { class: "actions" },
    el("button", { class: "primary", type: "submit" }, "Save"),
    el("button", { class: "secondary", type: "button", onclick: () => form.remove() }, "Cancel")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      await api(`/api/jobs/${j.id}/${kind}`, { json: values });
      toast(kind === "posted" ? "Marked as posted" : "Numbers saved");
      loadPosts();
    } catch (err) { toast(err.message); }
  });
  row.append(form);
  form.querySelector("input")?.focus();
}

async function jobAction(id, action) {
  try {
    const r = await api(`/api/jobs/${id}/${action}`, { json: {} });
    toast(`${action === "cancel" ? "Cancelled" : "Result"}: ${(STATUS[r.status] || [r.status])[0]}${r.error ? ` (${r.error})` : ""}`);
  } catch (err) { toast(err.message); }
  loadPosts();
}

async function runRefresh(url, body = {}) {
  toast("Fetching the latest numbers…");
  try {
    const { task_id } = await api(url, { json: body });
    for (;;) {
      const task = await api(`/api/tasks/${task_id}`);
      if (task.state === "error") throw new Error(task.error);
      if (task.state === "done") {
        const r = task.result;
        toast(r.errors.length ? `${r.text}. First problem: ${r.errors[0]}` : r.text[0].toUpperCase() + r.text.slice(1));
        return r;
      }
      await new Promise((res) => setTimeout(res, 1000));
    }
  } catch (err) { toast(err.message); return null; }
}
async function publishDrafts(p) {
  const n = p.drafts;
  if (!confirm(`Post ${n} draft${n === 1 ? "" : "s"} now?\n\nThey go live on each platform straight away. A Trial Reel follows 1–2 hours later.`)) return;
  toast("Posting your drafts…");
  try {
    const { task_id } = await api(`/api/posts/${p.post_id}/publish`, { json: {} });
    for (;;) {
      const task = await api(`/api/tasks/${task_id}`);
      if (task.state === "error") throw new Error(task.error);
      if (task.state === "done") {
        const failed = task.result.results.filter((r) => r.status === "failed");
        toast(failed.length ? `${failed.length} failed: ${failed[0].label}: ${failed[0].error}` : "Drafts posted");
        break;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  } catch (err) { toast(err.message); }
  loadPosts();
}

async function refreshNumbers(postId) { await runRefresh(`/api/posts/${postId}/refresh`); loadPosts(); }

async function postAgain(postId) {
  try {
    const data = await api(`/api/posts/${postId}/reuse`, { json: {} });
    state.upload = data;
    renderPreviews();
    $("#caption").value = data.caption || "";
    $("#upload-status").textContent = "Loaded from My posts. Pick platforms, then Preview or Post.";
    $("#opt-force").checked = true;
    showTab("post");
    renderMeters(); updateButtons();
  } catch (err) { toast(err.message); }
}

async function forgetPost(p) {
  if (!confirm("Remove this post from your history?\n\nIt stays on the platforms; vauto just stops tracking it.")) return;
  try { await api(`/api/posts/${p.post_id}`, { method: "DELETE" }); toast("Removed from history"); loadPosts(); }
  catch (err) { toast(err.message); }
}

async function loadUpcoming() {
  let s;
  try { s = await api("/api/summary?days=30"); } catch { return; }
  // One row per post: its time, where it is going, and a way to manage it.
  const groups = new Map();
  for (const j of s.upcoming) {
    const g = groups.get(j.post_id) || { ...j, names: [] };
    g.names.push(j.name);
    g.run_at = g.run_at < j.run_at ? g.run_at : j.run_at;
    groups.set(j.post_id, g);
  }
  const card = $("#upcoming-card");
  card.hidden = !groups.size;
  $("#upcoming-count").textContent = groups.size ? `${groups.size} post${groups.size === 1 ? "" : "s"}` : "";
  $("#upcoming-list").replaceChildren(...[...groups.values()].map((g) => el("div", { class: "urow" },
    thumbEl(g.thumb, "video", "uthumb"),
    el("div", { class: "umain" }, el("b", {}, when(g.run_at)),
      el("div", { class: "small" }, g.caption), el("div", { class: "small" }, g.names.join(", "))),
    el("button", { class: "link", onclick: () => openPost(g.post_id) }, "Manage"))));
}

function openPost(postId) {
  const item = $(`#post-${postId}`);
  if (!item) { toast("Scroll down to find it, or search its caption"); return; }
  item.querySelector(".phead").click();
  if (item.querySelector(".pdetail").hidden) item.querySelector(".phead").click();
  item.scrollIntoView({ behavior: "smooth", block: "start" });
}

$$("#tab-posts .seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#tab-posts .seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  postsState.show = b.dataset.show;
  loadPosts();
}));
let searchTimer = null;
$("#posts-q").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { postsState.q = e.target.value.trim(); loadPosts(); }, 250);
});
$("#posts-platform").addEventListener("change", (e) => { postsState.platform = e.target.value; loadPosts(); });
$("#posts-more").addEventListener("click", () => loadPosts(true));
$("#posts-refresh").addEventListener("click", async () => { await runRefresh("/api/stats/refresh", { days: 30 }); loadPosts(); });

// -------------------------------------------------------------------- stats
const statsState = { days: 30 };

async function loadStats() {
  const body = $("#stats-body");
  body.classList.add("loading");
  let s;
  try { s = await api(`/api/summary?days=${statsState.days}`); } catch (err) { toast(err.message); body.classList.remove("loading"); return; }
  body.classList.remove("loading");
  $("#stats-updated").textContent = s.refreshed_at ? `Numbers updated ${ago(s.refreshed_at)}` : "";
  renderKpis(s);
  const note = $("#stats-note");
  note.hidden = s.has_numbers;
  note.replaceChildren(s.posts
    ? el("span", {}, el("b", {}, "No view counts yet. "), "vauto reads numbers from Instagram and Facebook (direct), Threads, Bluesky, Mastodon and anything posted through Zernio. Press ",
      el("b", {}, "Update numbers"), ", or type them in on My posts for phone hand-offs.")
    : el("span", {}, "Post something and your numbers will build up here."));
  renderPlatformBars(s);
  renderTopPosts(s);
  renderHours(s);
  renderCalendar(s);
  renderHashtags(s);
  loadTrials();
}

function renderKpis(s) {
  const t = s.totals;
  const tiles = [["Posts", s.posts, `${s.live} live across platforms`], ["Views", t.views], ["Likes", t.likes],
    ["Comments", t.comments], ["Shares", t.shares]];
  $("#kpis").replaceChildren(...tiles.map(([label, value, sub]) => el("div", { class: "kpi" },
    el("div", { class: "kpi-label" }, label), el("div", { class: "kpi-value" }, num(value)),
    sub ? el("div", { class: "kpi-sub" }, sub) : null)));
}

function emptyChart(text) { return el("div", { class: "chart-empty" }, text); }

function renderPlatformBars(s) {
  const box = $("#platform-bars");
  const rows = s.platforms.filter((p) => p.posts);
  if (!rows.length) { box.replaceChildren(emptyChart("No live posts in this period.")); return; }
  const max = Math.max(...rows.map((r) => r.views), 1);
  box.replaceChildren(el("div", { class: "hbars" }, rows.map((r) => {
    const bar = el("div", { class: "hbar-fill", style: `width:${r.views ? Math.max(1.5, (r.views / max) * 100) : 0}%` });
    const track = el("div", { class: "hbar-track" }, bar, el("span", { class: "hbar-value" }, r.views ? num(r.views) : "–"));
    attachTip(track, [`${r.name}: ${num(r.views)} views`, `${r.posts} post(s), ${num(r.avg_views)} average`, `${num(r.likes)} likes · ${num(r.comments)} comments`]);
    return el("div", { class: "hbar-row" },
      el("div", { class: "hbar-label" }, el("span", {}, r.name), el("span", { class: "muted small" }, `${r.posts} post${r.posts === 1 ? "" : "s"}`)),
      track);
  })));
}

function renderTopPosts(s) {
  const box = $("#top-posts");
  if (!s.top.length) { box.replaceChildren(emptyChart("Your best posts show up here once numbers come in.")); return; }
  box.replaceChildren(...s.top.map((p, i) => {
    const link = (p.jobs.find((j) => j.url) || {}).url;
    return el("div", { class: "trow" },
      el("span", { class: "trank" }, `${i + 1}`), thumbEl(p.thumb, p.kind, "tthumb"),
      el("div", { class: "tmain" }, el("div", { class: "pcaption" }, p.caption),
        el("div", { class: "muted small" }, numbersLine(p.totals))),
      link ? el("a", { href: link, target: "_blank", rel: "noopener", class: "small tap" }, "Open ↗") : null);
  }));
}

function renderHours(s) {
  const box = $("#hour-chart");
  if (!s.enough_data) { box.replaceChildren(emptyChart("After a few posts with numbers, this shows which hours work best for you.")); return; }
  const by = Object.fromEntries(s.by_hour.map((h) => [h.hour, h]));
  const max = Math.max(...s.by_hour.map((h) => h.avg_views), 1);
  const best = s.by_hour.reduce((a, b) => (b.avg_views > a.avg_views ? b : a));
  const cols = [];
  for (let h = 0; h < 24; h++) {
    const d = by[h];
    const col = el("div", { class: `vcol${d ? "" : " none"}${d && d === best ? " best" : ""}` },
      d && d === best ? el("span", { class: "vcol-value" }, num(d.avg_views)) : null,
      el("div", { class: "vcol-fill", style: `height:${d ? Math.max(3, (d.avg_views / max) * 100) : 0}%` }));
    if (d) attachTip(col, [`${String(h).padStart(2, "0")}:00`, `${num(d.avg_views)} average views`, `${d.posts} post(s)`]);
    cols.push(col);
  }
  box.replaceChildren(el("div", { class: "vcols" }, cols),
    el("div", { class: "vaxis" }, ["00:00", "06:00", "12:00", "18:00", "23:00"].map((t) => el("span", {}, t))),
    el("p", { class: "small" }, `Best so far: around ${String(best.hour).padStart(2, "0")}:00. Pick “Next best time” when posting to schedule for your best slots.`));
}

function renderCalendar(s) {
  const box = $("#calendar");
  const start = new Date(`${s.calendar.start}T12:00:00`);
  const end = new Date(`${s.calendar.end}T12:00:00`);
  const monday = new Date(start);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const weeks = [];
  for (let w = new Date(monday); w <= end; w.setDate(w.getDate() + 7)) {
    const cells = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(w); day.setDate(day.getDate() + d);
      const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
      const n = s.calendar.days[key] || 0;
      const out = day < start || day > end;
      const cell = el("div", { class: `cal-cell l${Math.min(n, 4)}${out ? " out" : ""}` });
      if (!out) attachTip(cell, [day.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }), n ? `${n} post${n === 1 ? "" : "s"}` : "no posts"]);
      cells.push(cell);
    }
    weeks.push(el("div", { class: "cal-week" }, cells));
  }
  $("#streak").textContent = s.streak > 1 ? `🔥 ${s.streak}-day streak.` : "";
  box.replaceChildren(el("div", { class: "cal" },
    el("div", { class: "cal-days", "aria-hidden": "true" }, ["Mon", "", "Wed", "", "Fri", "", "Sun"].map((d) => el("span", {}, d))),
    el("div", { class: "cal-grid" }, weeks)),
    el("div", { class: "cal-legend" }, "Fewer", [0, 1, 2, 3, 4].map((l) => el("span", { class: `cal-cell l${l}` })), "More"));
}

function renderHashtags(s) {
  const box = $("#hashtags");
  if (!s.enough_data || !s.hashtags.length) { box.replaceChildren(emptyChart("Hashtags you use will be ranked here once a few posts have numbers.")); return; }
  box.replaceChildren(el("table", { class: "tight" },
    el("thead", {}, el("tr", {}, ["Hashtag", "Posts", "Avg views"].map((h) => el("th", {}, h)))),
    el("tbody", {}, s.hashtags.map((h) => el("tr", {}, el("td", {}, h.tag), el("td", { class: "num" }, h.posts), el("td", { class: "num" }, num(h.avg_views)))))));
}

async function loadTrials() {
  const box = $("#trials-list");
  let items;
  try { items = await api("/api/trials"); } catch (err) { box.replaceChildren(emptyChart(err.message)); return; }
  if (!items.length) { box.replaceChildren(emptyChart("No Trial Reels yet. Tick “Instagram Trial Reel” when you post a video.")); return; }
  box.replaceChildren(el("div", { class: "legend-row" },
      el("span", {}, el("i", { class: "sw s1" }), "Main reel"), el("span", {}, el("i", { class: "sw s2" }), "Trial reel")),
    ...items.map((t) => {
    const keys = ["views", "reach", "likes", "comments", "shares", "saved"].filter((k) => (t.main || {})[k] !== undefined || (t.trial || {})[k] !== undefined);
    const bars = keys.length ? el("div", { class: "tbars" }, keys.map((k) => {
      const a = (t.main || {})[k] || 0, b = (t.trial || {})[k] || 0, max = Math.max(a, b, 1);
      const pair = el("div", { class: "tbar-pair" },
        el("div", { class: "tbar" }, el("div", { class: "tbar-fill s1", style: `width:${(a / max) * 100}%` }), el("span", {}, num(a))),
        el("div", { class: "tbar" }, el("div", { class: "tbar-fill s2", style: `width:${(b / max) * 100}%` }), el("span", {}, num(b))));
      attachTip(pair, [k, `main ${num(a)}`, `trial ${num(b)}`]);
      return [el("span", { class: "tbar-label" }, k), pair];
    })) : null;
    return el("div", { class: "trial" },
      el("div", { class: "row card-head" }, el("b", {}, `Post ${t.post_id}`),
        t.winner ? el("span", { class: `badge ${t.winner === "trial" ? "ok" : "info"}` }, `${t.winner} wins`) : null),
      el("div", { class: "small" },
        t.main_url ? el("a", { href: t.main_url, target: "_blank", rel: "noopener" }, "Main reel ↗") : null, " ",
        t.trial_url ? el("a", { href: t.trial_url, target: "_blank", rel: "noopener" }, "Trial reel ↗") : null),
      bars,
      (t.notes || []).length ? el("ul", { class: "notes" }, t.notes.map((n) => el("li", {}, n))) : null);
  }));
}

$$("#tab-stats .seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#tab-stats .seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  statsState.days = Number(b.dataset.days);
  loadStats();
}));
$("#stats-refresh").addEventListener("click", async () => { await runRefresh("/api/stats/refresh", { days: statsState.days || 3650 }); loadStats(); });

// ----------------------------------------------------------------- snippets
async function loadSnippets() {
  let items = [];
  try { items = await api("/api/snippets"); } catch { return; }
  $("#snippet-chips").replaceChildren(...items.map((sn) => el("span", { class: "snip" },
    el("button", { type: "button", class: "snip-use", title: sn.text, onclick: () => insertSnippet(sn.text) }, `+ ${sn.name}`),
    el("button", { type: "button", class: "snip-del", "aria-label": `Delete saved text ${sn.name}`, onclick: async () => {
      if (!confirm(`Delete “${sn.name}”?`)) return;
      await api(`/api/snippets/${sn.id}`, { method: "DELETE" }).catch((err) => toast(err.message));
      loadSnippets();
    } }, "×"))));
}
function insertSnippet(text) {
  const box = $("#caption");
  const start = box.selectionStart ?? box.value.length, end = box.selectionEnd ?? box.value.length;
  const before = box.value.slice(0, start), after = box.value.slice(end);
  const glue = before && !/\s$/.test(before) ? (text.startsWith("#") ? " " : "\n\n") : "";
  box.value = before + glue + text + after;
  box.focus();
  const pos = (before + glue + text).length;
  box.setSelectionRange(pos, pos);
  renderMeters(); updateButtons();
}
$("#snippet-toggle").addEventListener("click", () => {
  const form = $("#snippet-form");
  form.hidden = !form.hidden;
  $("#snippet-toggle").setAttribute("aria-expanded", String(!form.hidden));
  if (!form.hidden) $("#snippet-name").focus();
});
$("#snippet-save").addEventListener("click", async () => {
  const text = $("#caption").value.trim();
  if (!text) { toast("Write the caption or hashtags first, then save them"); return; }
  try {
    await api("/api/snippets", { json: { name: $("#snippet-name").value, text } });
    $("#snippet-name").value = "";
    $("#snippet-form").hidden = true;
    $("#snippet-toggle").setAttribute("aria-expanded", "false");
    toast("Saved. Tap it any time to add it to a caption.");
    loadSnippets();
  } catch (err) { toast(err.message); }
});

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
const platState = { q: "", show: "all", open: new Set() };
const ROUTE_KIND = { implemented: "Official API", beta: "Beta: drives the website", handoff: "Hand-off: you tap post on your phone", planned: "Researched, no route yet" };
const TIER = { 1: "UK core", 2: "Global", 3: "Regional apps", 4: "China" };
const REGION = { uk: "UK", global: "Global", us: "US", ru: "Russia", cis: "CIS", mena: "Middle East", in: "India", br: "Brazil",
  latam: "Latin America", id: "Indonesia", pk: "Pakistan", sea: "Southeast Asia", jp: "Japan", th: "Thailand", tw: "Taiwan",
  vn: "Vietnam", kr: "South Korea", cn: "China", fr: "France" };

function renderPlatformTable() {
  if (!state.status) return;
  const q = platState.q.toLowerCase();
  const items = state.status.platforms.filter((p) => {
    const regions = (p.regions || []).map((r) => REGION[r] || r).join(" ");
    const text = `${p.name} ${p.key} ${regions} ${TIER[p.tier] || ""}`.toLowerCase();
    if (q && !text.includes(q)) return false;
    if (platState.show === "ready") return p.ready && p.status !== "planned";
    if (platState.show === "setup") return !p.ready && p.status !== "planned";
    return true;
  });
  $("#plat-count").textContent = `${items.length} of ${state.status.platforms.length}`;
  const box = $("#platform-list");
  box.replaceChildren();
  let tier = null;
  for (const p of items) {
    if (p.tier !== tier) { tier = p.tier; box.append(el("h3", { class: "plat-tier" }, TIER[tier] || `Tier ${tier}`)); }
    box.append(platformItem(p));
  }
  if (!items.length) box.append(el("div", { class: "empty" }, "No platforms match."));
}

function platformItem(p) {
  const g = p.guide || {};
  const readyBadge = p.status === "planned" ? el("span", { class: "badge" }, "Planned")
    : p.backend === "handoff" && p.ready ? el("span", { class: "badge info" }, "Via your phone")
    : el("span", { class: `badge ${p.ready ? "ok" : "warn"}` }, p.ready ? "Ready" : "Needs setup");
  const details = el("details", { class: "plat", open: platState.open.has(p.key),
    ontoggle: (e) => { e.target.open ? platState.open.add(p.key) : platState.open.delete(p.key); } },
    el("summary", {},
      el("div", { class: "plat-main" }, el("b", {}, p.name),
        el("span", { class: "muted small" }, (p.regions || []).map((r) => REGION[r] || r).join(", "))),
      readyBadge),
    el("div", { class: "plat-body" },
      g.summary ? el("p", {}, g.summary) : null,
      g.how_it_works ? el("div", { class: "gsec" }, el("h4", {}, "How posts get seen"), el("p", {}, g.how_it_works)) : null,
      el("div", { class: "gfacts" },
        g.length ? el("div", {}, el("span", { class: "muted small" }, "Length that works"), el("div", {}, g.length)) : null,
        g.best_times ? el("div", {}, el("span", { class: "muted small" }, "When to post"), el("div", {}, g.best_times)) : null,
        g.earn ? el("div", {}, el("span", { class: "muted small" }, "Earning"), el("div", {}, g.earn)) : null),
      (g.tips || []).length ? el("div", { class: "gsec" }, el("h4", {}, "Tips"), el("ul", {}, g.tips.map((t) => el("li", {}, t)))) : null,
      el("div", { class: "gsec gvauto" }, el("h4", {}, "In vauto"),
        el("ul", {},
          el("li", {}, `${ROUTE_KIND[p.status] || p.status}${p.backend ? ` (${ROUTES[p.backend] || p.backend})` : ""}`),
          p.status !== "planned" ? el("li", {}, p.ready ? "Ready to post." : `To set up: ${p.detail}`) : el("li", {}, p.detail),
          p.video && p.video.max_s ? el("li", {}, `Video ${p.video.min_s || 0}–${p.video.max_s} s${p.video.max_mb ? `, up to ${p.video.max_mb} MB` : ""}`) : null,
          p.caption && p.caption.max_chars ? el("li", {}, `Caption up to ${p.caption.max_chars} characters${p.caption.max_hashtags ? `, ${p.caption.max_hashtags} hashtags` : ""}`) : null,
          p.rate_limit ? el("li", {}, `Limits: ${p.rate_limit}`) : null),
        p.status !== "planned" && !p.ready ? el("button", { class: "link", onclick: () => showTab("setup") }, "Open Setup →") : null)));
  return details;
}

$("#plat-q").addEventListener("input", (e) => { platState.q = e.target.value.trim(); renderPlatformTable(); });
$$("#tab-platforms .seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#tab-platforms .seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  platState.show = b.dataset.plat;
  renderPlatformTable();
}));

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
  history.replaceState(null, "", location.pathname + (location.hash || "#post"));
  renderMeters();
  updateButtons();
}

// --------------------------------------------------------------------- init
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* installing is optional */ });
}
loadSnippets();
loadStatus().then(async () => {
  await loadSharedUpload();
  updateButtons();
  const tab = location.hash.replace("#", "");
  if (tab && $(`#tab-${tab}`)) showTab(tab, false);
}).catch((err) => toast(err.message));
