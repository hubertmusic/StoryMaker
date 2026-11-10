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
    bedtime: $("bedtime").checked,
    adventure: $("adventure").checked,
    bigMoments: $("bigMoments").value,
    // the next episode keeps its series' look unless you picked another one for it on purpose
    pictureStyle: kind === "next" && !pictureStyleTouched ? "" : (document.querySelector('input[name="pictureStyle"]:checked') || {}).value,
    useStyle: $("useStyle").checked,
    proofread: $("proofread").checked,
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
  const pics = !!features.localPictures;                  // pictures drawn by the picture app on this Mac
  video.disabled = explicit && !pics;                     // explicit video = free local pictures only
  $("explicitControls").hidden = !explicit;
  if (explicit && !pics && video.checked) document.querySelector('input[name="mode"][value="audio"]').checked = true;
  $("writer").disabled = explicit; $("video").disabled = explicit; $("book").disabled = explicit && !pics;
  if (explicit && !pics) $("book").checked = false;
  $("explicitNote").hidden = !explicit;
  $("explicitPics").textContent = pics
    ? "Pictures, the picture book and the video's moving pictures are drawn by the picture app on your Mac (Draw Things), tasteful only: everyone fully clothed. Keep Draw Things open with its API server on."
    : "For pictures, a picture book or video, open Draw Things on your Mac, turn on Settings → API Server (HTTP, port 7860), and set LOCAL_IMAGE_URL in storymaker.env. They're drawn on your Mac and stay tasteful.";
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
    const who = { female: "Women", male: "Men" };
    const kinds = [["standard", "voices"], ["character", "character voices"], ["accent", "with an accent"]];
    for (const g of gender ? [gender, gender === "female" ? "male" : "female"] : ["female", "male"]) {
      for (const [k, label] of kinds) {
        const list = v.voices.filter((y) => y.gender === g && (y.group || "standard") === k);
        if (!list.length) continue;
        const og = document.createElement("optgroup"); og.label = `${who[g]}: ${label}`;
        for (const x of list) og.append(new Option(`${x.name} (${x.accent}, ${x.note})`, x.id));
        sel.append(og);
      }
    }
    const { base, pitch: p } = parseSpec(current);
    if (base && [...sel.options].some((o) => o.value === base)) { sel.value = base; pitch.value = PITCHES.some(([x]) => x === p) ? p : ""; }
    else if (current) { const o = new Option("Keep the current voice", "__keep__"); sel.insertBefore(o, sel.options[1]); sel.value = "__keep__"; box.spec = () => (sel.value === "__keep__" ? current.replace(/@[\d.]+$/, "") : sel.value ? sel.value + (pitch.value ? `^${pitch.value}` : "") : ""); }
  });
  listen.addEventListener("click", () => {
    const spec = box.spec() || (voiceData && voiceData.narrator) || "af_heart";
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
  loadNotify().catch(() => {});
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
  if (typeof updatePictureStyle === "function") { pictureStyleTouched = false; updatePictureStyle(); }
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
  renderChecks(r.checks || []);
  show("priceView");
});

// what the story needs, checked before it starts (the writer, voices, picture app, sounds, disk)
function renderChecks(checks) {
  const ul = $("checks");
  ul.replaceChildren();
  for (const c of checks) {
    const li = document.createElement("li");
    const autoOpen = !c.ok && c.item === "Pictures" && /Draw Things/.test(c.detail);
    li.className = c.ok ? "ok" : c.blocking && !autoOpen ? "bad" : "warn";
    const mark = document.createElement("span"); mark.className = "mark"; mark.textContent = c.ok ? "✓" : c.blocking && !autoOpen ? "✕" : "!";
    const txt = document.createElement("span");
    txt.textContent = `${c.item}: ${c.detail}` + (autoOpen ? ". It will be opened for you when you press Make it." : "")
      + (!c.ok && c.fix && !autoOpen ? ` ${c.fix}` : "");
    li.append(mark, txt); ul.appendChild(li);
  }
  const blocked = checks.some((c) => c.blocking && !(c.item === "Pictures" && /Draw Things/.test(c.detail)));
  $("makeBtn").disabled = blocked;
  $("makeBtn").title = blocked ? "Fix the item marked ✕ first" : "";
}
$("changeBtn").addEventListener("click", () => { show("stageEmpty"); $("idea").focus(); });

// ── tonight: made overnight, one after another ──
$("tonightBtn").addEventListener("click", async () => {
  $("tonightBtn").disabled = true;
  const r = await api("/api/queue", formData());
  $("tonightBtn").disabled = false;
  if (!r.ok) { $("tonightMsg").textContent = r.error || "Couldn't add it."; return; }
  $("tonightMsg").textContent = r.night
    ? `Added. It's night time, so it starts as soon as nothing else is being made (${r.waiting} on the list).`
    : `Added to tonight's list (${r.waiting} waiting). They're made between ${r.from} and ${r.to}.`;
  loadQueue();
});
const MODE_NAMES = { story: "Story", audio: "Audio", video: "Video" };
async function loadQueue() {
  const r = await api("/api/queue", null, "GET").catch(() => null);
  const panel = $("queuePanel");
  if (!r || !r.ok || !r.items.length) { panel.hidden = true; return; }
  panel.hidden = false;
  const waiting = r.items.filter((i) => i.state === "waiting");
  $("queueHint").textContent = waiting.length
    ? (r.night ? "Night time: these are being made one after another." : `Made between ${r.from} and ${r.to}, one after another. Change the hours with QUEUE_START and QUEUE_END in storymaker.env.`)
    : "Last night's list.";
  const list = $("queueList"); list.replaceChildren();
  r.items.forEach((q) => {
    const row = document.createElement("div"); row.className = "queue-row";
    const t = document.createElement("div"); t.className = "q-title"; t.textContent = q.title;
    const sub = document.createElement("div"); sub.className = "small";
    const bits = [MODE_NAMES[q.mode] || q.mode];
    if (q.chapters > 1) bits.push(`${q.chapters} chapters`);
    if (q.level) bits.push(LEVEL_NAMES[q.level] || q.level);
    if (q.bedtime) bits.push("bedtime");
    if (q.by) bits.push(`by ${q.by}`);
    if (q.state === "started") bits.push("started");
    if (q.state === "failed") bits.push(`couldn't start: ${q.error || ""}`);
    else if (q.state === "waiting" && q.error) bits.push(`will try again (${q.error.slice(0, 120)})`);
    sub.textContent = bits.join(", ");
    const txt = document.createElement("div"); txt.className = "q-text"; txt.append(t, sub);
    const tools = document.createElement("div"); tools.className = "q-tools";
    const b = (label, title, fn) => { const x = document.createElement("button"); x.type = "button"; x.className = "small-btn"; x.textContent = label; x.title = title; x.setAttribute("aria-label", title); x.addEventListener("click", fn); return x; };
    if (q.state === "waiting") {
      const i = waiting.indexOf(q);
      if (i > 0) tools.appendChild(b("↑", "Make it sooner", async () => { await api(`/api/queue/${q.id}/move`, { dir: -1 }); loadQueue(); }));
      if (i < waiting.length - 1) tools.appendChild(b("↓", "Make it later", async () => { await api(`/api/queue/${q.id}/move`, { dir: 1 }); loadQueue(); }));
      tools.appendChild(b("Start now", "Don't wait for tonight", async (e) => {
        e.target.disabled = true; const x = await api(`/api/queue/${q.id}/now`, {});
        if (!x.ok) sub.textContent = x.error || "Couldn't start it."; else { loadQueue(); loadUnfinished(); }
      }));
      tools.appendChild(b("Remove", "Take it off the list", async () => { await api(`/api/queue/${q.id}`, null, "DELETE"); loadQueue(); }));
    } else {
      tools.appendChild(b("Clear", "Take it off the list", async () => { await api(`/api/queue/${q.id}`, null, "DELETE"); loadQueue(); }));
    }
    row.append(txt, tools); list.appendChild(row);
  });
}

// ── phone notifications ──
async function loadNotify(r) {
  r = r || await api("/api/notify", null, "GET");
  const box = $("notifyBody"); box.replaceChildren();
  const btn = (label, cls, fn) => { const x = document.createElement("button"); x.type = "button"; x.className = cls; x.textContent = label; x.addEventListener("click", fn); return x; };
  const msg = document.createElement("p"); msg.className = "settings-msg"; msg.setAttribute("role", "status");
  if (!r.on) {
    box.append(btn("Turn on phone notifications", "go go-small", async () => loadNotify(await api("/api/notify", { on: true }))), msg);
    return;
  }
  const steps = document.createElement("ol"); steps.className = "notify-steps";
  const li = (nodes) => { const l = document.createElement("li"); l.append(...nodes); steps.appendChild(l); };
  li(["Install the free ", Object.assign(document.createElement("b"), { textContent: "ntfy" }), " app on your phone (App Store or Google Play)."]);
  const code = document.createElement("code"); code.textContent = r.topic;
  const copy = btn("Copy", "small-btn", async () => { try { await navigator.clipboard.writeText(r.topic); copy.textContent = "Copied"; } catch { copy.textContent = "Select it and copy"; } });
  li(["Tap + and subscribe to this topic: ", code, " ", copy]);
  if (!/^https:\/\/ntfy\.sh$/.test(r.server)) li([`In the app, choose "Use another server" and type ${r.server}.`]);
  li(["Send a test to check it arrives."]);
  const row = document.createElement("div"); row.className = "cs-row";
  row.append(btn("Send a test", "small-btn", async () => {
    msg.textContent = "Sending…";
    const x = await api("/api/notify", { test: true });
    msg.textContent = x.sent ? "Sent. It should pop up on your phone in a few seconds." : "Couldn't send it. Is the server online?";
  }), btn("Turn off", "small-btn warn", async () => loadNotify(await api("/api/notify", { on: false }))));
  const note = document.createElement("p"); note.className = "level-note";
  note.textContent = "The topic is your private channel: anyone who knows it could read the messages, so they only ever say a story's title (and not even that for Explicit ones). Audio Studio on the Mac uses it too.";
  box.append(steps, row, msg, note);
}

// ── make it ──
$("makeBtn").addEventListener("click", async () => {
  $("makeBtn").disabled = true;
  const data = formData();
  $("makeBtn").textContent = "Checking everything is ready…";
  const r = await api("/api/make", data);
  $("makeBtn").disabled = false; $("makeBtn").textContent = "Make it";
  if (!r.ok) { if (r.checks) renderChecks(r.checks); return showError(r.error || "Couldn't start the story."); }
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
  $("stopStory").hidden = false; $("stopStory").disabled = false; $("stopStory").textContent = "Stop";
  $("pauseStory").hidden = false; $("pauseStory").disabled = false; $("pauseStory").textContent = "Pause";
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
      $("stopStory").hidden = true; $("pauseStory").hidden = true;
      showResult(st.result);
      loadLibrary();
      loadSeries();
    } else if (st.state === "stopped" || st.state === "paused") {
      clearInterval(polling);
      followingId = null;
      $("stopStory").hidden = true; $("pauseStory").hidden = true;
      setMeter(st.progress || 0, st.state === "paused" ? "Paused" : "Stopped");
      show("progressView");
      showError(st.detail || "Stopped. What was already made is kept in Unfinished stories.");
      loadUnfinished();
    } else if (st.state === "failed") {
      clearInterval(polling);
      followingId = null;
      $("stopStory").hidden = true; $("pauseStory").hidden = true;
      show("progressView");
      showError(`${st.error} Press Make it again to pick up where it stopped — nothing already made is paid for twice.`);
    }
  }, 2000);
}
$("stopStory").addEventListener("click", () => {
  if (followingId) stopJob(followingId, "making this story? What's already written is kept, and you can carry it on later", $("stopStory"));
});
// Pause: another story can be made now; this one waits in Unfinished stories with Resume
$("pauseStory").addEventListener("click", async () => {
  if (!followingId) return;
  $("pauseStory").disabled = true; $("pauseStory").textContent = "Pausing…";
  const r = await api(`/api/jobs/${followingId}/pause`, {});
  if (!r.ok) { $("pauseStory").disabled = false; $("pauseStory").textContent = "Pause"; alert(r.error || "Couldn't pause it."); }
});

