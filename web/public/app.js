const $ = (id) => document.getElementById(id);
const STEPS = ["writing", "narration", "characters", "video", "book"];
const LEVEL_NAMES = { "young-kids": "Young kids", kids: "Kids", "middle-grade": "Middle grade", teen: "Teens", adult: "Adults", mature: "Mature (R)", explicit: "Explicit (18+)", family: "Everyone" };
let seriesList = [];
let polling = null;
let followingId = null;          // the story this page is showing progress for

function formData() {
  const kind = document.querySelector('input[name="kind"]:checked').value;
  return {
    idea: $("idea").value.trim(),
    series: kind === "next" ? $("series").value : "",
    level: document.querySelector('input[name="level"]:checked').value,
    genre: $("genre").value,
    scenes: $("scenes").value,
    chapters: $("chapters").value,
    keywords: $("keywords").value,
    pov: $("pov").value,
    heat: $("heat").value,
    pacing: (document.querySelector('input[name="pacing"]:checked') || {}).value,
    language: (document.querySelector('input[name="language"]:checked') || {}).value,
    turnOns: $("turnOns").value,
    limits: $("limits").value,
    sounds: $("sounds").value,
    narrator: $("narratorPick").spec ? $("narratorPick").spec() : "",
    detail: document.querySelector('input[name="detail"]:checked').value,
    mode: document.querySelector('input[name="mode"]:checked').value,
    video: $("video").value,
    writer: $("writer").value,
    portrait: $("portrait").checked,
    book: $("book").checked,
  };
}

async function api(path, body, method) {
  const opts = body || method ? { method: method || "POST", headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined } : {};
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login.html"; return { ok: false, error: "Please sign in." }; }
  return res.json();
}

function show(view) {
  for (const v of ["stageEmpty", "priceView", "progressView", "resultView"]) $(v).hidden = v !== view;
  $("error").hidden = true;
  // On a phone the stage sits under the form, so bring it into view.
  if (view !== "stageEmpty" && matchMedia("(max-width: 860px)").matches) {
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("stage").scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  }
}
function showError(msg) { $("error").textContent = msg; $("error").hidden = false; }

// Explicit: local writer and voice only, so no video and no writer choice.
let features = {};
const HEAT_NAMES = { 1: "sensual", 2: "steamy", 3: "explicit", 4: "very explicit", 5: "maximum" };
$("heat").addEventListener("input", () => ($("heatOut").textContent = `${$("heat").value} · ${HEAT_NAMES[$("heat").value]}`));
function updateLevel() {
  const level = document.querySelector('input[name="level"]:checked').value;
  const explicit = level === "explicit";
  const video = document.querySelector('input[name="mode"][value="video"]');
  video.disabled = explicit && !features.localPictures;   // explicit video = free local pictures only
  $("explicitControls").hidden = !explicit;
  if (explicit && video.checked) document.querySelector('input[name="mode"][value="audio"]').checked = true;
  $("writer").disabled = explicit; $("video").disabled = explicit; $("book").disabled = explicit;
  $("explicitNote").hidden = !explicit;
}

async function loadFeatures() {
  const f = await api("/api/features");
  features = f;
  $("explicitChip").hidden = !f.adult;
  $("matureChip").hidden = !f.mature;
  const picked = document.querySelector('input[name="level"]:checked');
  if (picked && ((picked.value === "explicit" && !f.adult) || (picked.value === "mature" && !f.mature))) {
    document.querySelector('input[name="level"][value="family"]').checked = true;
    updateLevel();
  }
}

// ── voice picking (the free Kokoro voices), with a Listen button ──
let voiceData = null;
async function voices() {
  if (!voiceData) voiceData = await api("/api/voices");
  return voiceData;
}
const PITCHES = [["", "Normal pitch"], ["0.94", "A little lower"], ["0.9", "Lower"], ["1.06", "A little higher"], ["1.1", "Higher"]];
function parseSpec(spec) {
  const base = String(spec || "").split(/[@^]/)[0];
  const m = String(spec || "").match(/\^([\d.]+)/);
  return { base, pitch: m ? m[1] : "" };
}
// gender: "female" | "male" | "" (all). current: a voice spec to start on. auto: label for the automatic choice.
function voicePicker(box, { gender = "", current = "", auto = "Automatic" } = {}) {
  box.innerHTML = "";
  const sel = document.createElement("select");
  const pitch = document.createElement("select"); pitch.className = "pitch";
  const listen = document.createElement("button"); listen.type = "button"; listen.className = "small-btn"; listen.textContent = "Listen";
  const audio = new Audio();
  sel.setAttribute("aria-label", "Voice"); pitch.setAttribute("aria-label", "Pitch");
  sel.append(new Option(auto, ""));
  for (const [p, label] of PITCHES) pitch.append(new Option(label, p));
  box.append(sel, pitch, listen);
  box.spec = () => (sel.value ? sel.value + (pitch.value ? `^${pitch.value}` : "") : "");
  voices().then((v) => {
    if (!v.ok || !v.kokoro) { listen.hidden = true; pitch.hidden = true; sel.disabled = true; sel.options[0].text = "Automatic (voice picking needs the free Kokoro voices)"; return; }
    const groups = { female: "Women's voices", male: "Men's voices" };
    for (const g of gender ? [gender, gender === "female" ? "male" : "female"] : ["female", "male"]) {
      const og = document.createElement("optgroup"); og.label = groups[g];
      for (const x of v.voices.filter((y) => y.gender === g)) og.append(new Option(`${x.name} (${x.accent}, ${x.note})`, x.id));
      sel.append(og);
    }
    const { base, pitch: p } = parseSpec(current);
    if (base && [...sel.options].some((o) => o.value === base) && !String(current).includes("+")) { sel.value = base; pitch.value = PITCHES.some(([x]) => x === p) ? p : ""; }
    else if (current) { const o = new Option("Keep the current voice", "__keep__"); sel.insertBefore(o, sel.options[1]); sel.value = "__keep__"; box.spec = () => (sel.value === "__keep__" ? current.replace(/@[\d.]+$/, "") : sel.value ? sel.value + (pitch.value ? `^${pitch.value}` : "") : ""); }
  });
  listen.addEventListener("click", () => {
    const spec = box.spec() || (voiceData && voiceData.narrator) || "af_heart";
    if (spec.includes("+")) { listen.textContent = "Blended voice"; return; }
    listen.textContent = "Loading…";
    audio.src = `/api/voices/sample?v=${encodeURIComponent(spec)}`;
    audio.play().then(() => { listen.textContent = "Listen"; }).catch(() => { listen.textContent = "Listen"; });
  });
  return box;
}
voicePicker($("narratorPick"), { auto: "Automatic" });

