const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  view: "create",
  config: null,
  checks: null,
  jobs: {},
  current: null,
  review: null,
  renderedJob: null,
  lastRevs: {},
  pendingRevs: {},
  sel: new Set(),
  debounce: {},
  videoEls: {},
  transcriptFetched: false,
};

const api = async (path, opts = {}) => {
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j); } catch {}
    throw new Error(msg);
  }
  return res.json();
};

function toast(msg, ms = 4000) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), ms);
}

function fmtDur(s) {
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m}m ${r}s` : `${r}s`;
}

function switchView(name) {
  state.view = name;
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  $$(".nav-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  if (name === "review" && state.review) {
    const j = state.jobs[state.review];
    if (j) renderReview(j);
  }
  if (name === "create") renderHistory();
}

async function loadInit() {
  try {
    const [setup, config] = await Promise.all([api("/api/setup"), api("/api/config")]);
    state.checks = setup;
    state.config = config;
    renderChecks();
    fillConfigForm();
    const bad = setup.failures.length;
    const ns = $("#nav-status");
    ns.textContent = bad ? `${bad} setup problem${bad > 1 ? "s" : ""}` : "All systems ready";
    ns.className = "nav-status " + (bad ? "warn" : "ok");
    $("#banner").hidden = bad === 0;
    $("#banner").textContent = bad
      ? `Setup issues found: ${setup.failures.join(", ")}. Open Setup to fix before processing video.`
      : "";
  } catch (e) {
    toast("Cannot reach backend: " + e.message);
  }
  poll();
  setInterval(poll, 1200);
}

async function poll() {
  try {
    const data = await api("/api/jobs");
    state.jobs = Object.fromEntries(data.jobs.map((j) => [j.id, j]));
  } catch (e) {
    if (state.current || state.review) toast("Backend unreachable: " + e.message);
    return;
  }
  if (state.current) {
    const j = state.jobs[state.current];
    if (j) paintProgress(j);
  }
  if (state.view === "review" && state.review) {
    const j = state.jobs[state.review];
    if (j) paintReview(j);
  }
  if (state.view === "create") renderHistory();
}

function paintProgress(j) {
  $("#progress-card").hidden = false;
  $("#progress-title").textContent = j.source || "Job";
  $("#progress-phase").textContent = j.phase;
  $("#progress-pct").textContent = Math.round(j.progress) + "%";
  $("#progress-fill").style.width = Math.round(j.progress) + "%";
  $("#progress-elapsed").textContent =
    j.status === "ready" ? "done"
    : j.status === "failed" ? "failed"
    : fmtElapsed(Date.now() / 1000 - j.created);
  const err = $("#progress-error");
  err.hidden = !j.error;
  err.textContent = j.error || "";
  $("#open-review-btn").hidden = j.status !== "ready";
  if (j.status === "failed") state.current = null;
}

function fmtElapsed(sec) {
  if (!isFinite(sec) || sec < 0) return "";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m > 0 ? m + "m " + s + "s" : s + "s";
}

function renderHistory() {
  const jobs = Object.values(state.jobs).sort((a, b) => b.created - a.created);
  const list = $("#history-list");
  list.innerHTML = "";
  $("#history-empty").hidden = jobs.length > 0;
  for (const j of jobs) {
    const el = document.createElement("div");
    el.className = "history-item";
    const badge = document.createElement("span");
    badge.className = "badge " + j.status;
    badge.textContent = j.status;
    const del = document.createElement("button");
    del.className = "small ghost";
    del.textContent = "Delete";
    del.onclick = async (e) => {
      e.stopPropagation();
      await api("/api/jobs/" + j.id, { method: "DELETE" });
      delete state.jobs[j.id];
      renderHistory();
    };
    el.appendChild(Object.assign(document.createElement("span"), { className: "src", textContent: j.source, title: j.source }));
    el.appendChild(badge);
    el.appendChild(del);
    el.onclick = () => openReview(j.id);
    list.appendChild(el);
  }
}

function openReview(jobId) {
  state.review = jobId;
  state.renderedJob = null;
  state.sel = new Set();
  state.lastRevs = {};
  state.pendingRevs = {};
  state.transcriptFetched = false;
  switchView("review");
  const j = state.jobs[jobId];
  if (j) renderReview(j);
}

function renderReview(j) {
  state.renderedJob = j.id;
  state.clipCount = j.clips.length;
  $("#review-title").textContent = j.source || "Project";
  const meta = [];
  if (j.info) {
    if (j.info.duration) meta.push(fmtDur(j.info.duration));
    if (j.info.width && j.info.height) meta.push(`${j.info.width}x${j.info.height}`);
    if (j.info.language) meta.push(j.info.language);
  }
  meta.push(`${j.clips.length} clips`);
  $("#review-meta").textContent = meta.join(" · ");
  $("#review-error").hidden = !j.error;
  $("#review-error").textContent = j.error || "";

  const grid = $("#clips-grid");
  grid.innerHTML = "";
  state.videoEls = {};
  for (const c of j.clips) {
    const card = buildClipCard(j, c);
    grid.appendChild(card);
    state.sel.add(c.cid);
  }
  renderTranscript(j);
  updateExportAllBtn(j);
  paintReview(j);
}

function renderTranscript(j) {
  const ol = $("#transcript-list");
  ol.innerHTML = "";
  if (!j.transcript || !j.transcript.length) return;
  for (const s of j.transcript) {
    const li = document.createElement("li");
    li.textContent = `${s.start.toFixed(1)}s – ${s.end.toFixed(1)}s  ${s.text}`;
    ol.appendChild(li);
  }
}

function buildClipCard(j, c) {
  const card = document.createElement("div");
  card.className = "clip-card";

  const preview = document.createElement("div");
  preview.className = "clip-preview";
  const video = document.createElement("video");
  video.controls = true;
  video.muted = true;
  video.playsInline = true;
  state.videoEls[c.cid] = video;
  preview.appendChild(video);
  const overlay = document.createElement("div");
  overlay.className = "preview-overlay";
  overlay.innerHTML = `<div class="spinner"></div><div class="muted">Rendering preview…</div>`;
  overlay.hidden = true;
  preview.appendChild(overlay);
  card.appendChild(preview);

  const body = document.createElement("div");
  body.className = "clip-body";
  const head = document.createElement("div");
  head.className = "clip-head";
  const title = document.createElement("div");
  title.className = "clip-title";
  title.textContent = c.title || `Clip ${c.cid}`;
  const score = document.createElement("div");
  score.className = "score";
  score.textContent = "★ " + c.score.toFixed(1);
  head.append(title, score);
  body.appendChild(head);

  const reason = document.createElement("div");
  reason.className = "clip-reason";
  reason.textContent = c.reason || "";
  body.appendChild(reason);

  const rangeStart = rangeRow("Start", c.start, 0, Math.max(0, (j.info?.duration || c.end) - 5), 0.1, (v) => (c._start = v));
  const rangeEnd = rangeRow("End", c.end, c.start + 5, j.info?.duration || c.end, 0.1, (v) => (c._end = v));
  body.appendChild(rangeStart.row);
  body.appendChild(rangeEnd.row);
  c._sliders = { start: rangeStart.input, end: rangeEnd.input };
  rangeStart.input.onchange = () => saveSettings(j, c);
  rangeEnd.input.onchange = () => saveSettings(j, c);

  const grid2 = document.createElement("div");
  grid2.className = "setting-grid";

  const capWrap = document.createElement("label");
  capWrap.innerHTML = `<span>Captions</span>`;
  const cap = document.createElement("input");
  cap.type = "checkbox";
  cap.checked = c.settings.captions;
  cap.onchange = () => saveSettings(j, c);
  capWrap.appendChild(cap);
  grid2.appendChild(capWrap);

  const styleWrap = document.createElement("label");
  styleWrap.innerHTML = `<span>Caption style</span>`;
  const styleSel = document.createElement("select");
  styleSel.innerHTML = `<option value="pop">Pop</option><option value="fade">Fade</option><option value="slam">Slam</option>`;
  styleSel.value = c.settings.style;
  styleSel.onchange = () => saveSettings(j, c);
  styleWrap.appendChild(styleSel);
  grid2.appendChild(styleWrap);

  const posWrap = document.createElement("label");
  posWrap.innerHTML = `<span>Position</span>`;
  const posSel = document.createElement("select");
  posSel.innerHTML = `<option value="bottom">Bottom</option><option value="center">Center</option><option value="top">Top</option>`;
  posSel.value = c.settings.position;
  posSel.onchange = () => saveSettings(j, c);
  posWrap.appendChild(posSel);
  grid2.appendChild(posWrap);

  const sizeWrap = document.createElement("label");
  sizeWrap.innerHTML = `<span>Size: <b class="val">${Math.round(c.settings.size * 100)}%</b></span>`;
  const sizeIn = document.createElement("input");
  sizeIn.type = "range";
  sizeIn.min = 0.6; sizeIn.max = 1.5; sizeIn.step = 0.05; sizeIn.value = c.settings.size;
  sizeIn.oninput = () => (sizeWrap.querySelector(".val").textContent = Math.round(sizeIn.value * 100) + "%");
  sizeIn.onchange = () => saveSettings(j, c);
  sizeWrap.appendChild(sizeIn);
  grid2.appendChild(sizeWrap);

  const zoomWrap = document.createElement("label");
  zoomWrap.innerHTML = `<span>Zoom: <b class="val">${c.settings.zoom.toFixed(1)}×</b></span>`;
  const zoomIn = document.createElement("input");
  zoomIn.type = "range";
  zoomIn.min = 1.0; zoomIn.max = 1.6; zoomIn.step = 0.05; zoomIn.value = c.settings.zoom;
  zoomIn.oninput = () => (zoomWrap.querySelector(".val").textContent = zoomIn.value.toFixed(1) + "×");
  zoomIn.onchange = () => saveSettings(j, c);
  zoomWrap.appendChild(zoomIn);
  grid2.appendChild(zoomWrap);
  body.appendChild(grid2);

  c._inputs = { cap, style: styleSel, pos: posSel, size: sizeIn, zoom: zoomIn };

  const faces = document.createElement("div");
  faces.className = "faces-row";
  const mkChip = (label, active, fn) => {
    const b = document.createElement("div");
    b.className = "face-chip" + (active ? " sel" : "");
    b.textContent = label;
    b.onclick = fn;
    return b;
  };
  const autoChip = mkChip("Auto", c.settings.focus_mode === "auto", () => setFocus(c, "auto", -1));
  const centerChip = mkChip("Center", c.settings.focus_mode === "center", () => setFocus(c, "center", -1));
  faces.appendChild(autoChip);
  faces.appendChild(centerChip);
  for (let i = 0; i < c.faces.length; i++) {
    const f = c.faces[i];
    const chip = document.createElement("div");
    chip.className = "face-chip" + (c.settings.focus_mode === "face" && c.settings.face_idx === i ? " sel" : "");
    chip.title = `${f.label} — on screen ${f.pct}%`;
    const img = document.createElement("img");
    img.src = f.thumb;
    img.loading = "lazy";
    const lbl = document.createElement("span");
    lbl.textContent = f.label;
    chip.append(img, lbl);
    chip.onclick = () => setFocus(c, "face", i);
    faces.appendChild(chip);
  }
  body.appendChild(faces);

  const actions = document.createElement("div");
  actions.className = "clip-actions";
  const selWrap = document.createElement("label");
  selWrap.style.cssText = "display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)";
  const selBox = document.createElement("input");
  selBox.type = "checkbox";
  selBox.checked = true;
  selBox.onchange = () => {
    selBox.checked ? state.sel.add(c.cid) : state.sel.delete(c.cid);
    updateExportAllBtn(j);
  };
  selWrap.append(selBox, document.createTextNode("Export"));
  const expBtn = document.createElement("button");
  expBtn.className = "small primary";
  expBtn.textContent = "Export";
  expBtn.onclick = () => exportClips(j, [c.cid]);
  const expState = document.createElement("div");
  expState.className = "export-state";
  actions.append(selWrap, expBtn, expState);
  body.appendChild(actions);
  card.appendChild(body);

  return card;
}

function rangeRow(label, value, min, max, step, oninput) {
  const row = document.createElement("div");
  row.className = "range-row";
  const lbl = document.createElement("div");
  lbl.className = "lbl";
  const name = document.createElement("span");
  name.textContent = label;
  const val = document.createElement("span");
  val.className = "val";
  const input = document.createElement("input");
  input.type = "range";
  input.min = min; input.max = max; input.step = step; input.value = value;
  val.textContent = value.toFixed(1) + "s";
  input.oninput = () => {
    val.textContent = parseFloat(input.value).toFixed(1) + "s";
    if (oninput) oninput(parseFloat(input.value));
  };
  lbl.append(name, val);
  row.append(lbl, input);
  return { row, input };
}

function setFocus(c, mode, idx) {
  c.settings.focus_mode = mode;
  c.settings.face_idx = idx;
  const j = state.jobs[state.review];
  saveSettings(j, c);
  $$(".face-chip").forEach((el) => el.classList.remove("sel"));
  const mine = mode === "auto" ? $$(".face-chip")[0] : mode === "center" ? $$(".face-chip")[1] : $$(".face-chip")[2 + idx];
  if (mine) mine.classList.add("sel");
}

function saveSettings(j, c, immediate) {
  clearTimeout(state.debounce[c.cid]);
  state.debounce[c.cid] = setTimeout(async () => {
    try {
      const body = {
        start: parseFloat(c._sliders.start.value),
        end: parseFloat(c._sliders.end.value),
        captions: c._inputs.cap.checked,
        style: c._inputs.style.value,
        position: c._inputs.pos.value,
        size: parseFloat(c._inputs.size.value),
        zoom: parseFloat(c._inputs.zoom.value),
        focus_mode: c.settings.focus_mode,
        face_idx: c.settings.face_idx,
      };
      const upd = await api(`/api/jobs/${j.id}/clips/${c.cid}/settings`, { method: "POST", body: JSON.stringify(body) });
      Object.assign(c, upd);
      state.pendingRevs[c.cid] = upd.preview.rev;
      const v = state.videoEls[c.cid];
      if (v && upd.preview.state === "ready") {
        v.src = upd.preview.url + "?v=" + upd.preview.rev;
        state.lastRevs[c.cid] = upd.preview.rev;
      }
    } catch (e) {
      toast("Settings save failed: " + e.message);
    }
  }, 650);
}

async function exportClips(j, ids) {
  try {
    await api(`/api/jobs/${j.id}/export`, { method: "POST", body: JSON.stringify({ clip_ids: ids }) });
  } catch (e) {
    toast("Export failed: " + e.message);
  }
}

function updateExportAllBtn(j) {
  const btn = $("#export-all-btn");
  btn.textContent = `Export selected (${state.sel.size})`;
}

function paintReview(j) {
  if (j.clips.length !== state.clipCount) {
    renderReview(j);
    return;
  }
  const anyBusy =
    j.status === "queued" || j.status === "failed" ||
    j.clips.some((c) => c.preview.state === "rendering" || c.export.state === "rendering");
  $("#review-progress-row").hidden = !anyBusy;
  if (anyBusy) {
    const pcts = j.clips.map((c) => {
      if (c.preview.state === "rendering") return c.preview.progress * 0.6;
      if (c.preview.state === "error") return 0;
      if (c.export.state === "rendering") return 60 + c.export.progress * 0.4;
      if (c.export.state === "done") return 100;
      return 100;
    });
    const avg = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : 0;
    $("#review-progress-fill").style.width = Math.round(avg) + "%";
    $("#review-progress-pct").textContent = Math.round(avg) + "%";
  }
  updateExportAllBtn(j);
  for (const c of j.clips) {
    const v = state.videoEls[c.cid];
    if (!v) continue;
    const overlay = v.closest(".clip-preview").querySelector(".preview-overlay");
    if (c.preview.state === "rendering") {
      overlay.hidden = false;
      overlay.querySelector(".muted").textContent = `Rendering preview… ${c.preview.progress}%`;
    } else if (c.preview.state === "error") {
      overlay.hidden = false;
      overlay.querySelector(".muted").textContent = "Preview failed: " + (c.preview.error || "error");
    } else {
      overlay.hidden = true;
    }
    if (c.preview.state === "ready" && c.preview.rev !== state.lastRevs[c.cid]) {
      v.src = c.preview.url + "?v=" + c.preview.rev;
      state.lastRevs[c.cid] = c.preview.rev;
    }
    const st = v.parentElement.parentElement.querySelector(".export-state");
    if (st) {
      if (c.export.state === "rendering") st.textContent = `Rendering ${c.export.progress}%…`;
      else if (c.export.state === "done" && c.export.url) st.innerHTML = `<a class="dl" href="${c.export.url}" download>Save MP4</a>`;
      else if (c.export.state === "error") st.textContent = "Failed: " + (c.export.error || "");
      else st.textContent = "";
    }
  }
}

function renderChecks() {
  const box = $("#checks");
  box.innerHTML = "";
  box.className = "checks";
  for (const c of state.checks.checks) {
    const row = document.createElement("div");
    row.className = `check-row ${c.status}`;
    const icon = document.createElement("div");
    icon.className = "check-icon";
    icon.textContent = c.status === "ok" ? "✓" : c.status === "warn" ? "!" : "✗";
    const name = document.createElement("div");
    name.className = "check-name";
    name.textContent = c.name;
    const detail = document.createElement("div");
    detail.className = "check-detail";
    detail.textContent = c.detail;
    row.append(icon, name, detail);
    box.appendChild(row);
  }
}

function fillConfigForm() {
  const c = state.config;
  $("#cfg-mode").value = c.mode;
  $("#cfg-whisper").value = c.whisper_model;
  $("#cfg-device").value = c.device;
  $("#cfg-ollama-url").value = c.ollama_url;
  $("#cfg-ollama-model").value = c.ollama_model;
  $("#cfg-api-base").value = c.api_base;
  $("#cfg-api-model").value = c.api_model;
  $("#cfg-api-key").value = c.api_key;
}

async function saveConfig() {
  const body = {
    mode: $("#cfg-mode").value,
    whisper_model: $("#cfg-whisper").value,
    device: $("#cfg-device").value,
    ollama_url: $("#cfg-ollama-url").value,
    ollama_model: $("#cfg-ollama-model").value,
    api_base: $("#cfg-api-base").value,
    api_model: $("#cfg-api-model").value,
    api_key: $("#cfg-api-key").value,
    max_clips: parseInt($("#opt-clips")?.value || "6", 10),
    min_len: parseInt($("#opt-minlen")?.value || "12", 10),
    max_len: parseInt($("#opt-maxlen")?.value || "45", 10),
  };
  await api("/api/config", { method: "POST", body: JSON.stringify(body) });
  $("#config-saved").hidden = false;
  setTimeout(() => ($("#config-saved").hidden = true), 2500);
  loadInit();
}

async function createJob() {
  const urlMode = $$(".input-card .tabs .tab").find((t) => t.classList.contains("active")).dataset.input;
  const options = {
    whisper_model: $("#opt-whisper").value,
    max_clips: parseInt($("#opt-clips").value, 10),
    min_len: parseInt($("#opt-minlen").value, 10),
    max_len: parseInt($("#opt-maxlen").value, 10),
  };
  let job;
  const btn = $("#process-btn");
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    if (urlMode === "url") {
      const url = $("#url-input").value.trim();
      if (!/^https?:\/\//.test(url)) throw new Error("Enter a valid YouTube URL");
      job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind: "url", source: url, options }) });
    } else {
      const file = $("#file-input").files[0];
      if (!file) throw new Error("Choose a video file first");
      const fd = new FormData();
      fd.append("file", file);
      fd.append("options", JSON.stringify(options));
      job = await api("/api/jobs/file", { method: "POST", body: fd });
    }
    state.current = job.id;
    paintProgress(job);
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create clips";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $$(".nav-tabs .tab").forEach((t) => (t.onclick = () => switchView(t.dataset.view)));
  $$(".input-card .tabs .tab").forEach((t) => {
    t.onclick = () => {
      $$(".input-card .tabs .tab").forEach((x) => x.classList.toggle("active", x === t));
      $("#input-file").hidden = t.dataset.input !== "file";
      $("#input-url").hidden = t.dataset.input !== "url";
    };
  });

  const dz = $("#dropzone");
  const fi = $("#file-input");
  dz.onclick = () => fi.click();
  fi.onchange = () => {
    $("#file-name").textContent = fi.files[0] ? fi.files[0].name : "";
  };
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
  dz.ondragleave = () => dz.classList.remove("drag");
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    const f = e.dataTransfer.files[0];
    if (f) {
      fi.files = e.dataTransfer.files;
      $("#file-name").textContent = f.name;
    }
  };

  $("#process-btn").onclick = createJob;
  $("#open-review-btn").onclick = () => openReview(state.current);
  $("#back-btn").onclick = () => switchView("create");
  $("#export-all-btn").onclick = () => {
    const j = state.jobs[state.review];
    if (j) exportClips(j, [...state.sel]);
  };
  $("#save-config-btn").onclick = saveConfig;

  loadInit();
});