function mediaBlock(s) {
  const wrap = document.createElement("div");
  wrap.className = "player" + (s.bedtime ? " bedtime" : "");
  let m = null;
  // a video story can be played as sound only, so it keeps going with the phone's screen off
  const canListen = !!((s.video_url || s.video) && (s.audio_url || s.audio));
  const listenOnly = canListen && (s._listen !== undefined ? s._listen : !!readerPrefs().listenOnly);
  if ((s.video_url || s.video) && !listenOnly) {
    const v = document.createElement("video");
    v.controls = true; v.preload = "metadata"; v.src = s.video_url || s.video; v.playsInline = true;
    v.poster = s.thumb || v.src.replace(/story\.mp4$/, "thumb.jpg");
    const subs = s.subtitles || (s.subtitles_url && s.subtitles_url.replace(/\.srt$/, ".vtt"));
    if (subs) {
      const t = document.createElement("track");
      Object.assign(t, { kind: "captions", srclang: "en", label: "English", src: subs });
      v.appendChild(t);
    }
    wrap.appendChild(v); m = v;
  } else if (s.audio_url || s.audio) {
    const a = document.createElement("audio");
    a.controls = true; a.preload = "metadata"; a.src = s.audio_url || s.audio;
    wrap.appendChild(a); m = a;
  }
  const sid = s.id || s.run_id;
  if (m && sid && s.id) {
    const swap = canListen ? () => {
      const at = m.currentTime, playing = !m.paused;
      m.pause();
      saveReaderPrefs({ ...readerPrefs(), listenOnly: !listenOnly });
      const nw = mediaBlock({ ...s, _listen: !listenOnly, _startAt: at });
      wrap.replaceWith(nw);
      const nm = nw.querySelector("video, audio");
      if (nm && playing) nm.play().catch(() => {});
    } : null;
    wrap.appendChild(listenTools(s, m, swap, listenOnly));
  }
  const links = document.createElement("div");
  links.className = "downloads";
  const add = (href, label) => { if (!href) return; const a = document.createElement("a"); a.href = href; a.textContent = label; a.download = ""; links.appendChild(a); };
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
  if (sid && s.id) wrap.appendChild(exportLinks(s));
  return wrap;
}

// ── e-book, print-ready book, YouTube ──
function exportLinks(s) {
  const box = document.createElement("div"); box.className = "downloads exports";
  const id = encodeURIComponent(s.id);
  const add = (href, label, title) => { const a = document.createElement("a"); a.href = href; a.textContent = label; a.title = title; a.download = ""; box.appendChild(a); return a; };
  add(`/api/stories/${id}/epub`, "E-book (EPUB)", "For Apple Books, Kindle and other e-readers, with the pictures");
  if (s.pictures && !s.adventure) {
    add(`/api/stories/${id}/print`, "Print-ready book (PDF)", "8.5 × 8.5 inch pages with bleed at 300 dpi, for Lulu, Amazon KDP or a print shop");
    add(`/api/stories/${id}/print?cover=1`, "Print cover (PDF)", "The front cover on its own, which print services ask for separately");
  }
  if (s.video) {
    const b = document.createElement("button"); b.type = "button"; b.className = "yt-btn"; b.textContent = "For YouTube";
    b.setAttribute("aria-expanded", "false");
    const panel = document.createElement("div"); panel.className = "yt-panel"; panel.hidden = true;
    b.addEventListener("click", async () => {
      if (!panel.hidden) { panel.hidden = true; b.setAttribute("aria-expanded", "false"); return; }
      const r = await api(`/api/stories/${id}/youtube`, null, "GET");
      panel.replaceChildren();
      const mk = (label, value, rows) => {
        const l = document.createElement("label"); l.textContent = label;
        const t = document.createElement(rows ? "textarea" : "input"); t.readOnly = true; t.value = value; if (rows) t.rows = rows;
        const c = document.createElement("button"); c.type = "button"; c.className = "small-btn"; c.textContent = "Copy";
        c.addEventListener("click", async () => { try { await navigator.clipboard.writeText(value); c.textContent = "Copied"; } catch { t.select(); } });
        panel.append(l, t, c);
      };
      mk("Title", r.title || s.title);
      mk("Description, with the chapters (paste it as it is: YouTube turns the times into chapters)", r.text || "", 9);
      const n = document.createElement("p"); n.className = "hint";
      n.textContent = (r.note ? r.note + " " : "") + "Upload the Video download above. Picture stories with music you didn't make yourself may get a copyright claim.";
      panel.appendChild(n);
      panel.hidden = false; b.setAttribute("aria-expanded", "true");
    });
    box.append(b);
    const wrap = document.createElement("div"); wrap.append(box, panel); return wrap;
  }
  return box;
}

// ── choose your own adventure ──
function adventurePlayer(s) {
  const box = document.createElement("section"); box.className = "adventure" + (s.bedtime ? " bedtime" : "");
  const sid = s.id || s.run_id;
  const advUrl = typeof s.adventure === "string" ? s.adventure : `/media/${encodeURIComponent(sid)}/adventure.json`;
  const key = `storymaker-adv-${sid}`;
  let adv = null, path = [], found = new Set();
  try { const saved = JSON.parse(localStorage.getItem(key)) || {}; path = saved.path || []; found = new Set(saved.found || []); } catch { /* private */ }
  const save = () => { try { localStorage.setItem(key, JSON.stringify({ path, found: [...found] })); } catch { /* private */ } };
  const audio = document.createElement("audio"); audio.className = "adv-audio"; audio.controls = true; audio.preload = "auto";
  const text = document.createElement("div"); text.className = "adv-text"; text.setAttribute("aria-live", "polite");
  const choices = document.createElement("div"); choices.className = "adv-choices";
  const foot = document.createElement("div"); foot.className = "adv-foot";
  const draw = (autoplay) => {
    const k = path[path.length - 1], p = adv.passages[k];
    if (!p) { path = [adv.start]; return draw(false); }
    text.replaceChildren(...p.text.split(/\n+/).filter(Boolean).map((t) => { const x = document.createElement("p"); x.textContent = t; return x; }));
    choices.replaceChildren();
    if (p.audio) {
      audio.hidden = false; audio.src = advUrl.replace(/adventure\.json$/, p.audio);
      if (autoplay) audio.play().catch(() => {});
    } else audio.hidden = true;
    if (p.choices && p.choices.length) {
      const q = document.createElement("p"); q.className = "adv-q"; q.textContent = "What will you do?"; choices.appendChild(q);
      p.choices.forEach((c) => {
        const b = document.createElement("button"); b.type = "button"; b.className = "adv-choice"; b.textContent = c.label;
        b.addEventListener("click", () => { path.push(c.to); save(); draw(true); box.scrollIntoView({ block: "start", behavior: "smooth" }); });
        choices.appendChild(b);
      });
    } else {
      found.add(k); save();
      const end = document.createElement("p"); end.className = "adv-end";
      end.textContent = `The End${p.ending && p.ending !== "the end" ? `: a ${p.ending} ending` : ""}.`;
      const again = document.createElement("button"); again.type = "button"; again.className = "adv-choice"; again.textContent = "Start again and choose differently";
      again.addEventListener("click", () => { path = [adv.start]; save(); draw(true); });
      choices.append(end, again);
    }
    const ends = Object.values(adv.passages).filter((x) => !x.choices || !x.choices.length).length;
    foot.replaceChildren();
    const info = document.createElement("span"); info.className = "hint";
    info.textContent = `Endings found: ${found.size} of ${ends}. Choices made: ${path.length - 1}.`;
    foot.appendChild(info);
    if (path.length > 1) {
      const back = document.createElement("button"); back.type = "button"; back.className = "small-btn"; back.textContent = "Go back one choice";
      back.addEventListener("click", () => { path.pop(); save(); draw(false); });
      foot.appendChild(back);
    }
    if (path.length > 1) {
      const restart = document.createElement("button"); restart.type = "button"; restart.className = "small-btn"; restart.textContent = "Start over";
      restart.addEventListener("click", () => { path = [adv.start]; save(); draw(false); });
      foot.appendChild(restart);
    }
  };
  box.append(audio, text, choices, foot);
  text.textContent = "Opening the adventure…";
  fetch(advUrl).then((r) => r.json()).then((a) => {
    adv = a;
    if (!path.length || !adv.passages[path[path.length - 1]]) path = [adv.start];
    draw(false);
  }).catch(() => { text.textContent = "Couldn't open this adventure."; });
  return box;
}