// ── settings: my account, the grown-up switch (owner), and people (owner) ──
let me = null;
async function loadMe() {
  me = await api("/api/me");
  if (!me.ok) return;
  $("whoAmI").textContent = me.username;
}
$("signOut").addEventListener("click", async () => { await api("/api/logout", {}); location.href = "/login.html"; });

async function openSettings() {
  await loadMe();
  $("setUser").value = me.username || "";
  $("setPw").value = ""; $("setPw2").value = ""; $("setCurrent").value = "";
  $("setAdult").checked = !!me.adultSwitch;
  $("adultBlock").hidden = me.role !== "owner";
  $("peopleBlock").hidden = me.role !== "owner";
  $("settingsMsg").textContent = ""; $("settingsMsg").className = "settings-msg";
  $("peopleMsg").textContent = "";
  if (me.role === "owner") loadPeople();
  $("settings").showModal();
}
$("gearBtn").addEventListener("click", openSettings);
$("meForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("settingsMsg");
  const bad = (text) => { msg.textContent = text; msg.className = "settings-msg bad"; };
  if ($("setPw").value !== $("setPw2").value) return bad("The two new passwords don't match.");
  $("settingsSave").disabled = true;
  const r = await api("/api/me", { current: $("setCurrent").value, username: $("setUser").value,
    password: $("setPw").value, adult: $("setAdult").checked });
  $("settingsSave").disabled = false;
  if (!r.ok) return bad(r.error || "Couldn't save.");
  msg.className = "settings-msg"; msg.textContent = "Saved.";
  $("setCurrent").value = ""; $("setPw").value = ""; $("setPw2").value = "";
  await loadMe(); await loadFeatures(); loadSeries(); loadLibrary();
});

async function loadPeople() {
  const r = await api("/api/users");
  const list = $("peopleList");
  list.innerHTML = "";
  for (const u of r.users || []) {
    const card = document.createElement("div"); card.className = "person";
    const top = document.createElement("div"); top.className = "person-top";
    const name = document.createElement("span"); name.className = "person-name"; name.textContent = u.username;
    const meta = document.createElement("span"); meta.className = "person-meta";
    meta.textContent = `${u.role === "owner" ? "Owner (you)" : "Member"}, ${u.stories} stor${u.stories === 1 ? "y" : "ies"}`;
    top.append(name, meta); card.appendChild(top);
    if (u.role !== "owner") {
      const row1 = document.createElement("label"); row1.className = "check";
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = u.mature;
      cb.addEventListener("change", async () => { await api(`/api/users/${u.id}`, { mature: cb.checked }, "PATCH"); });
      row1.append(cb, document.createTextNode(" Can make Mature (R) stories"));
      const row2 = document.createElement("div"); row2.className = "person-row";
      const pw = document.createElement("input"); pw.type = "password"; pw.placeholder = "New password for them";
      pw.setAttribute("aria-label", `New password for ${u.username}`);
      const set = document.createElement("button"); set.type = "button"; set.className = "small-btn"; set.textContent = "Set password";
      set.addEventListener("click", async () => {
        const x = await api(`/api/users/${u.id}`, { password: pw.value }, "PATCH");
        $("peopleMsg").className = x.ok ? "settings-msg" : "settings-msg bad";
        $("peopleMsg").textContent = x.ok ? `New password set for ${u.username}.` : x.error;
        if (x.ok) pw.value = "";
      });
      const del = document.createElement("button"); del.type = "button"; del.className = "small-btn warn"; del.textContent = "Remove";
      del.addEventListener("click", async () => {
        if (del.dataset.sure !== "1") { del.dataset.sure = "1"; del.textContent = `Remove ${u.username}? Tap again`; return; }
        const x = await api(`/api/users/${u.id}`, null, "DELETE");
        $("peopleMsg").className = x.ok ? "settings-msg" : "settings-msg bad";
        $("peopleMsg").textContent = x.ok ? `${u.username} was removed. Their stories are now yours.` : x.error;
        loadPeople(); loadLibrary();
      });
      row2.append(pw, set, del);
      card.append(row1, row2);
    }
    list.appendChild(card);
  }
}
$("addPersonForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("/api/users", { username: $("newName").value, password: $("newPw").value, mature: $("newMature").checked });
  $("peopleMsg").className = r.ok ? "settings-msg" : "settings-msg bad";
  $("peopleMsg").textContent = r.ok ? `${$("newName").value} can now sign in.` : r.error;
  if (r.ok) { $("newName").value = ""; $("newPw").value = ""; $("newMature").checked = false; loadPeople(); }
});

function updateKind() {
  const next = document.querySelector('input[name="kind"]:checked').value === "next";
  $("seriesRow").hidden = !next;
  $("levelRow").hidden = next;            // a series keeps its own reading level
  $("ideaLabel").textContent = next ? "Anything that should happen this time? (optional)" : "What should the story be about?";
  $("idea").placeholder = next ? "They find a map in the attic — or leave it blank to be surprised" : "A lighthouse keeper who befriends a storm";
}