// ── listening: read along, chapters, speed, sleep timer, picking up where you stopped, saving for offline ──
const clock = (t) => {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
};
function parseVtt(text) {
  const sec = (x) => { const p = x.trim().split(":").map(parseFloat); return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]; };
  const cues = [];
  for (const block of text.replace(/\r/g, "").split(/\n\n+/)) {
    const lines = block.split("\n");
    const i = lines.findIndex((l) => l.includes("-->"));
    if (i < 0) continue;
    const [a, b] = lines[i].split("-->");
    const words = lines.slice(i + 1).join(" ").replace(/<[^>]+>/g, "").trim();
    if (words) cues.push({ start: sec(a), end: sec(b.trim().split(/\s/)[0]), text: words });
  }
  return cues;
}
let sleepTimer = null;           // one sleep timer for the whole page
function listenTools(s, m, swap, listenOnly) {
  const id = s.id;
  const box = document.createElement("div"); box.className = "listen";
  const bar = document.createElement("div"); bar.className = "listen-bar";
  const field = (label, el) => { const l = document.createElement("label"); l.className = "listen-field"; const t = document.createElement("span"); t.textContent = label; l.append(t, el); return l; };
  const select = (opts, val) => { const x = document.createElement("select"); for (const [v, t] of opts) { const o = document.createElement("option"); o.value = v; o.textContent = t; x.appendChild(o); } x.value = val; return x; };

  // chapters
  const chSel = select([["", "Start"]], "");
  const chField = field("Chapter", chSel); chField.hidden = true;
  let chapters = [];
  chSel.addEventListener("change", () => { const c = chapters[+chSel.value]; if (c) { m.currentTime = c.start + 0.05; m.play().catch(() => {}); } });

  // speed
  const prefs = readerPrefs();
  const speed = select([["0.8", "0.8×"], ["0.9", "0.9×"], ["1", "1×"], ["1.1", "1.1×"], ["1.25", "1.25×"], ["1.5", "1.5×"]], String(prefs.rate || 1));
  const setRate = () => { m.playbackRate = parseFloat(speed.value) || 1; m.defaultPlaybackRate = m.playbackRate; };
  speed.addEventListener("change", () => { setRate(); saveReaderPrefs({ ...readerPrefs(), rate: parseFloat(speed.value) }); });
  m.addEventListener("loadedmetadata", setRate);

  // sleep timer
  const sleep = select([["", "Off"], ["10", "10 min"], ["20", "20 min"], ["30", "30 min"], ["45", "45 min"], ["60", "1 hour"], ["chapter", "End of chapter"]], s.bedtime ? "20" : "");
  const sleepLeft = document.createElement("span"); sleepLeft.className = "sleep-left";
  const startSleep = () => {
    if (sleepTimer) { clearInterval(sleepTimer.tick); sleepTimer = null; }
    sleepLeft.textContent = "";
    if (!sleep.value) return;
    const until = sleep.value === "chapter" ? null : Date.now() + parseInt(sleep.value, 10) * 60000;
    const chapterEnd = () => { const t = m.currentTime; const n = chapters.find((c) => c.start > t + 1); return n ? n.start : (m.duration || Infinity); };
    const stopAt = sleep.value === "chapter" ? chapterEnd() : null;
    const vol = m.volume;
    const t = { tick: setInterval(() => {
      if (m.paused && !until) return;
      const left = until ? (until - Date.now()) / 1000 : (stopAt - m.currentTime) / (m.playbackRate || 1);
      sleepLeft.textContent = left > 0 ? `${clock(left)} left` : "";
      if (left <= 8 && left > 0) m.volume = Math.max(0, vol * (left / 8));      // a gentle fade at the end
      if (left <= 0) {
        m.pause(); m.volume = vol; clearInterval(t.tick); sleepTimer = null;
        sleep.value = ""; sleepLeft.textContent = "Stopped by the sleep timer";
      }
    }, 1000) };
    sleepTimer = t;
  };
  sleep.addEventListener("change", startSleep);
  m.addEventListener("play", () => { if (sleep.value && !sleepTimer) startSleep(); }, { once: false });

  // read along
  const raBtn = document.createElement("button"); raBtn.type = "button"; raBtn.className = "quiet"; raBtn.textContent = "Read along";
  raBtn.hidden = !s.subtitles;
  const ra = document.createElement("div"); ra.className = "readalong"; ra.hidden = true;
  ra.setAttribute("aria-label", "The words, highlighted as they're read. Tap a line to go there.");
  let cueEls = [], cues = [], current = -1;
  const showReadAlong = async () => {
    if (!cues.length) {
      try { cues = parseVtt(await fetch(s.subtitles).then((x) => x.text())); } catch { cues = []; }
      ra.innerHTML = "";
      let para = null, ch = 0, prevEnd = -1;
      cues.forEach((c, i) => {
        while (ch < chapters.length && chapters[ch].start <= c.start + 0.3) {
          if (ch > 0 || chapters.length === 1 || chapters[ch].start > 1) {
            const h = document.createElement("h4"); h.textContent = chapters[ch].title; ra.appendChild(h); para = null;
          }
          ch++;
        }
        if (!para || c.start - prevEnd > 0.7) { para = document.createElement("p"); ra.appendChild(para); }
        const w = document.createElement("span"); w.className = "cue"; w.textContent = c.text + " "; w.dataset.i = i;
        para.appendChild(w); cueEls.push(w); prevEnd = c.end;
      });
      if (!cues.length) ra.textContent = "The words for this one aren't available.";
    }
    ra.hidden = false; raBtn.textContent = "Hide words"; raBtn.setAttribute("aria-expanded", "true");
    follow(true);
  };
  raBtn.addEventListener("click", () => {
    if (ra.hidden) showReadAlong();
    else { ra.hidden = true; raBtn.textContent = "Read along"; raBtn.setAttribute("aria-expanded", "false"); }
    saveReaderPrefs({ ...readerPrefs(), readAlong: !ra.hidden });
  });
  ra.addEventListener("click", (e) => {
    const w = e.target.closest(".cue"); if (!w) return;
    m.currentTime = cues[+w.dataset.i].start + 0.02; if (m.paused) m.play().catch(() => {});
  });
  let userScrolled = 0;
  ra.addEventListener("wheel", () => { userScrolled = Date.now(); }, { passive: true });
  ra.addEventListener("touchmove", () => { userScrolled = Date.now(); }, { passive: true });
  const follow = (force) => {
    if (ra.hidden || !cues.length) return;
    const t = m.currentTime;
    let i = current;
    if (i < 0 || !(cues[i].start <= t && (cues[i + 1] ? cues[i + 1].start > t : true))) {
      i = cues.findIndex((c, k) => c.start <= t && (!cues[k + 1] || cues[k + 1].start > t));
    }
    if (i === current && !force) return;
    if (cueEls[current]) cueEls[current].classList.remove("now");
    current = i;
    const el = cueEls[i]; if (!el) return;
    el.classList.add("now");
    if (force || Date.now() - userScrolled > 4000) {           // keep the line in view (inside the box, not the page)
      const top = el.offsetTop - ra.offsetTop - ra.clientHeight / 3;
      ra.scrollTo({ top: Math.max(0, top), behavior: force ? "auto" : "smooth" });
    }
  };

  // where we are: chapter name and picking up later
  const where = document.createElement("p"); where.className = "listen-where";
  const chapterNow = () => {
    if (!chapters.length) return;
    let k = 0; for (let j = 0; j < chapters.length; j++) if (chapters[j].start <= m.currentTime + 0.3) k = j;
    if (chSel.value !== String(k)) chSel.value = String(k);
  };
  let lastSave = 0;
  const save = (keep) => {
    if (!m.duration || !isFinite(m.duration)) return;
    lastSave = Date.now();
    const pos = m.ended ? m.duration : m.currentTime;
    fetch(`/api/stories/${encodeURIComponent(id)}/position`, { method: "PUT", keepalive: !!keep,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pos, dur: m.duration }) }).catch(() => {});
  };
  m.addEventListener("timeupdate", () => {
    follow(false); chapterNow();
    if (!m.paused && Date.now() - lastSave > 15000) save();
  });
  m.addEventListener("seeked", () => { follow(true); chapterNow(); });
  m.addEventListener("pause", () => save());
  m.addEventListener("ended", () => save());
  window.addEventListener("pagehide", () => { if (!m.paused) save(true); });

  const pickUp = async () => {
    if (s._startAt !== undefined) { m.currentTime = s._startAt; return; }     // switched between video and sound
    const p = await fetch(`/api/stories/${encodeURIComponent(id)}/position`).then((x) => x.json()).catch(() => ({}));
    const dur = m.duration || p.dur || 0;
    if (p.pos > 15 && (!dur || p.pos < dur - 20) && m.currentTime < 1) {
      m.currentTime = p.pos;
      where.innerHTML = "";
      const t = document.createElement("span"); t.textContent = `Picking up where you stopped, at ${clock(p.pos)}. `;
      const again = document.createElement("button"); again.type = "button"; again.className = "linkish"; again.textContent = "Start from the beginning";
      again.addEventListener("click", () => { m.currentTime = 0; where.textContent = ""; });
      where.append(t, again);
    }
  };
  if (m.readyState >= 1) pickUp(); else m.addEventListener("loadedmetadata", pickUp, { once: true });

  fetch(`/api/stories/${encodeURIComponent(id)}/chapters`).then((x) => x.json()).then((r) => {
    chapters = (r.chapters || []).filter((c) => c.title);
    if (chapters.length > 1) {
      chSel.innerHTML = "";
      chapters.forEach((c, k) => { const o = document.createElement("option"); o.value = String(k); o.textContent = c.title; chSel.appendChild(o); });
      chField.hidden = false; chapterNow();
    }
    if (readerPrefs().readAlong && s.subtitles) showReadAlong();
  }).catch(() => {});

  // the lock screen and Control Center: title, cover, play/pause, skip and chapter buttons
  m.addEventListener("play", () => {
    if (!("mediaSession" in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: s.title || "Story",
        artist: s.series ? `${s.series}${s.episode ? `, episode ${s.episode}` : ""}` : "StoryMaker", album: "StoryMaker",
        artwork: s.thumb ? [{ src: new URL(s.thumb, location.href).href, sizes: "640x427", type: "image/jpeg" }] : [] });
      const ms = navigator.mediaSession, set = (a, f) => { try { ms.setActionHandler(a, f); } catch { /* not on this device */ } };
      set("play", () => m.play()); set("pause", () => m.pause());
      set("seekbackward", (d) => { m.currentTime = Math.max(0, m.currentTime - ((d && d.seekOffset) || 15)); });
      set("seekforward", (d) => { m.currentTime = Math.min(m.duration || 1e9, m.currentTime + ((d && d.seekOffset) || 30)); });
      set("seekto", (d) => { if (d && d.seekTime !== undefined) m.currentTime = d.seekTime; });
      set("previoustrack", chapters.length > 1 ? () => {
        const t = m.currentTime; const back = [...chapters].reverse().find((c) => c.start < t - 3); m.currentTime = back ? back.start : 0;
      } : null);
      set("nexttrack", chapters.length > 1 ? () => { const n = chapters.find((c) => c.start > m.currentTime + 1); if (n) m.currentTime = n.start; } : null);
    } catch { /* older browser */ }
  });

  bar.append(chField, field("Speed", speed), field("Sleep", sleep), sleepLeft, raBtn);
  if (swap) {
    const sw = document.createElement("button"); sw.type = "button"; sw.className = "quiet";
    sw.textContent = listenOnly ? "Watch the video" : "Listen only";
    sw.title = listenOnly ? "Show the pictures again" : "Just the sound, so it keeps playing with the screen off";
    sw.addEventListener("click", swap); bar.appendChild(sw);
  }
  const off = offlineButton(s);
  if (off) bar.appendChild(off);
  box.append(bar, where, ra);
  return box;
}

// ── saving a story on this phone or tablet to play with no connection ──
const OFFLINE_CACHE = "storymaker-offline-v1";
const offlineOk = () => "caches" in window && "serviceWorker" in navigator && window.isSecureContext;
const playable = (s) => s.video_url || s.video || s.audio_url || s.audio;
function offlineFiles(s) {
  const files = [playable(s), s.subtitles, s.thumb, `/api/stories/${encodeURIComponent(s.id)}/chapters`].filter(Boolean);
  return files;
}
async function isSavedOffline(s) {
  if (!offlineOk()) return false;
  try { const c = await caches.open(OFFLINE_CACHE); return !!(await c.match(playable(s))); } catch { return false; }
}
function offlineButton(s) {
  if (!playable(s)) return null;
  const b = document.createElement("button"); b.type = "button"; b.className = "quiet offline-btn";
  if (!offlineOk()) {
    b.textContent = "Save for offline";
    b.addEventListener("click", () => {
      b.replaceWith(Object.assign(document.createElement("span"), { className: "sleep-left",
        textContent: "Saving for offline needs the secure (https) address, like your Tailscale .ts.net one." }));
    });
    return b;
  }
  const label = async () => { b.textContent = (await isSavedOffline(s)) ? "Saved on this device ✓ (remove)" : "Save for offline"; };
  label();
  b.addEventListener("click", async () => {
    const c = await caches.open(OFFLINE_CACHE);
    if (await isSavedOffline(s)) {
      for (const f of offlineFiles(s)) await c.delete(f);
      return label();
    }
    b.disabled = true; b.textContent = "Saving…";
    try {
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      for (const f of offlineFiles(s)) {
        const r = await fetch(f, { credentials: "same-origin" });
        if (!r.ok) throw new Error(`${r.status}`);
        await c.put(f, r);
      }
      await label();
    } catch (e) {
      for (const f of offlineFiles(s)) await c.delete(f);
      b.textContent = "Couldn't save it (the device may be out of space). Try again";
    } finally { b.disabled = false; }
  });
  return b;
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
  if (s.adventure) {                      // choose your own adventure: played, not read straight through
    el.appendChild(adventurePlayer(s));
    if (s.id || s.run_id) el.appendChild(exportLinks({ ...s, id: s.id || s.run_id }));
  } else {
    el.appendChild(mediaBlock(s));
  }
  if (s.note) { const n = document.createElement("p"); n.className = "note"; n.textContent = s.note; el.appendChild(n); }
  const sid = s.id || s.run_id;
  if (sid && !s.adventure) el.appendChild(storyTools(s, sid, el));
  if (!s.adventure) el.appendChild(renderReader(s.story || ""));
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
// Stop anything that's being made. Asks first; what's done so far is kept where it can be.
async function stopJob(jobId, what, button) {
  if (!confirm(`Stop ${what}?`)) return false;
  if (button) { button.disabled = true; button.textContent = "Stopping…"; }
  const r = await api(`/api/jobs/${jobId}/stop`, {});
  if (!r.ok && button) { button.disabled = false; button.textContent = "Stop"; alert(r.error || "Couldn't stop it."); }
  return r.ok;
}
function followJob(jobId, meter, done, fail, what = "this") {
  const stop = document.createElement("button");
  stop.type = "button"; stop.className = "small-btn warn stop-btn"; stop.textContent = "Stop";
  stop.addEventListener("click", () => stopJob(jobId, what, stop));
  meter.appendChild(stop);
  const t = setInterval(async () => {
    const st = await api(`/api/status/${jobId}`);
    if (st.state === "running" || st.state === "queued") meter.set(st.progress || 0, st.state === "queued" ? "Getting started…" : st.detail);
    else if (st.state === "done") { clearInterval(t); stop.remove(); meter.set(100, "Done"); done(st); }
    else if (st.state === "stopped") { clearInterval(t); stop.remove(); meter.set(st.progress || 0, "Stopped"); fail(st.detail || "Stopped."); }
    else if (st.state === "failed" || st.ok === false) { clearInterval(t); stop.remove(); fail(st.error || "It stopped."); }
  }, 2000);
}
// The choices, once more, before anything starts: Start, or Change to go back and pick again.
function confirmStart(panel, go, title, lines, startLabel, onStart) {
  const old = panel.querySelector(".confirm-start"); if (old) old.remove();
  const box = document.createElement("div"); box.className = "confirm-start";
  const h = document.createElement("p"); h.className = "cs-title"; h.textContent = title;
  const ul = document.createElement("ul");
  for (const l of lines) { const li = document.createElement("li"); li.textContent = l; ul.appendChild(li); }
  const row = document.createElement("div"); row.className = "cs-row";
  const yes = document.createElement("button"); yes.type = "button"; yes.className = "go go-small"; yes.textContent = startLabel;
  const back = document.createElement("button"); back.type = "button"; back.className = "small-btn"; back.textContent = "Change";
  row.append(yes, back); box.append(h, ul, row);
  go.hidden = true; go.after(box);
  back.addEventListener("click", () => { box.remove(); go.hidden = false; go.disabled = false; go.focus(); });
  yes.addEventListener("click", () => { box.remove(); go.hidden = false; onStart(); });
  yes.focus();
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
  bar.append(btn("Edit the text", editPanel), btn("Clean up the writing", cleanupPanel), btn("Rewrite with AI", rewritePanel));
  bar.append(btn("Cast", castPanel), btn("Feedback", feedbackPanel));
  if (hasAudio) bar.append(btn("Voices", voicesPanel), btn("Sounds", soundsPanel), btn("Remake the audio", () => remakePanel(false)));
  bar.append(btn("Send to Audio Studio", () => {
    const base = features.studioUrl || `${location.protocol}//${location.hostname}:${location.protocol === "https:" ? 8443 : 8096}`;
    window.open(`${base.replace(/\/$/, "")}/?import=${encodeURIComponent(id)}`, "_blank", "noopener");
  }));
  const ser = (seriesList || []).find((x) => x.name === s.series);
  if (ser) bar.append(btn("Next episode", () => nextEpisode(ser.name)), btn("Series bible", biblePanel));
  if (s.request) bar.append(btn("Make a new version", () => newVersion(s.request)));
  if (!$("tabArchive").hidden) bar.append(btn("Move to archive", archivePanel));   // only when there's a NAS
  wrap.append(bar, area);

  // the series bible: who's in it and how they look and sound, the look of every episode, and what the writer
  // remembers (each episode's recap, where things stand, open threads)
  async function biblePanel() {
    area.innerHTML = '<p class="hint">Opening the series bible…</p>';
    const b = await api(`/api/series/${ser.slug}`);
    area.innerHTML = "";
    if (!b.ok) { area.textContent = b.error || "Couldn't open it."; return; }
    const p = document.createElement("div"); p.className = "panel-tool bible";
    const h = document.createElement("h4"); h.textContent = `${b.name}: series bible`;
    const note = document.createElement("p"); note.className = "level-note";
    note.textContent = "Every new episode uses exactly these characters, looks and voices. Change anything here and the next episode follows it.";
    p.append(h, note);
    const mkField = (label, value, { area: big = false, ph = "", max = 300 } = {}) => {
      const l = document.createElement("label"); l.className = "bf";
      const t = document.createElement("span"); t.textContent = label;
      const i = document.createElement(big ? "textarea" : "input"); if (!big) i.type = "text";
      i.value = value || ""; i.placeholder = ph; i.maxLength = max; if (big) i.rows = 2;
      l.append(t, i); return [l, i];
    };
    // the look
    const lookRow = document.createElement("div"); lookRow.className = "bible-look";
    const styleSel = document.createElement("select");
    for (const [v, t] of [["", "Each story's own choice"], ["storybook", "Storybook"], ["realistic", "Realistic"], ["cinematic", "Cinematic"], ["3d", "3D animated"], ["epic", "Epic action"]]) styleSel.append(new Option(t, v));
    styleSel.value = b.bible.picture_style || "";
    const sl = document.createElement("label"); sl.className = "bf"; sl.append(Object.assign(document.createElement("span"), { textContent: "Picture style" }), styleSel);
    const [vl, vi] = mkField("Look of every picture", b.bible.visual_style, { area: true, max: 400, ph: "e.g. lush jungle greens, golden hour light, painterly" });
    const nl = document.createElement("div"); nl.className = "bf";
    nl.append(Object.assign(document.createElement("span"), { textContent: "Narrator" }));
    const narr = document.createElement("div"); nl.appendChild(narr); voicePicker(narr, { current: b.bible.narrator });
    lookRow.append(sl, nl, vl); p.appendChild(lookRow);
    // the characters
    const ch = document.createElement("h5"); ch.textContent = "Characters"; p.appendChild(ch);
    const list = document.createElement("div"); list.className = "bible-chars"; p.appendChild(list);
    const KINDS = [["", "Kind…"], ["female", "Woman"], ["male", "Man"], ["girl", "Girl"], ["boy", "Boy"], ["old_woman", "Older woman"], ["old_man", "Older man"], ["creature", "Creature"]];
    const rows = [];
    const addRow = (c = {}) => {
      const r = document.createElement("div"); r.className = "bible-char";
      const pic = document.createElement("div"); pic.className = "bc-pic";
      if (c.portraitUrl) { const im = document.createElement("img"); im.src = c.portraitUrl; im.alt = `Portrait of ${c.name}`; pic.appendChild(im); }
      else pic.textContent = (c.name || "?").slice(0, 1);
      const f = document.createElement("div"); f.className = "bc-fields";
      const [nL, nI] = mkField("Name", c.name, { max: 60 });
      const kL = document.createElement("label"); kL.className = "bf"; const kS = document.createElement("select");
      for (const [v, t] of KINDS) kS.append(new Option(t, v)); kS.value = c.type || "";
      kL.append(Object.assign(document.createElement("span"), { textContent: "Kind" }), kS);
      const [aL, aI] = mkField("Age", c.age, { max: 20, ph: "12, 40s…" });
      const [lL, lI] = mkField("Looks", c.look, { area: true, ph: "hair, eyes, build, skin, anything distinctive" });
      const [oL, oI] = mkField("Usually wears", c.outfit, { max: 200 });
      const vb = document.createElement("div"); vb.className = "bf"; vb.append(Object.assign(document.createElement("span"), { textContent: "Voice" }));
      const vp = document.createElement("div"); vb.appendChild(vp);
      voicePicker(vp, { gender: ["female", "girl", "old_woman"].includes(c.type) ? "female" : ["male", "boy", "old_man"].includes(c.type) ? "male" : "", current: c.voice });
      const rm = document.createElement("button"); rm.type = "button"; rm.className = "small-btn bc-rm"; rm.textContent = "Remove";
      rm.addEventListener("click", () => { r.remove(); rows.splice(rows.indexOf(row), 1); });
      f.append(nL, kL, aL, oL, lL, vb, rm);
      r.append(pic, f); list.appendChild(r);
      const row = { get: () => ({ name: nI.value.trim(), type: kS.value, age: aI.value.trim(), look: lI.value.trim(), outfit: oI.value.trim(), voice: vp.spec ? vp.spec() : "" }) };
      rows.push(row);
    };
    (b.bible.characters || []).forEach(addRow);
    const add = document.createElement("button"); add.type = "button"; add.className = "small-btn"; add.textContent = "Add a character";
    add.addEventListener("click", () => addRow({})); p.appendChild(add);
    // what the writer remembers
    const mh = document.createElement("h5"); mh.textContent = "What the writer remembers"; p.appendChild(mh);
    const eps = document.createElement("ol"); eps.className = "bible-eps";
    for (const e of b.episodes) { const li = document.createElement("li"); const t = document.createElement("strong"); t.textContent = e.title; li.append(t, document.createTextNode(` ${e.recap || e.logline || ""}`)); eps.appendChild(li); }
    p.appendChild(eps);
    const [stL, stI] = mkField("Where things stand now (one per line)", (b.state || []).join("\n"), { area: true, max: 3000 });
    const [thL, thI] = mkField("Open threads the next episode can pick up (one per line)", (b.threads || []).join("\n"), { area: true, max: 3000 });
    stI.rows = 4; thI.rows = 3; p.append(stL, thL);
    // save, portraits
    const acts = document.createElement("div"); acts.className = "cs-row";
    const save = document.createElement("button"); save.type = "button"; save.className = "go go-small"; save.textContent = "Save the bible";
    const draw = document.createElement("button"); draw.type = "button"; draw.className = "small-btn"; draw.textContent = "Draw the portraits";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    acts.append(save, ...(features.localPictures ? [draw] : [])); p.append(acts, msg); area.appendChild(p);
    const payload = () => ({ bible: { characters: rows.map((r) => r.get()).filter((c) => c.name), picture_style: styleSel.value,
      visual_style: vi.value.trim(), narrator: narr.spec ? narr.spec() : "" },
      state: stI.value.split("\n").map((x) => x.trim()).filter(Boolean), threads: thI.value.split("\n").map((x) => x.trim()).filter(Boolean) });
    const doSave = async () => {
      const r = await api(`/api/series/${ser.slug}`, payload(), "PUT");
      msg.className = r.ok ? "settings-msg" : "settings-msg bad";
      msg.textContent = r.ok ? "Saved. The next episode follows it." : (r.error || "Couldn't save.");
      return r.ok;
    };
    save.addEventListener("click", doSave);
    draw.addEventListener("click", async () => {
      if (!(await doSave())) return;
      draw.disabled = true;
      const r = await api(`/api/series/${ser.slug}/portraits`, {});
      if (!r.ok) { draw.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.insertBefore(m, msg);
      followJob(r.job_id, m, () => { msg.className = "settings-msg"; msg.textContent = "Portraits drawn."; setTimeout(biblePanel, 800); },
        (err) => { draw.disabled = false; msg.className = "settings-msg bad"; msg.textContent = err; }, "drawing the portraits");
    });
  }

  // this one story to the NAS archive (copied and checked before it's removed here)
  function archivePanel() {
    area.innerHTML = "";
    const p = document.createElement("div"); p.className = "panel-tool";
    const note = document.createElement("p"); note.className = "level-note";
    note.textContent = "Moves this story to the NAS to free space here. It's copied and every file checked before it's removed from this computer. It then shows in the Archive tab, where Bring back returns it.";
    const keepL = document.createElement("label"); keepL.className = "check";
    const keep = document.createElement("input"); keep.type = "checkbox";
    keepL.append(keep, document.createTextNode(" Keep it here too (just back it up)"));
    const go = document.createElement("button"); go.type = "button"; go.className = "go"; go.textContent = "Move to archive";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    keep.addEventListener("change", () => { go.textContent = keep.checked ? "Back up to the archive" : "Move to archive"; });
    p.append(note, keepL, go, msg); area.appendChild(p);
    go.addEventListener("click", async () => {
      go.disabled = true;
      const r = await api("/api/nas/archive", { ids: [id], keep: keep.checked });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.insertBefore(m, msg);
      followNas(r.task, m, (st) => {
        if ((st.failed || []).length) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = nasSummary(st, "moved"); return; }
        msg.className = "settings-msg";
        msg.textContent = keep.checked ? "Backed up to the archive." : "Moved to the archive. Find it in the Archive tab.";
        setTimeout(() => { if (!keep.checked) { const v = document.getElementById("viewer"); if (v && v.close) v.close(); } loadLibrary(); }, 1500);
      });
    });
  }

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

  async function cleanupPanel() {
    area.innerHTML = "";
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Clean up the writing</h4><p class="level-note">Checking the sentences…</p>';
    area.appendChild(p);
    const c = await api(`/api/stories/${encodeURIComponent(id)}/writing-check`);
    if (!c.ok) { p.querySelector(".level-note").textContent = c.error || "Couldn't check it."; return; }
    p.innerHTML = '<h4>Clean up the writing</h4>';
    p.appendChild(writingReport(c));
    const note = document.createElement("p"); note.className = "level-note";
    note.textContent = "The proofreader and the sentence checker go over every scene: spelling, grammar, run-on and tangled sentences, quotes, and who is speaking. The events, names, style and heat stay as they are, and so do your sounds.";
    const rl = document.createElement("label"); rl.className = "check";
    const rc = document.createElement("input"); rc.type = "checkbox"; rc.checked = true;
    rl.append(rc, document.createTextNode(" Remake the audio afterwards (only the changed lines are recorded again)"));
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Clean it up";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(note, ...(c.has_audio ? [rl] : []), go, msg);
    go.addEventListener("click", () => confirmStart(p, go, "Ready to clean up the writing:", [
      `${c.problems || 0} sentence problem${c.problems === 1 ? "" : "s"} found; every scene is checked`,
      "Events, names, style, heat and sounds stay as they are",
      c.has_audio ? (rc.checked ? "Then the audio is remade (only changed lines are recorded again)" : "The audio is left as it is") : "No audio to remake",
    ], "Start cleaning up", async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/cleanup`, { remake: rc.checked });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, (st) => { msg.className = "settings-msg"; msg.textContent = cleanupSummary(st); setTimeout(() => refreshCard(id, el), 2500); },
        (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; }, "cleaning up this story");
    }));
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
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; }, "rewriting this story");
    });
  }

  // tell the writer what worked and what didn't: it goes into your style for stories like this one
  async function feedbackPanel() {
    area.innerHTML = "";
    const full = await api(`/api/stories/${encodeURIComponent(id)}/full`);
    if (!full.ok) { area.textContent = full.error; return; }
    const p = document.createElement("div"); p.className = "panel-tool";
    p.innerHTML = '<h4>Feedback</h4><p class="level-note">What you liked or didn\'t about this story. The writer uses it for every story like this one from now on.</p>';
    let rating = "";
    const chips = document.createElement("div"); chips.className = "row";
    const mkChip = (val, label) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "small-btn"; b.textContent = label;
      b.addEventListener("click", () => { rating = rating === val ? "" : val; chips.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b && rating === val)); });
      return b;
    };
    chips.append(mkChip("up", "More like this"), mkChip("down", "Less like this"));
    const nl = document.createElement("label"); nl.textContent = "What to keep or change"; nl.htmlFor = `fb-${id}`;
    const notes = document.createElement("textarea"); notes.id = `fb-${id}`; notes.rows = 3;
    notes.placeholder = "Loved the slow build and the banter. Less description of rooms, more of what they feel. Dialogue sounded too formal.";
    const keep = document.createElement("label"); keep.className = "check";
    const kc = document.createElement("input"); kc.type = "checkbox";
    keep.append(kc, document.createTextNode(" Also keep a passage from this story as writing to match"));
    const passage = document.createElement("textarea"); passage.rows = 6; passage.hidden = true;
    passage.value = (full.scenes[0] && full.scenes[0].narration || "").slice(0, 1500);
    kc.addEventListener("change", () => { passage.hidden = !kc.checked; });
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Save feedback";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    go.addEventListener("click", async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/feedback`, { rating, notes: notes.value, passage: kc.checked ? passage.value : "" });
      go.disabled = false;
      msg.className = r.ok ? "settings-msg" : "settings-msg bad";
      msg.textContent = r.ok ? `Saved to your ${{ kids: "Kids", family: "Family", sfw: "Safe for work", mature: "Mature", explicit: "Explicit" }[r.profile] || ""} style. The next stories like this one will use it.` : (r.error || "Not saved.");
      if (r.ok) { notes.value = ""; kc.checked = false; passage.hidden = true; }
    });
    p.append(chips, nl, notes, keep, passage, go, msg);
    area.appendChild(p);
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
    p.innerHTML = '<h4>Sounds</h4><p class="level-note">Background: leave it blank and it follows the story by itself, starting where the text puts you somewhere (the rain, a café, the sea) and lasting only as long as you stay there. Type a sound to play it under the whole scene, or <strong>none</strong> for silence. Effects play once, on the words you give, and only as long as that line; tick <strong>every time</strong> to play it each time those words are read.</p>';
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
      amb.value = sc.sound && sc.sound.chosen ? sc.sound.ambience : "";
      amb.placeholder = "Background: automatic (type a sound, or none)"; amb.setAttribute("aria-label", `Background for scene ${sc.index + 1}`);
      const fxBox = document.createElement("div"); fxBox.style.display = "grid"; fxBox.style.gap = "6px";
      const fxRows = [];
      const addFx = (f = { sound: "", at: "" }) => {
        if (fxRows.length >= 8) return;
        const r = document.createElement("div"); r.className = "fx-row";
        const so = document.createElement("input"); so.type = "text"; so.maxLength = 60; so.value = f.sound; so.placeholder = "Effect, e.g. door knock";
        const at = document.createElement("input"); at.type = "text"; at.maxLength = 120; at.value = f.at; at.placeholder = "Plays on these words (optional)";
        const rm = document.createElement("button"); rm.type = "button"; rm.className = "small-btn warn"; rm.textContent = "Remove";
        rm.addEventListener("click", () => { r.remove(); fxRows.splice(fxRows.indexOf(entry), 1); });
        const ev = document.createElement("label"); ev.className = "check every";
        const evc = document.createElement("input"); evc.type = "checkbox"; evc.checked = !!f.every;
        ev.append(evc, document.createTextNode(" every time"));
        ev.title = "Play it each time these words are read, not just once";
        const entry = [so, at, evc];
        r.append(so, at, ev, rm); fxBox.appendChild(r); fxRows.push(entry);
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
      for (const [i, amb, fx] of editors) scenes[i] = { ambience: amb.value.trim(), effects: fx.map(([so, at, ev]) => ({ sound: so.value, at: at.value, every: ev.checked })).filter((f) => f.sound.trim()) };
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
    const [dl, dcb] = mk("Draw new pictures for the video and picture book (with Draw Things: the characters acting out each scene)");
    // the look of the new pictures (starts at the story's own)
    const styleRow = document.createElement("label"); styleRow.className = "field style-pick"; styleRow.hidden = true;
    const styleSel = document.createElement("select");
    for (const [v, t] of [["storybook", "Storybook (illustrated)"], ["realistic", "Realistic (like photographs)"], ["cinematic", "Cinematic (like a film)"], ["3d", "3D animated"], ["epic", "Epic action"]]) {
      const o = document.createElement("option"); o.value = v; o.textContent = t; styleSel.appendChild(o);
    }
    styleSel.value = ((s.request || {}).picture_style) || "storybook";
    styleRow.append(document.createTextNode("Picture style "), styleSel);
    dcb.addEventListener("change", () => { styleRow.hidden = !dcb.checked; });
    const go = document.createElement("button"); go.className = "go"; go.type = "button"; go.textContent = "Remake the audio";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    p.append(vl, sl, ...(features.localPictures ? [dl, styleRow] : []), go, msg);
    area.appendChild(p);
    if (quick) p.querySelector(".level-note").textContent = "The text changed since the audio was made. Pick what to redo, then remake it: the changed lines are recorded and everything else is reused.";
    const start = async () => {
      go.disabled = true;
      const r = await api(`/api/stories/${encodeURIComponent(id)}/remaster`, { revoice: vc.checked, redesign: scb.checked, redraw: dcb.checked, pictureStyle: styleSel.value });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); p.appendChild(m);
      followJob(r.job_id, m, () => refreshCard(id, el), (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; }, "remaking the audio");
    };
    go.addEventListener("click", () => confirmStart(p, go, "Ready to remake the audio:", [
      vc.checked ? "New voices are chosen for the characters" : "The same voices as before (only changed lines are recorded)",
      scb.checked ? "The sounds are picked again" : "The same sounds as before",
      ...(features.localPictures ? [dcb.checked ? `New ${styleSel.options[styleSel.selectedIndex].text.split(" (")[0].toLowerCase().replace(/^3d/, "3D")} pictures are drawn for the video, picture book and cover` : "The same pictures as before"] : []),
    ], "Start the remake", start));
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
// ── unfinished stories: never lost, from any device ──
async function loadUnfinished() {
  const r = await api("/api/jobs/unfinished");
  const list = $("unfinishedList");
  list.innerHTML = "";
  const items = (r.ok && r.stories) || [];
  $("unfinished").hidden = !items.length;
  for (const u of items) {
    const row = document.createElement("div"); row.className = "unfinished-row";
    const info = document.createElement("div");
    const t = document.createElement("div"); t.className = "uf-title"; t.textContent = u.title;
    const m = document.createElement("div"); m.className = "uf-meta";
    const when = new Date(u.started.replace(" ", "T") + "Z").toLocaleString();
    m.textContent = `${u.written}${u.planned ? ` of ${u.planned}` : ""} scenes written · started ${when}`
      + (u.level ? ` · ${LEVEL_NAMES[u.level] || u.level}` : "");
    const why = document.createElement("div"); why.className = "uf-why"; why.textContent = u.error || "";
    info.append(t, m, why);
    const acts = document.createElement("div"); acts.className = "uf-actions";
    const mk = (label, cls, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = cls; b.textContent = label; b.addEventListener("click", fn); acts.appendChild(b); return b; };
    if (u.paused) { row.classList.add("paused"); why.textContent = "Paused. Everything made so far is kept."; }
    if (u.canResume) mk(u.paused ? "Resume" : "Recover", "small-btn go-small", async (e) => {
      e.target.disabled = true;
      const rr = await api(`/api/jobs/${u.id}/resume`, {});
      e.target.disabled = false;
      if (!rr.ok) return alert(rr.error || "Couldn't recover it.");
      checkActive();                                       // the progress shows on this and every device
      window.scrollTo({ top: 0, behavior: "smooth" });
      loadUnfinished();
    });
    if (u.written) mk("Read what's written", "small-btn", async () => {
      const rr = await api(`/api/jobs/${u.id}/text`);
      if (!rr.ok) return;
      const body = $("viewerBody"); body.innerHTML = "";
      const h = document.createElement("h3"); h.textContent = u.title;
      const sub = document.createElement("p"); sub.className = "sub"; sub.textContent = `Unfinished: ${rr.written} scenes so far`;
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "small-btn"; copy.textContent = "Copy the text";
      copy.addEventListener("click", () => { navigator.clipboard.writeText(rr.text).then(() => { copy.textContent = "Copied"; }); });
      body.append(h, sub, copy, renderReader(rr.text));
      $("viewer").showModal();
    });
    mk("Delete", "small-btn warn", async (e) => {
      if (e.target.dataset.sure !== "1") { e.target.dataset.sure = "1"; e.target.textContent = "Delete it for good?"; return; }
      await fetch(`/api/jobs/${u.id}`, { method: "DELETE" });
      loadUnfinished();
    });
    row.append(info, acts);
    list.appendChild(row);
  }
}

// ── clean up the writing: what the sentence checker finds, and the summary afterwards ──
function writingReport(c) {
  const box = document.createElement("div"); box.className = "writing-report";
  const head = document.createElement("p"); head.className = "wr-head";
  head.textContent = c.problems
    ? `${c.problems} sentence or spelling problem${c.problems === 1 ? "" : "s"} found in ${c.scenes_with} of ${c.scenes} scene${c.scenes === 1 ? "" : "s"}.`
    : "The sentence checker found nothing to fix. The proofreader can still smooth the grammar and the dialogue.";
  box.appendChild(head);
  if (c.examples && c.examples.length) {
    const ul = document.createElement("ul"); ul.className = "wr-list";
    for (const e of c.examples) {
      const li = document.createElement("li");
      const q = document.createElement("q"); q.textContent = e.sentence.length > 160 ? `${e.sentence.slice(0, 160)}…` : e.sentence;
      const why = document.createElement("span"); why.className = "wr-why"; why.textContent = `Scene ${e.scene}: ${e.problem}`;
      li.append(q, why); ul.appendChild(li);
    }
    box.appendChild(ul);
  }
  return box;
}
function cleanupSummary(st) {
  const r = (st && st.result) || {};
  if (r.problems_before === undefined) return "Done.";
  const failed = (r.failed || []).length ? ` ${(r.failed || []).length} couldn't be done.` : "";
  return `Done. ${r.scenes_changed} scene${r.scenes_changed === 1 ? "" : "s"} tidied${r.stories > 1 ? ` across ${r.stories} stories` : ""}; ` +
    `sentence problems ${r.problems_before} → ${r.problems_after}.${failed}`;
}
async function cleanupAll() {
  $("nasPanel").hidden = true;
  const box = $("cleanupAll");
  box.hidden = false; box.innerHTML = '<h3>Clean up the writing</h3><p class="hint">Checking every story…</p>';
  const r = await api("/api/writing-check");
  box.innerHTML = "<h3>Clean up the writing</h3>";
  const list = (r.stories || []).filter((x) => !x.error);
  if (!r.ok || !list.length) { box.insertAdjacentHTML("beforeend", `<p class="hint">${r.error || "No stories to clean up."}</p>`); return; }
  const hint = document.createElement("p"); hint.className = "hint";
  hint.textContent = "The proofreader and the sentence checker go over each story you tick, one after another. Events, names, style, heat and sounds stay the same. It runs in the background, so you can close the page.";
  const rows = document.createElement("div"); rows.className = "wr-rows";
  const boxes = [];
  list.sort((a, b) => b.problems - a.problems);
  for (const x of list) {
    const l = document.createElement("label"); l.className = "check wr-row";
    const c = document.createElement("input"); c.type = "checkbox"; c.checked = x.problems > 0; c.value = x.run_id;
    const t = document.createElement("span"); t.className = "wr-title"; t.textContent = x.title || x.run_id;
    const n = document.createElement("span"); n.className = "wr-count";
    n.textContent = x.problems ? `${x.problems} problem${x.problems === 1 ? "" : "s"}, ${x.scenes_with} of ${x.scenes} scenes` : "looks clean";
    l.append(c, t, n); rows.appendChild(l); boxes.push(c);
  }
  const rl = document.createElement("label"); rl.className = "check";
  const rc = document.createElement("input"); rc.type = "checkbox"; rc.checked = true;
  rl.append(rc, document.createTextNode(" Remake the audio afterwards (only changed lines are recorded again)"));
  const bar = document.createElement("div"); bar.className = "wr-bar";
  const go = document.createElement("button"); go.className = "go go-small"; go.type = "button";
  const close = document.createElement("button"); close.className = "small-btn"; close.type = "button"; close.textContent = "Close";
  close.addEventListener("click", () => { box.hidden = true; box.innerHTML = ""; });
  const count = () => { const n = boxes.filter((c) => c.checked).length; go.textContent = `Clean up ${n} stor${n === 1 ? "y" : "ies"}`; go.disabled = n === 0; };
  boxes.forEach((c) => c.addEventListener("change", count)); count();
  const msg = document.createElement("p"); msg.className = "settings-msg";
  bar.append(go, close);
  box.append(hint, rows, rl, bar, msg);
  go.addEventListener("click", () => {
    const picked = boxes.filter((c) => c.checked);
    const names = picked.map((c) => (list.find((x) => x.run_id === c.value) || {}).title || c.value);
    confirmStart(box, go, `Ready to clean up ${picked.length} stor${picked.length === 1 ? "y" : "ies"}:`, [
      names.length > 4 ? `${names.slice(0, 4).join(", ")} and ${names.length - 4} more` : names.join(", "),
      "One after another, in the background (you can close the page)",
      rc.checked ? "Each story's audio is remade afterwards (only changed lines)" : "The audio is left as it is",
    ], "Start cleaning up", async () => {
      go.disabled = true;
      const res = await api("/api/cleanup", { ids: picked.map((c) => c.value), remake: rc.checked });
      if (!res.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = res.error; return; }
      const m = jobMeter(); box.insertBefore(m, msg);
      followJob(res.job_id, m, (st) => { msg.className = "settings-msg"; msg.textContent = cleanupSummary(st); loadLibrary(); },
        (err) => { msg.className = "settings-msg bad"; msg.textContent = err; go.disabled = false; loadLibrary(); }, "cleaning up these stories");
    });
  });
}
$("cleanupAllBtn").addEventListener("click", cleanupAll);

// ───────────────────────────── the NAS ─────────────────────────────
const gb = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round((n || 0) / 1e6))} MB`);
const shortDate = (s) => { const d = new Date(String(s || "").replace(/_(\d\d)(\d\d)(\d\d)$/, "T$1:$2:$3")); return isNaN(d) ? "" : d.toLocaleDateString(); };
// follow a NAS move: the meter fills, then done(task)
function followNas(tid, meter, done) {
  const t = setInterval(async () => {
    const st = await api(`/api/nas/task/${tid}`);
    if (st.ok === false && !st.state) { clearInterval(t); done({ state: "failed", failed: [{ error: st.error }] }); return; }
    meter.set(st.progress || 0, st.detail || "");
    if (["done", "failed", "partly"].includes(st.state)) { clearInterval(t); done(st); }
  }, 1500);
}
function nasSummary(st, verb) {
  const ok = (st.done || []).length, bad = st.failed || [];
  return `${ok} ${verb}.` + (bad.length ? ` ${bad.length} couldn't be: ${bad.map((f) => `${f.label ? f.label + ": " : ""}${f.error}`).join(" ")}` : "");
}
async function nasPanel() {
  const box = $("nasPanel");
  if (!box.hidden) { box.hidden = true; return; }
  $("cleanupAll").hidden = true;
  box.hidden = false; box.innerHTML = '<p class="hint">Checking the NAS…</p>';
  const [nas, cand] = await Promise.all([api("/api/nas"), api("/api/nas/candidates")]);
  box.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = "Move stories to the NAS"; box.appendChild(h);
  if (!nas.ok) {
    const p = document.createElement("p"); p.className = "settings-msg bad"; p.textContent = nas.error || "The NAS isn't reachable.";
    box.appendChild(p); return;
  }
  const intro = document.createElement("p"); intro.className = "hint";
  intro.textContent = `Frees space on this computer. Each story is copied to the NAS and checked file by file; only then is it removed from here. Bring it back any time from On the NAS.${nas.free ? ` The NAS has ${gb(nas.free)} free.` : ""}`;
  box.appendChild(intro);
  const list = cand.stories || [];
  if (!list.length) { const p = document.createElement("p"); p.textContent = "No stories on this computer to move."; box.appendChild(p); return; }
  const tools = document.createElement("div"); tools.className = "cleanup-tools";
  const all = document.createElement("button"); all.type = "button"; all.className = "small-btn"; all.textContent = "Select all";
  const none = document.createElement("button"); none.type = "button"; none.className = "small-btn"; none.textContent = "Select none";
  const total = document.createElement("span"); total.className = "hint";
  tools.append(all, none, total); box.appendChild(tools);
  const ul = document.createElement("div"); ul.className = "cleanup-list";
  const boxes = list.map((st) => {
    const l = document.createElement("label"); l.className = "check nas-row";
    const c = document.createElement("input"); c.type = "checkbox"; c.value = st.id; c.disabled = st.busy; c.dataset.size = st.size;
    const name = document.createElement("span"); name.textContent = st.title;
    const info = document.createElement("span"); info.className = "hint";
    info.textContent = st.busy ? "being worked on" : `${gb(st.size)} · ${shortDate(st.created)}${st.level ? ` · ${LEVEL_NAMES[st.level] || st.level}` : ""}`;
    l.append(c, name, info); ul.appendChild(l); return c;
  });
  box.appendChild(ul);
  const [kl, kc] = (() => { const l = document.createElement("label"); l.className = "check"; const c = document.createElement("input"); c.type = "checkbox"; l.append(c, document.createTextNode(" Keep a copy on this computer too (just back up, don't free the space)")); return [l, c]; })();
  box.appendChild(kl);
  const go = document.createElement("button"); go.type = "button"; go.className = "go"; go.textContent = "Move to the NAS";
  const msg = document.createElement("p"); msg.className = "settings-msg";
  box.append(go, msg);
  const update = () => {
    const picked = boxes.filter((c) => c.checked);
    const size = picked.reduce((n, c) => n + Number(c.dataset.size || 0), 0);
    total.textContent = picked.length ? `${picked.length} picked, ${gb(size)}` : "";
    go.disabled = !picked.length;
    go.textContent = kc.checked ? "Back up to the NAS" : "Move to the NAS";
  };
  boxes.forEach((c) => c.addEventListener("change", update)); kc.addEventListener("change", update);
  all.addEventListener("click", () => { boxes.forEach((c) => { if (!c.disabled) c.checked = true; }); update(); });
  none.addEventListener("click", () => { boxes.forEach((c) => { c.checked = false; }); update(); });
  update();
  go.addEventListener("click", () => {
    const picked = boxes.filter((c) => c.checked);
    const size = picked.reduce((n, c) => n + Number(c.dataset.size || 0), 0);
    confirmStart(box, go, kc.checked ? `Ready to back up ${picked.length} stor${picked.length === 1 ? "y" : "ies"}:` : `Ready to move ${picked.length} stor${picked.length === 1 ? "y" : "ies"}:`, [
      `${gb(size)} copied to the NAS, then every file checked`,
      kc.checked ? "They stay on this computer too" : `Then removed from this computer, freeing ${gb(size)}`,
      "Bring any of them back later from On the NAS",
    ], kc.checked ? "Start the backup" : "Start moving", async () => {
      go.disabled = true;
      const r = await api("/api/nas/archive", { ids: picked.map((c) => c.value), keep: kc.checked });
      if (!r.ok) { go.disabled = false; msg.className = "settings-msg bad"; msg.textContent = r.error; return; }
      const m = jobMeter(); box.insertBefore(m, msg);
      followNas(r.task, m, (st) => {
        msg.className = st.failed && st.failed.length ? "settings-msg bad" : "settings-msg";
        msg.textContent = nasSummary(st, kc.checked ? "backed up" : "moved to the NAS");
        loadLibrary();
      });
    });
  });
}
$("nasBtn").addEventListener("click", nasPanel);
// Library tabs: Stories (with their covers) and Archive (what's on the NAS: a plain list)
let libTab = "stories";
try { if (localStorage.getItem("libTab") === "archive") libTab = "archive"; } catch { /* private window */ }
function setLibTab(t) {
  libTab = t === "archive" && !$("tabArchive").hidden ? "archive" : "stories";
  try { localStorage.setItem("libTab", libTab); } catch { /* private window */ }
  const arch = libTab === "archive";
  $("tabStories").setAttribute("aria-selected", String(!arch)); $("tabArchive").setAttribute("aria-selected", String(arch));
  $("shelf").hidden = arch; $("nasShelf").hidden = !arch;
  $("shelfEmpty").hidden = arch || $("shelf").children.length > 0;
  $("unfinished").classList.toggle("tab-hidden", arch);
  $("filter").hidden = arch; $("cleanupAllBtn").hidden = arch;
  if (arch) $("cleanupAll").hidden = true;
}
$("tabStories").addEventListener("click", () => setLibTab("stories"));
$("tabArchive").addEventListener("click", () => setLibTab("archive"));
async function loadNas() {
  const r = await api("/api/nas");
  const list = $("nasList");
  $("nasBtn").hidden = r.configured === false;
  $("tabArchive").hidden = r.configured === false;
  const away = (r.items || []).filter((it) => !it.onComputer);
  $("tabArchive").textContent = away.length ? `Archive (${away.length})` : "Archive";
  setLibTab(libTab);
  if (!r.ok && r.configured !== false) {
    list.innerHTML = ""; $("nasEmpty").hidden = true;
    $("nasShelfHint").textContent = `${r.error || "Can't reach the NAS right now."} Your stories there are safe; they'll show again once it's back.`;
    return;
  }
  $("nasEmpty").hidden = away.length > 0;
  list.innerHTML = "";
  for (const it of away) {
    const row = document.createElement("div"); row.className = "nas-item";
    const t = document.createElement("div"); t.className = "nas-t";
    const name = document.createElement("strong"); name.textContent = it.title;
    const info = document.createElement("span"); info.className = "hint";
    info.textContent = `${it.mode === "video" ? "Video" : it.mode === "audio" ? "Audio" : "Story"} · ${gb(it.size)} · moved ${shortDate(it.archived)}${it.level ? ` · ${LEVEL_NAMES[it.level] || it.level}` : ""}${it.by ? ` · by ${it.by}` : ""}`;
    t.append(name, info);
    const back = document.createElement("button"); back.type = "button"; back.className = "small-btn"; back.textContent = "Bring back";
    const msg = document.createElement("p"); msg.className = "settings-msg";
    back.addEventListener("click", async () => {
      back.disabled = true;
      const res = await api("/api/nas/restore", { ids: [it.id] });
      if (!res.ok) { back.disabled = false; msg.className = "settings-msg bad"; msg.textContent = res.error; return; }
      const m = jobMeter(); row.appendChild(m);
      followNas(res.task, m, (st) => {
        if (st.failed && st.failed.length) { back.disabled = false; msg.className = "settings-msg bad"; msg.textContent = nasSummary(st, "brought back"); return; }
        msg.className = "settings-msg"; msg.textContent = "Back in your library (Stories)."; setTimeout(loadLibrary, 1200);
      });
    });
    row.append(t, back, msg); list.appendChild(row);
  }
}