// ── price check ──
$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = formData();
  if (!data.series && !data.idea) { show("stageEmpty"); return showError("Type what the story should be about first."); }
  $("priceBtn").disabled = true;
  const r = await api("/api/estimate", data);
  $("priceBtn").disabled = false;
  if (!r.ok) { show("stageEmpty"); return showError(r.error || "Couldn't check the price."); }
  const total = r.estimate_usd === 0 ? "Free" : `About $${r.estimate_usd.toFixed(2)}`;
  $("priceText").textContent = total;
  const parts = Object.entries(r.breakdown || {}).filter(([, v]) => v >= 0.005).map(([k, v]) => `${k} $${v.toFixed(2)}`);
  const what = (r.series ? `Episode ${r.episode} of “${r.series}”. ` : "") +
    (r.chapters > 1 ? `${r.chapters} chapters, ${r.scenes} scenes. ` : "");
  $("priceDetail").textContent = what + (parts.length ? parts.join(", ") + "." : "Nothing here costs money with your current settings.");
  show("priceView");
});

$("changeBtn").addEventListener("click", () => { show("stageEmpty"); $("idea").focus(); });

// ── make it ──
$("makeBtn").addEventListener("click", async () => {
  $("makeBtn").disabled = true;
  const data = formData();
  const r = await api("/api/make", data);
  $("makeBtn").disabled = false;
  if (!r.ok) return showError(r.error || "Couldn't start the story.");
  setLamps("writing", data.mode, data.book);
  $("progressTitle").textContent = "Working on it";
  resetPreview();
  show("progressView");
  poll(r.job_id, data);
});

function setLamps(step, mode, book) {
  const active = step === "finishing" || step === "done" ? STEPS.length : STEPS.indexOf(step);
  document.querySelectorAll("#lamps li").forEach((li, i) => {
    const s = li.dataset.step;
    const skip = (mode === "story" && ["narration", "characters", "video"].includes(s)) ||
                 (mode === "audio" && ["characters", "video"].includes(s)) ||
                 (s === "book" && mode !== "video" && !book);
    li.className = skip ? "skip" : i < active ? "done" : i === active ? "now" : "";
  });
}

function setMeter(pct, detail) {
  $("meterFill").style.width = `${pct}%`;
  $("meter").setAttribute("aria-valuenow", String(pct));
  $("pct").textContent = `${pct}%`;
  if (detail) $("detail").textContent = detail;
}

function resetPreview() {
  setMeter(0, "Starting…");
  $("preview").hidden = true;
  $("previewImg").hidden = true; $("previewImg").removeAttribute("src");
  $("previewLogline").textContent = ""; $("previewText").textContent = "";
  $("previewStrip").innerHTML = "";
}

function showPreview(jobId, st) {
  const words = st.now || st.opening || "";
  if (st.logline || words || st.image) $("preview").hidden = false;
  $("previewLogline").textContent = st.logline || "";
  $("previewText").textContent = words ? (words.length >= 320 ? words + "…" : words) : "";
  const strip = $("previewStrip");
  for (const name of st.images || []) {
    if (strip.querySelector(`[data-name="${name}"]`)) continue;
    const img = document.createElement("img");
    img.dataset.name = name; img.alt = ""; img.src = `/api/status/${jobId}/preview/${name}`;
    img.addEventListener("click", () => { $("previewImg").src = img.src; });
    strip.appendChild(img);
  }
  strip.querySelectorAll("img").forEach((im) => im.classList.toggle("current", im.dataset.name === st.image));
  if (st.image) {
    const src = `/api/status/${jobId}/preview/${st.image}`;
    if (!$("previewImg").src.endsWith(src)) $("previewImg").src = src;
    $("previewImg").hidden = false;
  }
}

function poll(jobId, data) {
  clearInterval(polling);
  followingId = jobId;
  polling = setInterval(async () => {
    const st = await api(`/api/status/${jobId}`);
    if (st.state === "running" || st.state === "queued") {
      setLamps(st.step || "writing", data.mode, data.book);
      if (st.title) $("progressTitle").textContent = st.title;
      setMeter(st.progress || 0, st.state === "queued" ? "Getting started…" : st.detail);
      showPreview(jobId, st);
    } else if (st.state === "done") {
      setMeter(100, "Done");
      clearInterval(polling);
      followingId = null;
      showResult(st.result);
      loadLibrary();
      loadSeries();
    } else if (st.state === "failed") {
      clearInterval(polling);
      followingId = null;
      show("progressView");
      showError(`${st.error} Press Make it again to pick up where it stopped — nothing already made is paid for twice.`);
    }
  }, 2000);
}

function mediaBlock(s) {
  const wrap = document.createElement("div");
  if (s.video_url || s.video) {
    const v = document.createElement("video");
    v.controls = true; v.preload = "metadata"; v.src = s.video_url || s.video; v.playsInline = true;
    v.poster = s.thumb || v.src.replace(/story\.mp4$/, "thumb.jpg");
    const subs = s.subtitles || (s.subtitles_url && s.subtitles_url.replace(/\.srt$/, ".vtt"));
    if (subs) {
      const t = document.createElement("track");
      Object.assign(t, { kind: "captions", srclang: "en", label: "English", src: subs });
      v.appendChild(t);
    }
    wrap.appendChild(v);
  } else if (s.audio_url || s.audio) {
    const a = document.createElement("audio");
    a.controls = true; a.src = s.audio_url || s.audio;
    wrap.appendChild(a);
  }
  const links = document.createElement("div");
  links.className = "downloads";
  const add = (href, label) => { if (!href) return; const a = document.createElement("a"); a.href = href; a.textContent = label; a.download = ""; links.appendChild(a); };
  const sid = s.id || s.run_id;
  add(s.video_url || s.video, "Video");
  if (sid && (s.audiobook || s.audiobook_file)) add(`/api/stories/${encodeURIComponent(sid)}/audio/m4b`, "Audiobook (M4B, chapters)");
  if (sid && (s.audio || s.audio_url)) add(`/api/stories/${encodeURIComponent(sid)}/audio/mp3`, "Audio (MP3)");
  else add(s.audio_url || s.audio, "Audio");
  add(s.book_url || s.book, "Picture book (PDF)");
  add(s.subtitles_url || (s.subtitles && s.subtitles.replace(/\.vtt$/, ".srt")), "Subtitles (.srt)");
  add(s.story_url, "Story text");
  const id = s.id || s.run_id;
  if (id) add(`/api/stories/${encodeURIComponent(id)}/pdf`, "Story (PDF)");
  add(s.credits, "Sound credits");
  wrap.appendChild(links);
  return wrap;
}

// ── reading view: chapters, paragraphs, adjustable size, paper or night ──
const READER_KEY = "storymaker-reader";
function readerPrefs() {
  try { return JSON.parse(localStorage.getItem(READER_KEY)) || {}; } catch { return {}; }
}
function saveReaderPrefs(p) { try { localStorage.setItem(READER_KEY, JSON.stringify(p)); } catch { /* private mode */ } }

function renderReader(md) {
  const prefs = { size: 19, paper: false, ...readerPrefs() };
  const wrap = document.createElement("section");
  wrap.className = "reader" + (prefs.paper ? " paper" : "");
  wrap.style.setProperty("--reader-size", `${prefs.size}px`);

  const bar = document.createElement("div"); bar.className = "reader-bar";
  const mk = (label, title, fn) => {
    const b = document.createElement("button"); b.type = "button"; b.className = "reader-btn";
    b.textContent = label; b.title = title; b.setAttribute("aria-label", title); b.addEventListener("click", fn); return b;
  };
  let modeBtn;
  const apply = () => {
    wrap.style.setProperty("--reader-size", `${prefs.size}px`); wrap.classList.toggle("paper", prefs.paper);
    if (modeBtn) modeBtn.textContent = prefs.paper ? "Night" : "Paper";
    saveReaderPrefs(prefs);
  };
  bar.append(
    mk("A−", "Smaller text", () => { prefs.size = Math.max(15, prefs.size - 2); apply(); }),
    mk("A+", "Larger text", () => { prefs.size = Math.min(30, prefs.size + 2); apply(); }),
    (modeBtn = mk(prefs.paper ? "Night" : "Paper", "Switch between paper and night", () => { prefs.paper = !prefs.paper; apply(); })),
  );

  const body = document.createElement("div"); body.className = "reader-body";
  const toc = document.createElement("nav"); toc.className = "reader-toc"; toc.setAttribute("aria-label", "Chapters");
  let chapterNo = 0, firstPara = true, castBox = null;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("# ")) continue;
    if (line === "## Characters") {                                  // who is who, at the top
      castBox = document.createElement("dl"); castBox.className = "reader-cast";
      const h = document.createElement("div"); h.className = "reader-cast-h"; h.textContent = "Characters";
      body.append(h, castBox); continue;
    }
    const cm = castBox && line.match(/^- \*\*(.+?)\*\*: (.+)$/);
    if (cm) {
      const dt = document.createElement("dt"); dt.textContent = cm[1];
      const dd = document.createElement("dd"); dd.textContent = cm[2];
      castBox.append(dt, dd); continue;
    }
    castBox = null;
    if (/^\*\*.*\*\*$/.test(line)) continue;                       // the series subtitle (shown above)
    const ch = line.match(/^## Chapter (\d+): (.*)$/);
    if (ch) {
      chapterNo += 1;
      const h = document.createElement("h4"); h.className = "chapter"; h.id = `ch-${Math.random().toString(36).slice(2, 8)}`;
      const num = document.createElement("span"); num.textContent = `Chapter ${ch[1]}`;
      const ttl = document.createElement("span"); ttl.className = "chapter-title"; ttl.textContent = ch[2];
      h.append(num, ttl); body.appendChild(h);
      const a = document.createElement("a"); a.href = `#${h.id}`; a.textContent = `${ch[1]}. ${ch[2]}`;
      a.addEventListener("click", (e) => { e.preventDefault(); h.scrollIntoView({ behavior: "smooth", block: "start" }); });
      toc.appendChild(a);
      firstPara = true;
      continue;
    }
    const p = document.createElement("p");
    if (line === "*The End*") { p.className = "the-end"; p.textContent = "The End"; body.appendChild(p); continue; }
    if (/^\*[^*].*\*$/.test(line)) { p.className = "logline"; p.textContent = line.slice(1, -1); body.appendChild(p); continue; }
    p.textContent = line.replace(/\*\*?/g, "");
    if (firstPara) { p.className = "opening"; firstPara = false; }
    body.appendChild(p);
  }
  wrap.append(bar);
  if (chapterNo > 1) wrap.append(toc);
  wrap.append(body);
  return wrap;
}

// Delete with a second, explicit tap — no pop-up dialogs.
function deleteControl(s, onDone) {
  const id = s.id || s.run_id;
  const box = document.createElement("div");
  box.className = "danger";
  if (!id) return box;
  const ask = document.createElement("button");
  ask.type = "button"; ask.className = "quiet"; ask.textContent = "Delete this story";
  box.appendChild(ask);
  ask.addEventListener("click", () => {
    box.innerHTML = "";
    const msg = document.createElement("span"); msg.textContent = "Delete it for good? The video, audio and book go too.";
    const yes = document.createElement("button"); yes.type = "button"; yes.className = "delete"; yes.textContent = "Delete";
    const no = document.createElement("button"); no.type = "button"; no.className = "quiet"; no.textContent = "Keep it";
    box.append(msg, yes, no);
    yes.focus();
    no.addEventListener("click", () => { box.replaceWith(deleteControl(s, onDone)); });
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      const r = await fetch(`/api/stories/${encodeURIComponent(id)}`, { method: "DELETE" }).then((x) => x.json());
      if (!r.ok) { msg.textContent = r.error || "Couldn't delete it."; return; }
      onDone();
      loadLibrary(); loadSeries();
    });
  });
  return box;
}