async function loadLibrary() {
  loadUnfinished();
  loadNas();
  loadQueue();
  const r = await api("/api/stories");
  const filter = $("filter").value;
  const shelf = $("shelf");
  shelf.innerHTML = "";
  const list = (r.stories || []).filter((s) => !filter || s.series === filter);
  $("shelfEmpty").hidden = list.length > 0 || libTab === "archive";
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
    const what = s.adventure ? "Choose your own adventure" : s.video ? "Video" : s.audio ? "Audio" : "Story";
    sm.textContent = `${what}${s.episode > 1 ? `, episode ${s.episode}` : ""}${s.level ? `, ${LEVEL_NAMES[s.level] || s.level}` : ""}${s.by ? `, by ${s.by}` : ""}`;
    meta.append(t, sm); b.appendChild(meta);
    if (s.bedtime || s.adventure) {
      const bt = document.createElement("span"); bt.className = "badge bedtime-badge";
      bt.textContent = [s.adventure ? "Adventure" : "", s.bedtime ? "Bedtime" : ""].filter(Boolean).join(", ");
      b.appendChild(bt);
    }
    if (s.listened > 0.02 && s.listened < 0.97) {        // how far you got
      const pr = document.createElement("div"); pr.className = "listened"; pr.title = `${Math.round(s.listened * 100)}% listened`;
      const f = document.createElement("span"); f.style.width = `${Math.round(s.listened * 100)}%`; pr.appendChild(f); b.appendChild(pr);
    }
    isSavedOffline(s).then((yes) => {
      if (yes) { const o = document.createElement("span"); o.className = "badge saved-badge"; o.textContent = "On this device"; b.appendChild(o); }
      else if (!navigator.onLine) b.classList.add("unavailable");
    });
    b.addEventListener("click", () => { storyCard(s, $("viewerBody")); $("viewer").showModal(); });
    shelf.appendChild(b);
  }
  $("offlineNote").hidden = navigator.onLine;
  openLinkedStory(r.stories || []);
}
// a link from a notification (…/#story=<id>) opens that story
function openLinkedStory(stories) {
  const m = location.hash.match(/^#story=(.+)$/);
  if (!m) return;
  const s = stories.find((x) => x.id === decodeURIComponent(m[1]));
  history.replaceState(null, "", location.pathname);
  if (s) { storyCard(s, $("viewerBody")); $("viewer").showModal(); }
}
window.addEventListener("online", () => loadLibrary());
window.addEventListener("offline", () => loadLibrary());

// ── bedtime: a calm, short story by default ──
$("bedtime").addEventListener("change", () => {
  if (!$("bedtime").checked) return;
  if (parseInt($("chapters").value, 10) > 1) $("chapters").value = "1";
  if (parseInt($("scenes").value, 10) > 5) { $("scenes").value = "5"; $("scenes").dispatchEvent(new Event("input")); }
  const epic = document.querySelector('input[name="detail"][value="epic"]');
  if (epic && epic.checked) document.querySelector('input[name="detail"][value="standard"]').checked = true;
  const calm = document.querySelector('input[name="pictureStyle"][value="epic"]');
  if (calm && calm.checked) { const sb = document.querySelector('input[name="pictureStyle"][value="storybook"]'); if (sb) sb.checked = true; }
});

// ── your private podcast feed ──
async function feedPanel() {
  const p = $("feedPanel");
  const btnEl = $("feedBtn");
  if (!p.hidden) { p.hidden = true; btnEl.setAttribute("aria-expanded", "false"); return; }
  p.hidden = false; btnEl.setAttribute("aria-expanded", "true");
  p.innerHTML = "<p class=\"hint\">Loading…</p>";
  const draw = (r) => {
    p.innerHTML = "";
    const h = document.createElement("h3"); h.textContent = "Your podcast feed";
    const hint = document.createElement("p"); hint.className = "hint";
    hint.textContent = "Every story with audio shows up as an episode in a podcast app, newest first, with its chapters and cover. The address is private to you: anyone who has it can listen, so don't share it.";
    p.append(h, hint);
    if (!r.url) {
      const make = document.createElement("button"); make.type = "button"; make.className = "go go-small"; make.textContent = "Make my feed address";
      make.addEventListener("click", async () => draw(await api("/api/feed?make=1", null, "GET")));
      p.appendChild(make); return;
    }
    const row = document.createElement("div"); row.className = "feed-row";
    const inp = document.createElement("input"); inp.type = "text"; inp.readOnly = true; inp.value = r.url; inp.setAttribute("aria-label", "Feed address");
    inp.addEventListener("focus", () => inp.select());
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "small-btn"; copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(r.url); copy.textContent = "Copied"; } catch { inp.select(); copy.textContent = "Press Copy on your keyboard"; }
      setTimeout(() => { copy.textContent = "Copy"; }, 2500);
    });
    row.append(inp, copy); p.appendChild(row);
    const how = document.createElement("ul"); how.className = "hint feed-how";
    for (const t of [
      "iPhone or iPad: open Podcasts, then Library, then the ⋯ button, and choose Follow a Show by URL. Paste the address.",
      "Try Apple Podcasts first. Many other apps (Overcast and Pocket Casts among them) fetch feeds from their own servers, which can't reach a private address like this one.",
      "Your phone has to be able to reach StoryMaker (on Tailscale, or at home) to download new episodes.",
    ]) { const li = document.createElement("li"); li.textContent = t; how.appendChild(li); }
    p.appendChild(how);
    if (!r.https) {
      const w = document.createElement("p"); w.className = "note";
      w.textContent = "This address starts with http. Some podcast apps only accept https: open StoryMaker at its secure address (your Tailscale .ts.net name) and the feed address will use it.";
      p.appendChild(w);
    }
    const tools = document.createElement("div"); tools.className = "feed-row";
    if (r.canExplicit) {
      const l = document.createElement("label"); l.className = "check";
      const c = document.createElement("input"); c.type = "checkbox"; c.checked = !!r.explicit;
      c.addEventListener("change", async () => draw({ ...r, ...(await api("/api/feed", { explicit: c.checked })) }));
      l.append(c, " Include Explicit stories"); tools.appendChild(l);
    }
    const reset = document.createElement("button"); reset.type = "button"; reset.className = "small-btn"; reset.textContent = "New address";
    reset.title = "The old address stops working. Use this if it got out.";
    reset.addEventListener("click", () => {
      confirmStart(tools, reset, "Make a new address?", ["The old address stops working straight away.", "You'll need to add the new one to your podcast app again."], "Make a new address",
        async () => draw({ ...r, ...(await api("/api/feed", { reset: true })) }));
    });
    tools.appendChild(reset); p.appendChild(tools);
  };
  draw(await api("/api/feed", null, "GET"));
}
$("feedBtn").addEventListener("click", feedPanel);