function storyCard(s, el) {
  el.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = s.title; el.appendChild(h);
  const sub = document.createElement("p"); sub.className = "sub";
  const bits = [];
  if (s.series && s.episode > 1) bits.push(`Episode ${s.episode} of ${s.series}`);
  else if (s.episode === 1) bits.push("Episode 1");
  if (s.level) bits.push(`for ${LEVEL_NAMES[s.level] || s.level}`);
  const wc = (s.story || "").split(/\s+/).filter(Boolean).length;
  if (wc > 50) bits.push(`${wc.toLocaleString()} words, about ${Math.max(1, Math.round(wc / 150))} min read aloud`);
  sub.textContent = bits.join(", "); el.appendChild(sub);
  el.appendChild(mediaBlock(s));
  if (s.note) { const n = document.createElement("p"); n.className = "note"; n.textContent = s.note; el.appendChild(n); }
  const sid = s.id || s.run_id;
  if (sid) el.appendChild(storyTools(s, sid, el));
  el.appendChild(renderReader(s.story || ""));
  el.appendChild(deleteControl(s, () => {
    if ($("viewer").open) $("viewer").close();
    if (el.id === "resultView") show("stageEmpty");
  }));
}

// ── edit, rewrite with AI, remake audio ──
async function refreshCard(id, el) {
  const r = await api("/api/stories");
  const s = (r.stories || []).find((x) => x.id === id);
  if (s) storyCard(s, el);
  loadLibrary();
}

function jobMeter() {
  const box = document.createElement("div"); box.className = "job-meter";
  box.innerHTML = '<div class="meter"><div class="meter-fill"></div></div><p class="meter-text"><span class="d">Starting…</span><span class="p">0%</span></p>';
  box.set = (pct, detail) => {
    box.querySelector(".meter-fill").style.width = `${pct}%`;
    box.querySelector(".p").textContent = `${pct}%`;
    if (detail) box.querySelector(".d").textContent = detail;
  };
  return box;
}
function followJob(jobId, meter, done, fail) {
  const t = setInterval(async () => {
    const st = await api(`/api/status/${jobId}`);
    if (st.state === "running" || st.state === "queued") meter.set(st.progress || 0, st.state === "queued" ? "Getting started…" : st.detail);
    else if (st.state === "done") { clearInterval(t); meter.set(100, "Done"); done(); }
    else if (st.state === "failed" || st.ok === false) { clearInterval(t); fail(st.error || "It stopped."); }
  }, 2000);
}