// ── works as an app on the phone's home screen, and plays saved stories with no connection ──
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(() => {});
}

// "Next episode" on a story: the make form, set to continue its series
function nextEpisode(name) {
  const v = document.getElementById("viewer"); if (v && v.close) v.close();
  const r = document.querySelector('input[name="kind"][value="next"]'); r.checked = true; updateKind();
  $("series").value = name;
  $("idea").value = "";
  window.scrollTo({ top: 0, behavior: "smooth" });
  setTimeout(() => $("idea").focus(), 400);
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
// Pictures: only when something gets drawn (a video, or a picture book)
const PICTURE_HINTS = { storybook: "Illustrated, like a picture book.",
  realistic: "Like photographs of real people and places. Best with a realistic model in Draw Things (see the checks).",
  cinematic: "Like stills from a film: real people, dramatic light. Best with a realistic model in Draw Things.",
  "3d": "Like a 3D animated film: rendered characters, soft light, rich colour.",
  epic: "Big action moments frozen mid-strike: dramatic light, dust and sparks, heroic angles. Best for fights and battles." };
function updatePictureStyle() {
  const mode = (document.querySelector('input[name="mode"]:checked') || {}).value;
  $("pictureStyleRow").hidden = !(mode === "video" || $("book").checked);
  const v = (document.querySelector('input[name="pictureStyle"]:checked') || {}).value || "storybook";
  const next = (document.querySelector('input[name="kind"]:checked') || {}).value === "next";
  $("pictureStyleHint").textContent = next && !pictureStyleTouched
    ? "The series keeps its own look (set it in the Series bible). Pick one here only to change it for this episode."
    : PICTURE_HINTS[v];
  try { localStorage.setItem("pictureStyle", v); } catch { /* private window */ }
}
try {
  const saved = localStorage.getItem("pictureStyle");
  const r = saved && document.querySelector(`input[name="pictureStyle"][value="${saved}"]`);
  if (r) r.checked = true;
} catch { /* private window */ }
var pictureStyleTouched = false;   // (var: updateKind may read it before this line runs)
document.querySelectorAll('input[name="pictureStyle"]').forEach((r) => r.addEventListener("change", () => { pictureStyleTouched = true; }));
document.querySelectorAll('input[name="mode"], input[name="pictureStyle"], #book').forEach((r) => r.addEventListener("change", updatePictureStyle));
updatePictureStyle();

updateLength();
loadMe();
loadFeatures();
loadSeries();
loadLibrary();

// ── the same work on every device: a story started on the Mac shows its progress on the iPad and iPhone too ──
const JOB_NAMES = { remaster: "Remaking the audio", rewrite: "Rewriting", cleanup: "Cleaning up the writing" };
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
    const stop = document.createElement("button"); stop.type = "button"; stop.className = "small-btn warn"; stop.textContent = "Stop";
    stop.addEventListener("click", async () => { if (await stopJob(j.id, (JOB_NAMES[j.kind] || "this").toLowerCase(), stop)) setTimeout(checkActive, 3000); });
    const btns = document.createElement("div"); btns.className = "aj-btns";
    if (!j.kind || j.kind === "make") {
      const pause = document.createElement("button"); pause.type = "button"; pause.className = "small-btn"; pause.textContent = "Pause";
      pause.addEventListener("click", async () => {
        pause.disabled = true; pause.textContent = "Pausing…";
        await api(`/api/jobs/${j.id}/pause`, {}); setTimeout(() => { checkActive(); loadUnfinished(); }, 3000);
      });
      btns.appendChild(pause);
    }
    btns.appendChild(stop);
    row.append(what, bar, det, btns);
    return row;
  }));
  box.hidden = !others.length;
}
checkActive();
setInterval(checkActive, 6000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkActive(); });
window.addEventListener("focus", checkActive);

// ── your style ──
let styleData = null;
function curProfile() { return document.querySelector('input[name="sprof"]:checked').value; }
function profileData() {
  styleData.writing = styleData.writing || {};
  styleData.writing[curProfile()] = styleData.writing[curProfile()] || { references: [], feedback: [] };
  return styleData.writing[curProfile()];
}
function readProfileForm() {
  const w = profileData();
  w.tone = $("stTone").value; w.use = $("stUse").value; w.avoid = $("stAvoid").value; w.share = $("stShare").checked;
  w.references = [...$("refList").querySelectorAll("textarea")].map((t) => t.value).filter((v) => v.trim());
}
function fillProfileForm() {
  const w = profileData();
  $("stTone").value = w.tone || ""; $("stUse").value = w.use || ""; $("stAvoid").value = w.avoid || "";
  $("stShare").checked = w.share !== false;
  $("refList").innerHTML = "";
  (w.references || []).forEach((r) => addRef(r));
  if (!(w.references || []).length) addRef();                  // always one reference box ready to paste into
  const fb = $("fbList"); fb.innerHTML = "";
  if (!(w.feedback || []).length) fb.innerHTML = '<p class="level-note">Nothing yet. Use Feedback on a story you\'ve made.</p>';
  (w.feedback || []).forEach((f, i) => {
    const row = document.createElement("div"); row.className = "fb-row";
    const t = document.createElement("span");
    t.textContent = `${f.rating === "up" ? "More like this" : f.rating === "down" ? "Less like this" : "Note"}${f.title ? ` (${f.title})` : ""}: ${f.notes}`;
    const del = document.createElement("button"); del.type = "button"; del.className = "small-btn warn"; del.textContent = "Remove";
    del.addEventListener("click", () => { w.feedback.splice(i, 1); fillProfileForm(); });
    row.append(t, del); fb.appendChild(row);
  });
}
function addRef(text = "") {
  const box = document.createElement("div"); box.className = "ref-row";
  const ta = document.createElement("textarea"); ta.rows = 5; ta.value = text; ta.placeholder = "Paste a few paragraphs of writing whose voice you want.";
  ta.setAttribute("aria-label", "Passage to match");
  const del = document.createElement("button"); del.type = "button"; del.className = "small-btn warn"; del.textContent = "Remove";
  del.addEventListener("click", () => box.remove());
  box.append(ta, del); $("refList").appendChild(box);
}
$("addRef").addEventListener("click", () => { if ($("refList").children.length < 5) addRef(); });
document.querySelectorAll('input[name="stab"]').forEach((r) => r.addEventListener("change", () => {
  document.querySelectorAll(".style-pane").forEach((p) => { p.hidden = p.dataset.pane !== r.value; });
}));
let lastProfile = "sfw";
const PROF_HINTS = { kids: "Young kids, Kids and Middle grade stories.", family: "Stories for Everyone: the whole family together.",
  sfw: "Teens and Adults stories: clean, nothing you couldn't read at work.", mature: "Mature (R) stories: strong language and heat, nothing graphic.",
  explicit: "Explicit (18+) stories, written by the writer on your own computer." };