function storyTools(s, id, el) {
  const wrap = document.createElement("div");
  const bar = document.createElement("div"); bar.className = "story-actions";
  const area = document.createElement("div");
  const btn = (label, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = "small-btn"; b.textContent = label; b.addEventListener("click", fn); return b; };
  const hasAudio = !!(s.audio || s.audio_url || s.video || s.video_url);
  if (s.audioOutdated) {
    const o = document.createElement("div"); o.className = "outdated";
    const tx = document.createElement("span"); tx.textContent = "The text changed since the audio was made.";
    o.append(tx, btn("Remake the audio to match", () => remakePanel(true)));
    wrap.appendChild(o);
  }
  bar.append(btn("Edit the text", editPanel), btn("Rewrite with AI", rewritePanel));
  bar.append(btn("Cast", castPanel));
  if (hasAudio) bar.append(btn("Voices", voicesPanel), btn("Sounds", soundsPanel), btn("Remake the audio", () => remakePanel(false)));
  bar.append(btn("Send to Audio Studio", () => {
    const base = features.studioUrl || `${location.protocol}//${location.hostname}:${location.protocol === "https:" ? 8443 : 8096}`;
    window.open(`${base.replace(/\/$/, "")}/?import=${encodeURIComponent(id)}`, "_blank", "noopener");
  }));
  if (s.request) bar.append(btn("Make a new version", () => newVersion(s.request)));
  wrap.append(bar, area);

  async function editPanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const ed = document.createElement("div"); ed.className = "editor";
    const tl = document.createElement("label"); tl.textContent = "Title";
    const ti = document.createElement("input"); ti.type = "text"; ti.value = full.title; ti.maxLength = 120;
    ed.append(tl, ti);
    const WORDS = { female: "a woman", male: "a man", girl: "a girl", boy: "a boy", old_woman: "an older woman", old_man: "an older man", creature: "a creature" };
    const cl = document.createElement("label"); cl.textContent = "Characters";
    const chint = document.createElement("span"); chint.className = "scene-label";
    chint.textContent = "One per line: the name, then woman, man, girl, boy, older woman, older man or creature. These are locked: the voices and the Audio Studio follow them.";
    const charsOrig = full.cast.map((c) => `${c.name}: ${WORDS[c.type] || "a woman"}`).join("\n");
    const ct = document.createElement("textarea"); ct.value = charsOrig; ct.rows = Math.max(3, full.cast.length + 1);
    ct.className = "chars-edit"; cl.htmlFor = ct.id = `ed-${id}-chars`;
    ed.append(cl, chint, ct);
    const boxes = [];
    for (const sc of full.scenes) {
      if (sc.chapterTitle) { const c = document.createElement("div"); c.className = "chapter-label"; c.textContent = `Chapter ${sc.chapter}: ${sc.chapterTitle}`; ed.appendChild(c); }
      const l = document.createElement("label"); l.className = "scene-label"; l.textContent = `Scene ${sc.index + 1}${sc.heading ? `: ${sc.heading}` : ""}`;
      const ta = document.createElement("textarea"); ta.value = sc.narration; ta.rows = Math.min(30, Math.max(6, Math.ceil(sc.narration.length / 75)));
      l.htmlFor = ta.id = `ed-${id}-${sc.index}`;
      boxes.push([sc.index, ta, sc.narration]);
      ed.append(l, ta);
    }
    const eb = document.createElement("div"); eb.className = "editor-bar";
    const save = document.createElement("button"); save.className = "go"; save.type = "button"; save.textContent = "Save changes";
    const cancel = btn("Cancel", () => { area.innerHTML = ""; });
    const msg = document.createElement("span"); msg.className = "settings-msg";
    eb.append(save, cancel, msg);
    ed.appendChild(eb);
    area.appendChild(ed);
    save.addEventListener("click", async () => {
      const scenes = {};
      for (const [i, ta, orig] of boxes) if (ta.value.trim() !== orig.trim()) scenes[i] = ta.value;
      save.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/edit`, { title: ti.value, scenes,
        ...(ct.value.trim() !== charsOrig.trim() ? { characters: ct.value } : {}) });
      save.disabled = false;
      if (!r.ok) { msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      refreshCard(id, el);
    });
  }

  async function rewritePanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Rewrite with AI</h4>';
    const tl = document.createElement("label"); tl.textContent = "What to rewrite";
    const sel = document.createElement("select");
    sel.append(new Option("The whole story", "story"));
    const chapters = [...new Set(full.scenes.filter((x) => x.chapter && x.chapterTitle).map((x) => `${x.chapter}|${x.chapterTitle}`))];
    for (const c of chapters) { const [n, title] = c.split("|"); sel.append(new Option(`Chapter ${n}: ${title}`, `chapter:${n}`)); }
    for (const sc of full.scenes) sel.append(new Option(`Scene ${sc.index + 1}${sc.heading ? `: ${sc.heading.slice(0, 50)}` : ""}`, `scene:${sc.index + 1}`));
    tl.htmlFor = sel.id = `rw-${id}`;
    const il = document.createElement("label"); il.textContent = "How should it change?";
    const ins = document.createElement("textarea"); ins.rows = 3; ins.placeholder = "e.g. Make it longer and more detailed, with more dialogue between them";
    il.htmlFor = ins.id = `rwi-${id}`;
    const kl = document.createElement("label"); kl.textContent = "Words or phrases to include";
    const kw = document.createElement("input"); kw.type = "text"; kw.value = full.keywords || ""; kw.maxLength = 600;
    kw.placeholder = "Separate them with commas"; kl.htmlFor = kw.id = `rwk-${id}`;
    const rl = document.createElement("label"); rl.className = "check";
    const rc = document.createElement("input"); rc.type = "checkbox"; rc.checked = true;
    rl.append(rc, document.createTextNode(" Remake the audio afterwards"));
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Rewrite it";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(tl, sel, il, ins, kl, kw, ...(full.hasAudio ? [rl] : []), go, msg);
    area.appendChild(p);
    go.addEventListener("click", async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/rewrite`, { target: sel.value, instructions: ins.value, remake: rc.checked, keywords: kw.value });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; });
    });
  }

  async function castPanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Cast</h4><p class="level-note">Tag who each character is. Tags are locked: the voices, the Audio Studio and every later episode follow them, and nothing changes them automatically.</p>';
    const TYPES = [["female", "Woman"], ["male", "Man"], ["girl", "Girl"], ["boy", "Boy"], ["old_woman", "Older woman"], ["old_man", "Older man"], ["creature", "Creature"]];
    const rows = [];
    if (!full.cast.length) p.insertAdjacentHTML("beforeend", '<p class="level-note">No speaking characters in this story.</p>');
    for (const c of full.cast) {
      const r = document.createElement("div"); r.className = "voice-row";
      const w = document.createElement("div"); w.className = "who";
      w.textContent = c.name;
      if (c.confirmed) { const sm = document.createElement("small"); sm.textContent = " (tagged)"; w.appendChild(sm); }
      const sel = document.createElement("select"); sel.setAttribute("aria-label", `Who ${c.name} is`);
      for (const [v, label] of TYPES) sel.append(new Option(label, v));
      sel.value = TYPES.some(([v]) => v === c.type) ? c.type : "female";
      r.append(w, sel); p.appendChild(r); rows.push([c.name, sel]);
    }
    const go = document.createElement("button"); go.className = "go"; go.type = "button";
    go.textContent = full.hasAudio ? "Save tags and remake the audio" : "Save tags";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(go, msg); area.appendChild(p);
    go.addEventListener("click", async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/cast`, { cast: rows.map(([name, sel]) => ({ name, voice: sel.value })) });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      if (!r.job_id) { msg.className = "settings-msg"; msg.textContent = "Saved."; go.disabled = false; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; });
    });
  }

  async function voicesPanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Voices</h4><p class="level-note">Pick who reads each part. Women\'s voices are listed first for women, men\'s for men. Press Listen to hear one.</p>';
    const rows = [];
    const row = (label, opts) => {
      const r = document.createElement("div"); r.className = "voice-row";
      const w = document.createElement("div"); w.className = "who"; w.innerHTML = label;
      const box = document.createElement("div"); box.className = "voice-pick";
      voicePicker(box, opts);
      r.append(w, box); p.appendChild(r);
      return box;
    };
    const narr = row("Narrator", { current: full.voiceChoice.narrator || "", auto: "Automatic" });
    for (const c of full.cast) {
      const g = ["female", "girl", "old_woman"].includes(c.type) ? "female" : ["male", "boy", "old_man"].includes(c.type) ? "male" : "";
      const chosen = (full.voiceChoice.characters || {})[c.name] || c.voice || "";
      const label = `${c.name.replace(/[<>&]/g, "")} <small>${g === "female" ? "woman" : g === "male" ? "man" : ""}${c.type && !["female", "male"].includes(c.type) ? ` (${c.type.replace("_", " ")})` : ""}</small>`;
      rows.push([c.name, row(label, { gender: g, current: chosen, auto: "Automatic" })]);
    }
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Use these voices and remake the audio";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(go, msg); area.appendChild(p);
    go.addEventListener("click", async () => {
      go.disabled = true;
      const characters = {};
      for (const [name, box] of rows) { const sp = box.spec(); if (sp) characters[name] = sp; }
      const r = await api(`/api/stories/${encodeURIComponent(id)}/voices`, { narrator: narr.spec(), characters });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; });
    });
  }

  async function soundsPanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Sounds</h4><p class="level-note">Change what plays under each scene. Background: where the scene happens ("rain on a window"). Effects: short sounds, and the words they play on (leave the words blank to play near the start).</p>';
    const wl = document.createElement("label"); wl.textContent = "Sounds you want to hear in this story";
    const wi = document.createElement("input"); wi.type = "text"; wi.maxLength = 300; wi.value = full.soundWishes || "";
    wi.placeholder = "rain, thunder, seagulls, a crackling fire"; wl.htmlFor = wi.id = `sw-${id}`;
    const ai = document.createElement("button"); ai.type = "button"; ai.className = "small-btn"; ai.textContent = "Let the AI choose the sounds again, using these";
    p.append(wl, wi, ai);
    const editors = [];
    for (const sc of full.scenes) {
      const box = document.createElement("div"); box.className = "sound-scene";
      const nm = document.createElement("div"); nm.className = "scene-name";
      nm.textContent = `${sc.chapterTitle ? `Chapter ${sc.chapter}: ${sc.chapterTitle}. ` : ""}Scene ${sc.index + 1}${sc.heading ? `: ${sc.heading.slice(0, 60)}` : ""}`;
      const amb = document.createElement("input"); amb.type = "text"; amb.maxLength = 80;
      amb.value = sc.sound && sc.sound.ambience && sc.sound.ambience !== "none" ? sc.sound.ambience : "";
      amb.placeholder = "Background (leave blank for none)"; amb.setAttribute("aria-label", `Background for scene ${sc.index + 1}`);
      const fxBox = document.createElement("div"); fxBox.style.display = "grid"; fxBox.style.gap = "6px";
      const fxRows = [];
      const addFx = (f = { sound: "", at: "" }) => {
        if (fxRows.length >= 6) return;
        const r = document.createElement("div"); r.className = "fx-row";
        const so = document.createElement("input"); so.type = "text"; so.maxLength = 60; so.value = f.sound; so.placeholder = "Effect, e.g. door knock";
        const at = document.createElement("input"); at.type = "text"; at.maxLength = 120; at.value = f.at; at.placeholder = "Plays on these words (optional)";
        const rm = document.createElement("button"); rm.type = "button"; rm.className = "small-btn warn"; rm.textContent = "Remove";
        rm.addEventListener("click", () => { r.remove(); fxRows.splice(fxRows.indexOf(entry), 1); });
        const entry = [so, at];
        r.append(so, at, rm); fxBox.appendChild(r); fxRows.push(entry);
      };
      for (const f of (sc.sound && sc.sound.effects) || []) addFx(f);
      const more = document.createElement("button"); more.type = "button"; more.className = "small-btn"; more.textContent = "Add an effect";
      more.addEventListener("click", () => addFx());
      box.append(nm, amb, fxBox, more); p.appendChild(box);
      editors.push([sc.index, amb, fxRows]);
    }
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Save these sounds and remake the audio";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(go, msg); area.appendChild(p);
    const send = async (redesign) => {
      go.disabled = ai.disabled = true;
      const scenes = {};
      for (const [i, amb, fx] of editors) scenes[i] = { ambience: amb.value.trim() || "none", effects: fx.map(([so, at]) => ({ sound: so.value, at: at.value })).filter((f) => f.sound.trim()) };
      const r = await api(`/api/stories/${encodeURIComponent(id)}/sounds`, { wishes: wi.value, scenes, redesign });
      if (!r.ok) { go.disabled = ai.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = ai.disabled = false; });
    };
    go.addEventListener("click", () => send(false));
    ai.addEventListener("click", () => send(true));
  }

  function remakePanel(quick) {
    area.innerHTML = "";
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Remake the audio</h4><p class="level-note">Records any changed lines, designs the sound again with your current sound files, and remasters it. Lines that didn\'t change are reused, so it\'s quick.</p>';
    const mk = (label) => { const l = document.createElement("label"); l.className = "check"; const c = document.createElement("input"); c.type = "checkbox"; l.append(c, document.createTextNode(" " + label)); return [l, c]; };
    const [vl, vc] = mk("Choose new character voices");
    const [sl, scb] = mk("Pick the sounds again");
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Remake the audio";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(vl, sl, go, msg);
    area.appendChild(p);
    const start = async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/remaster`, { revoice: vc.checked, redesign: scb.checked });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; });
    };
    go.addEventListener("click", start);
    if (quick) start();
  }
  return wrap;
}