function showProfHint() { $("profHint").textContent = PROF_HINTS[curProfile()] || ""; }
document.querySelectorAll('input[name="sprof"]').forEach((r) => r.addEventListener("change", () => {
  const now = curProfile();
  document.querySelector(`input[name="sprof"][value="${lastProfile}"]`).checked = true; readProfileForm();
  document.querySelector(`input[name="sprof"][value="${now}"]`).checked = true; lastProfile = now; fillProfileForm(); showProfHint();
}));
$("styleBtn").addEventListener("click", async () => {
  const r = await api("/api/style");
  styleData = (r.ok && r.style) || {};
  const allowed = r.profiles || ["kids", "family", "sfw"];
  document.querySelectorAll("#profileRow label[data-prof]").forEach((l) => { l.hidden = !allowed.includes(l.dataset.prof); });
  document.querySelector('input[name="sprof"][value="sfw"]').checked = true; lastProfile = "sfw";
  fillProfileForm(); showProfHint();
  $("stPic").value = (styleData.pictures || {}).style || ""; $("stPicAvoid").value = (styleData.pictures || {}).avoid || "";
  $("stVid").value = (styleData.video || {}).style || "";
  $("stSnd").value = (styleData.sounds || {}).like || ""; $("stSndAvoid").value = (styleData.sounds || {}).avoid || "";
  $("styleMsg").textContent = "";
  $("styleDlg").showModal();
});
$("styleSave").addEventListener("click", async () => {
  readProfileForm();
  styleData.pictures = { style: $("stPic").value, avoid: $("stPicAvoid").value };
  styleData.video = { style: $("stVid").value };
  styleData.sounds = { like: $("stSnd").value, avoid: $("stSndAvoid").value };
  const r = await api("/api/style", styleData);
  $("styleMsg").className = r.ok ? "settings-msg" : "settings-msg bad";
  $("styleMsg").textContent = r.ok ? "Saved. Every story you make from now on uses it." : (r.error || "Not saved.");
});