// Fill the form with a story's settings so a fresh version can be made.
function newVersion(rq) {
  if ($("viewer").open) $("viewer").close();
  document.querySelector('input[name="kind"][value="new"]').checked = true; updateKind();
  $("idea").value = rq.idea || "";
  $("genre").value = rq.genre || "";
  $("keywords").value = rq.keywords || "";
  $("sounds").value = rq.sounds || "";
  const lv = document.querySelector(`input[name="level"][value="${rq.level}"]`);
  if (lv && !lv.closest("label").hidden) lv.checked = true;
  const md = document.querySelector(`input[name="mode"][value="${rq.mode}"]`); if (md && !md.disabled) md.checked = true;
  const dt = document.querySelector(`input[name="detail"][value="${rq.detail}"]`); if (dt) dt.checked = true;
  if (rq.chapters) $("chapters").value = String(rq.chapters);
  if (rq.scenes) $("scenes").value = String(rq.scenes);
  $("pov").value = rq.pov || "";
  if (rq.heat) { $("heat").value = String(rq.heat); $("heat").dispatchEvent(new Event("input")); }
  for (const k of ["pacing", "language"]) { const r = document.querySelector(`input[name="${k}"][value="${rq[k]}"]`); if (r) r.checked = true; }
  $("turnOns").value = rq.turn_ons || ""; $("limits").value = rq.limits || "";
  voicePicker($("narratorPick"), { current: rq.narrator || "", auto: "Automatic" });
  updateLevel(); updateLength();
  show("stageEmpty");
  $("make").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $("idea").focus();
}

function showResult(r) {
  const el = $("resultView");
  el.className = "result";
  storyCard(r, el);
  show("resultView");
}

// ── library ──
async function loadLibrary() {
  const r = await api("/api/stories");
  const filter = $("filter").value;
  const shelf = $("shelf");
  shelf.innerHTML = "";
  const list = (r.stories || []).filter((s) => !filter || s.series === filter);
  $("shelfEmpty").hidden = list.length > 0;
  for (const s of list) {
    const b = document.createElement("button");
    b.className = "cover"; b.type = "button";
    if (s.thumb) {
      const img = document.createElement("img"); img.className = "art"; img.src = s.thumb; img.alt = ""; img.loading = "lazy";
      b.appendChild(img);
    } else {
      const d = document.createElement("div"); d.className = "noart"; d.textContent = (s.title || "?").slice(0, 1); b.appendChild(d);
    }
    const meta = document.createElement("div"); meta.className = "meta";
    const t = document.createElement("div"); t.className = "title"; t.textContent = s.title;
    const sm = document.createElement("div"); sm.className = "small";
    const what = s.video ? "Video" : s.audio ? "Audio" : "Story";
    sm.textContent = `${what}${s.episode > 1 ? `, episode ${s.episode}` : ""}${s.level ? `, ${LEVEL_NAMES[s.level] || s.level}` : ""}${s.by ? `, by ${s.by}` : ""}`;
    meta.append(t, sm); b.appendChild(meta);
    b.addEventListener("click", () => { storyCard(s, $("viewerBody")); $("viewer").showModal(); });
    shelf.appendChild(b);
  }
}

async function loadSeries() {
  const r = await api("/api/series");
  seriesList = (r.series || []);
  const sel = $("series"), filt = $("filter"), keep = filt.value;
  sel.innerHTML = ""; filt.innerHTML = '<option value="">All stories</option>';
  for (const s of seriesList) {
    const o = document.createElement("option");
    o.value = s.name; o.textContent = `${s.name} (${s.episodes} episode${s.episodes === 1 ? "" : "s"})`;
    sel.appendChild(o);
    filt.appendChild(o.cloneNode(true));
  }
  filt.value = keep;
  const nextRadio = document.querySelector('input[name="kind"][value="next"]');
  nextRadio.disabled = seriesList.length === 0;
  nextRadio.closest("label").title = seriesList.length ? "" : "Make a story first";
}

$("viewer").addEventListener("close", () => $("viewerBody").querySelectorAll("video,audio").forEach((m) => m.pause()));
$("filter").addEventListener("change", loadLibrary);
document.querySelectorAll('input[name="kind"]').forEach((r) => r.addEventListener("change", updateKind));
document.querySelectorAll('input[name="level"]').forEach((r) => r.addEventListener("change", updateLevel));
const DETAIL_X = { brief: 0.6, standard: 1, rich: 1.8, epic: 2.6 };
function updateLength() {
  const ch = parseInt($("chapters").value, 10), sc = parseInt($("scenes").value, 10);
  const detail = document.querySelector('input[name="detail"]:checked').value;
  const video = document.querySelector('input[name="mode"]:checked').value === "video";
  $("scenesOut").textContent = sc;
  $("scenesLabel").textContent = ch > 1 ? "Scenes per chapter" : "Scenes";
  const prose = !video && (ch > 1 || detail === "rich" || detail === "epic");
  const words = Math.round(ch * sc * (prose ? 160 : 85) * DETAIL_X[detail]);
  const mins = Math.max(1, Math.round(words / 150));
  const note = $("lengthNote");
  note.hidden = false;
  note.textContent = `About ${words.toLocaleString()} words, roughly ${mins} minute${mins === 1 ? "" : "s"} read aloud.` +
    (ch > 1 ? " Each chapter is written separately, so long stories take a while; you can close the page." : "") +
    (video && detail !== "standard" ? " With AI video clips, scenes stay short to fit each 8-second clip." : "");
}
$("scenes").addEventListener("input", updateLength);
$("chapters").addEventListener("change", updateLength);
document.querySelectorAll('input[name="detail"], input[name="mode"]').forEach((r) => r.addEventListener("change", updateLength));

updateLength();
loadMe();
loadFeatures();
loadSeries();
loadLibrary();

// ── the same work on every device: a story started on the Mac shows its progress on the iPad and iPhone too ──
const JOB_NAMES = { remaster: "Remaking the audio", rewrite: "Rewriting" };
const seenDone = new Set();
async function checkActive() {
  if (document.hidden) return;
  const r = await api("/api/jobs/active");
  if (!r.ok) return;
  const others = [];
  for (const j of r.jobs) {
    const live = j.state === "running" || j.state === "queued";
    if (j.kind === "make") {
      if (live && !followingId && $("priceView").hidden) {        // pick up a story started somewhere else
        const info = j.info || {};
        setLamps(j.step || "writing", info.mode || "story", info.book);
        $("progressTitle").textContent = j.title || "Working on it";
        resetPreview();
        setMeter(j.progress || 0, j.detail || "Getting started…");
        show("progressView");
        poll(j.id, { mode: info.mode || "story", book: !!info.book });
      }
      if (!live && !seenDone.has(j.id)) { seenDone.add(j.id); if (j.id !== followingId) loadLibrary(); }
    } else if (live) {
      others.push(j);
    } else if (!seenDone.has(j.id)) {
      seenDone.add(j.id); loadLibrary();
    }
  }
  const box = $("activeJobs");
  box.replaceChildren(...others.map((j) => {
    const row = document.createElement("div"); row.className = "active-job";
    const what = document.createElement("span"); what.className = "aj-what";
    what.textContent = `${JOB_NAMES[j.kind] || "Working"}${j.storyTitle ? `: ${j.storyTitle}` : ""}`;
    const bar = document.createElement("div"); bar.className = "meter";
    const fill = document.createElement("div"); fill.className = "meter-fill"; fill.style.width = `${j.progress || 0}%`;
    bar.appendChild(fill);
    const det = document.createElement("span"); det.className = "aj-detail";
    det.textContent = `${j.progress || 0}%${j.detail ? ` · ${j.detail}` : ""}`;
    row.append(what, bar, det);
    return row;
  }));
  box.hidden = !others.length;
}
checkActive();
setInterval(checkActive, 6000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkActive(); });
window.addEventListener("focus", checkActive);
