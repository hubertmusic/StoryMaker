// Audio Studio page
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c.nodeType ? c : document.createTextNode(c));
  return n;
};
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(path, o).catch(() => null);
  if (!r) return { ok: false, error: "Can't reach the studio. Is it running?" };
  if (r.status === 401) { location.href = "/login.html"; return { ok: false }; }
  return r.json().catch(() => ({ ok: false, error: `The studio answered ${r.status}.` }));
}

const LEVEL_NAMES = { "young-kids": "Young kids", kids: "Kids", "middle-grade": "Middle grade", teen: "Teens",
  adult: "Adults", family: "Everyone", mature: "Mature", explicit: "Explicit 18+" };
const AGES = [["child", "Child"], ["teen", "Teen"], ["adult", "Adult"], ["elder", "Elder"]];
const PITCH = [["", "Natural"], ["^0.94", "Deeper"], ["^1.04", "Brighter"], ["^1.08", "Younger"]];
const JOBS = { cast: "Finding who speaks", sounds: "Choosing the sounds", record: "Recording", all: "Making the audiobook" };
const GENDER_WORD = (c) => c.gender === "female"
  ? { child: "girl", teen: "teenage girl", adult: "woman", elder: "older woman" }[c.age || "adult"]
  : { child: "boy", teen: "teenage boy", adult: "man", elder: "older man" }[c.age || "adult"];

let me = null, voices = [], book = null, tab = "cast", chapterIx = 0, poll = null, lastState = "";
const edits = new Map();          // script changes not saved yet

// ───────────────────────── start ─────────────────────────
(async function start() {
  me = await api("/api/me");
  if (!me.ok) return;
  $("whoAmI").textContent = me.username;
  document.querySelector(".brand a").title = `Audio Studio ${me.version || ""}`;
  document.body.append(el("p", { class: "version", text: `Audio Studio ${me.version || ""}` }));
  $("smLink").href = me.storymaker || `${location.protocol}//${location.hostname}${location.protocol === "https:" ? "" : ":8095"}/`;
  const v = await api("/api/voices");
  voices = v.voices || [];
  buildLevels();
  const imp = new URLSearchParams(location.search).get("import");
  if (imp) {
    history.replaceState(null, "", "/" + location.hash);
    showHome();
    $("importErr").hidden = true;
    $("importGo").disabled = true; $("importGo").textContent = "Bringing it in…";
    const r = await api("/api/import/storymaker", { json: { id: imp } });
    $("importGo").disabled = false; $("importGo").textContent = "Bring it in";
    if (r.ok) { location.hash = `b=${r.id}`; return; }
    showError(r.error);
  }
  route();
})();
window.addEventListener("hashchange", route);
$("signOut").onclick = async () => { await api("/api/logout", { method: "POST" }); location.href = "/login.html"; };

function route() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("b")) openBook(h.get("b"), h.get("t"));
  else showHome();
}

// ───────────────────────── home ─────────────────────────
function buildLevels() {
  const box = $("levelChips");
  box.replaceChildren(...me.levels.map((lv) => el("label", {},
    el("input", { type: "radio", name: "level", value: lv, checked: lv === "family" }), el("span", { text: LEVEL_NAMES[lv] || lv }))));
}
async function showHome() {
  stopPolling();
  book = null;
  document.body.classList.remove("on-air");
  $("book").hidden = true; $("home").hidden = false;
  document.title = "Audio Studio";
  const [p, s] = await Promise.all([api("/api/projects"), api("/api/storymaker/stories")]);
  const books = $("books");
  books.replaceChildren();
  if (!(p.projects || []).length) books.append(el("p", { class: "empty", text: "Nothing here yet. Bring in a story from StoryMaker or a book file, and it will show up here." }));
  for (const b of p.projects || []) {
    const pill = b.state === "running" || b.state === "queued" ? el("span", { class: "pill live", text: b.state === "queued" ? "Waiting" : "Working" })
      : b.has_book ? el("span", { class: "pill done", text: "Audiobook ready" })
      : b.state === "failed" ? el("span", { class: "pill bad", text: "Stopped by a problem" })
      : el("span", { class: "pill", text: b.cast_done ? "Cast" : "New" });
    books.append(el("button", { type: "button", class: "book-row", onclick: () => { location.hash = `b=${b.id}`; } },
      b.has_cover ? el("img", { src: `/api/projects/${b.id}/file/cover.jpg`, alt: "" }) : el("span", { class: "noart", text: (b.title || "?")[0] }),
      el("span", {}, el("div", { class: "t", text: b.title }),
        el("div", { class: "m", text: `${b.chapters} chapter${b.chapters === 1 ? "" : "s"} · ${Number(b.words || 0).toLocaleString()} words · ${LEVEL_NAMES[b.level] || b.level}` })),
      pill));
  }
  const sel = $("smStory");
  sel.replaceChildren();
  if (!s.ok) sel.append(el("option", { value: "", text: s.error || "StoryMaker isn't answering" }));
  else if (!s.stories.length) sel.append(el("option", { value: "", text: "No StoryMaker stories yet" }));
  else for (const st of s.stories) {
    const label = `${st.title}${st.series ? ` (episode ${st.episode})` : ""}${st.imported ? " · already in the studio" : ""}`;
    sel.append(el("option", { value: st.id, text: label }));
  }
}
document.querySelectorAll("input[name=src]").forEach((r) => r.addEventListener("change", () => {
  const v = document.querySelector("input[name=src]:checked").value;
  $("srcSm").hidden = v !== "sm"; $("srcFile").hidden = v !== "file"; $("srcPaste").hidden = v !== "paste";
  $("titleRow").hidden = $("levelRow").hidden = v === "sm";
}));
function showError(msg) { $("importErr").textContent = msg || "That didn't work."; $("importErr").hidden = false; }
$("importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("importErr").hidden = true;
  const src = document.querySelector("input[name=src]:checked").value;
  const level = (document.querySelector("input[name=level]:checked") || {}).value || "family";
  let r;
  $("importGo").disabled = true; $("importGo").textContent = "Bringing it in…";
  if (src === "sm") {
    if (!$("smStory").value) { showError("Pick a story first."); r = null; }
    else r = await api("/api/import/storymaker", { json: { id: $("smStory").value } });
  } else if (src === "file") {
    const f = $("bookFile").files[0];
    if (!f) { showError("Choose a book file first."); r = null; }
    else {
      const fd = new FormData();
      fd.append("file", f); fd.append("title", $("bookTitle").value); fd.append("level", level);
      r = await api("/api/import/file", { method: "POST", body: fd });
    }
  } else {
    if (!$("pasteText").value.trim()) { showError("Paste some text first."); r = null; }
    else r = await api("/api/import/text", { json: { title: $("bookTitle").value, text: $("pasteText").value, level } });
  }
  $("importGo").disabled = false; $("importGo").textContent = "Bring it in";
  if (!r) return;
  if (!r.ok) return showError(r.error);
  location.hash = `b=${r.id}`;
});

// ───────────────────────── one book ─────────────────────────
async function openBook(id, t) {
  const r = await api(`/api/projects/${id}`);
  if (!r.ok) { location.hash = ""; return; }
  const switching = !book || book.id !== id;
  book = r;
  if (switching) { edits.clear(); chapterIx = 0; }
  $("home").hidden = true; $("book").hidden = false;
  document.title = `${book.title} · Audio Studio`;
  $("bTitle").textContent = book.title;
  const talkers = book.cast.filter((c) => c.lines > 0).length;
  $("bMeta").textContent = `${book.chapters.length} chapter${book.chapters.length === 1 ? "" : "s"} · ${Number(book.words).toLocaleString()} words`
    + (book.castDone ? ` · ${talkers} speaking part${talkers === 1 ? "" : "s"} and the narrator` : "")
    + ` · ${LEVEL_NAMES[book.level] || book.level}`;
  showTab(t || tab || (book.castDone ? "script" : "cast"), true);
  renderRun(book.status);
  if (["running", "queued"].includes(book.status.state)) startPolling();
}
function showTab(name, keep) {
  tab = name;
  document.querySelectorAll(".step").forEach((b) => b.setAttribute("aria-current", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  document.querySelector('.step[data-tab="cast"]').classList.toggle("ok", book.castDone);
  document.querySelector('.step[data-tab="sounds"]').classList.toggle("ok", book.soundsDone);
  document.querySelector('.step[data-tab="listen"]').classList.toggle("ok", !!book.files.m4b);
  ({ cast: renderCast, script: renderScript, sounds: renderSounds, record: renderRecord, listen: renderListen })[name]();
  if (!keep) history.replaceState(null, "", `#b=${book.id}&t=${name}`);
}
document.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => {
  if (edits.size && !confirm("You have script changes that aren't saved. Leave them?")) return;
  edits.clear();
  showTab(b.dataset.tab);
}));
$("book").querySelector(".back").addEventListener("click", (e) => { e.preventDefault(); location.hash = ""; });
$("homeLink").addEventListener("click", (e) => { e.preventDefault(); location.hash = ""; });

// the bar at the top: what's happening, and the one button that does everything
function renderRun(st) {
  st = st || {};
  const busy = ["running", "queued"].includes(st.state);
  document.body.classList.toggle("on-air", busy && (st.job === "record" || st.job === "all"));
  $("makeAll").disabled = busy; $("stopJob").hidden = !busy;
  $("runMeterWrap").hidden = !busy;
  $("runMeter").style.width = `${st.progress || 0}%`;
  let state, detail = st.detail || "";
  if (busy) state = `${JOBS[st.job] || "Working"}… ${st.state === "running" ? `${st.progress || 0}%` : ""}`;
  else if (st.state === "failed") { state = "Stopped by a problem."; detail = `${st.error || ""} Press the button again to carry on.`; }
  else if (st.state === "stopped") state = "Stopped.";
  else if (book.files.m4b) { state = "The audiobook is ready."; detail = "Listen in step 5. Change anything and record again: only what changed is redone."; }
  else if (!book.castDone) { state = "Ready when you are."; detail = "Check the cast first (step 1), or make the whole audiobook in one go."; }
  else { state = "Cast and ready to record."; detail = "Check the script and sounds, then record."; }
  $("runState").textContent = state; $("runDetail").textContent = detail;
  $("makeAll").textContent = book.files.m4b ? "Make it again" : "Make the audiobook";
}
async function startJob(kind, extra = {}) {
  const r = await api(`/api/projects/${book.id}/job`, { json: { kind, ...extra } });
  if (!r.ok) { $("runState").textContent = "Couldn't start."; $("runDetail").textContent = r.error || ""; return false; }
  renderRun({ state: "queued", job: kind, progress: 0, detail: "Waiting its turn" });
  startPolling();
  return true;
}
$("makeAll").onclick = async () => {
  if (edits.size) await saveScript();
  if (tab === "cast") await saveCast(true);
  if (tab === "sounds") await saveSounds(true);
  startJob("all");
};
$("stopJob").onclick = async () => { await api(`/api/projects/${book.id}/stop`, { method: "POST" }); };
function stopPolling() { if (poll) clearInterval(poll); poll = null; }
function startPolling() {
  stopPolling();
  const id = book.id;
  poll = setInterval(async () => {
    if (!book || book.id !== id) return stopPolling();
    const r = await api(`/api/projects/${id}/status`);
    if (!r.ok) return;
    book.files = r.files;
    renderRun(r.status);
    if (tab === "record") updateChapters();
    const st = r.status.state;
    if (!["running", "queued"].includes(st)) {
      stopPolling();
      if (!edits.size) openBook(id, tab);          // fresh cast, script, sounds and files
    }
    lastState = st;
  }, 2500);
}

// ───────────────────────── 1 · cast ─────────────────────────
function voiceOptions(gender, current) {
  const base = (current || "").replace(/\^[\d.]+$/, "");
  const list = voices.filter((v) => !gender || v.gender === gender || v.id === base);
  return list.map((v) => el("option", { value: v.id, selected: v.id === base,
    text: `${v.name} · ${v.accent ? v.accent + ", " : ""}${v.note}${v.own ? " (your clip)" : ""}` }));
}
function pitchSelect(current) {
  const m = /(\^[\d.]+)$/.exec(current || "");
  const cur = m ? m[1] : "";
  const opts = PITCH.map(([v, t]) => el("option", { value: v, text: t, selected: v === cur }));
  if (cur && !PITCH.some(([v]) => v === cur)) opts.push(el("option", { value: cur, text: `Custom ${cur.slice(1)}`, selected: true }));
  return el("select", { class: "pitch", "aria-label": "Pitch" }, opts);
}
function voiceOf(row) {
  const v = row.querySelector(".v").value, p = row.querySelector(".pitch");
  return v.startsWith("kokoro:") ? v + (p.value || "") : v;
}
function playButton(getUrl, label) {
  const b = el("button", { type: "button", class: "play", "aria-label": label, title: label, text: "▶" });
  b.addEventListener("click", () => playUrl(getUrl(), b));
  return b;
}
let playing = null;
function playUrl(url, btn, row) {
  const pl = $("player");
  if (playing && playing.btn === btn && !pl.paused) { pl.pause(); return; }
  document.querySelectorAll(".play.on").forEach((b) => { b.classList.remove("on"); b.textContent = "▶"; });
  document.querySelectorAll(".line.playing").forEach((l) => l.classList.remove("playing"));
  btn.classList.add("on"); btn.textContent = "…";
  if (row) row.classList.add("playing");
  playing = { btn, row };
  pl.src = url;
  pl.play().then(() => { btn.textContent = "❚❚"; }).catch(() => { btn.classList.remove("on"); btn.textContent = "▶"; });
}
$("player").addEventListener("ended", () => { if (playing) { playing.btn.classList.remove("on"); playing.btn.textContent = "▶"; playing.row && playing.row.classList.remove("playing"); } });
$("player").addEventListener("pause", () => { if (playing) { playing.btn.classList.remove("on"); playing.btn.textContent = "▶"; } });
$("player").addEventListener("error", () => { if (playing) { playing.btn.classList.remove("on"); playing.btn.textContent = "!"; playing.btn.title = "That couldn't be played"; } });
const sampleUrl = (v) => `/api/voices/sample?voice=${encodeURIComponent(v)}&engine=${book.engine || "kokoro"}`;

function renderCast() {
  $("findCast").hidden = book.castDone;
  $("recast").hidden = !book.castDone;
  $("saveCast").parentElement.hidden = !book.castDone && !book.cast.length;
  const list = $("castList");
  list.replaceChildren();
  if (!book.castDone && !book.cast.length) {
    list.append(el("p", { class: "empty", text: "No cast yet. “Find who speaks” reads the book and lists every speaking part, with their gender and age." }));
    return;
  }
  // the narrator
  const nar = el("div", { class: "part narrator", "data-narrator": "1" },
    el("div", { class: "name" }, "Narrator", el("small", { text: `${book.narratorLines} lines · reads everything outside quotes` })),
    el("span"), el("span"),
    el("div", { class: "voice" }, el("select", { class: "v", "aria-label": "Narrator's voice" }, voiceOptions("", book.narrator)), pitchSelect(book.narrator)),
    el("span"));
  nar.querySelector(".voice").append(playButton(() => sampleUrl(voiceOf(nar)), "Listen to the narrator's voice"));
  list.append(nar);
  const cast = [...book.cast].sort((a, b) => (b.lines || 0) - (a.lines || 0));
  cast.forEach((c, i) => {
    const gid = `g${i}`;
    const row = el("div", { class: `part ${c.gender === "female" ? "she" : "he"}${c.review && !c.locked ? " review" : ""}`, "data-name": c.name },
      el("div", { class: "name" },
        el("input", { type: "text", class: "rename", value: c.name, maxlength: "60", "aria-label": "Name" }),
        el("small", {}, `${c.lines || 0} line${c.lines === 1 ? "" : "s"}`, c.tagged ? " · tagged in StoryMaker" : "",
          c.review && !c.locked ? el("span", { class: "flag", text: c.why ? ` · ${c.why}` : " · added by the AI, check it" }) : "")),
      el("div", { class: "seg", role: "radiogroup", "aria-label": `${c.name}: woman or man` },
        el("label", { class: "f" }, el("input", { type: "radio", name: gid, value: "female", checked: c.gender === "female" }), c.age === "child" || c.age === "teen" ? "Girl" : "Woman"),
        el("label", { class: "m" }, el("input", { type: "radio", name: gid, value: "male", checked: c.gender === "male" }), c.age === "child" || c.age === "teen" ? "Boy" : "Man")),
      el("select", { class: "age", "aria-label": `${c.name}: age` }, AGES.map(([v, t]) => el("option", { value: v, text: t, selected: (c.age || "adult") === v }))),
      el("div", { class: "voice" }, el("select", { class: "v", "aria-label": `${c.name}: voice` }, voiceOptions(c.gender, c.voice)), pitchSelect(c.voice)),
      el("label", { class: "lock" }, el("input", { type: "checkbox", class: "locked", checked: !!c.locked }), "Locked"));
    row.querySelector(".voice").append(playButton(() => sampleUrl(voiceOf(row)), `Listen to ${c.name}'s voice`));
    const refresh = () => {
      const g = row.querySelector(`input[name=${gid}]:checked`).value;
      const kid = ["child", "teen"].includes(row.querySelector(".age").value);
      row.className = `part ${g === "female" ? "she" : "he"}`;
      row.querySelector(".f").lastChild.textContent = kid ? "Girl" : "Woman";
      row.querySelector(".m").lastChild.textContent = kid ? "Boy" : "Man";
      const vs = row.querySelector(".v"), cur = voices.find((v) => v.id === vs.value);
      if (!cur || cur.gender !== g) {           // a voice of the other gender doesn't fit any more
        const taken = new Set([...list.querySelectorAll(".v")].map((s) => s.value));
        const pick = voices.find((v) => v.gender === g && !taken.has(v.id)) || voices.find((v) => v.gender === g);
        vs.replaceChildren(...voiceOptions(g, pick ? pick.id : ""));
      } else vs.replaceChildren(...voiceOptions(g, vs.value));
      const p = row.querySelector(".pitch");
      if (kid && !p.value) p.value = "^1.08";
      if (row.querySelector(".age").value === "elder" && !p.value) p.value = "^0.94";
      row.querySelector(".locked").checked = true;   // you've checked it: lock it
      $("castSaved").textContent = "";
    };
    row.querySelectorAll(`input[name=${gid}]`).forEach((r) => r.addEventListener("change", refresh));
    row.querySelector(".age").addEventListener("change", refresh);
    row.querySelector(".v").addEventListener("change", () => { row.querySelector(".pitch").disabled = !row.querySelector(".v").value.startsWith("kokoro:"); });
    row.querySelector(".pitch").disabled = !(c.voice || "").startsWith("kokoro:");
    list.append(row);
  });
}
async function saveCast(quiet) {
  if (!book.cast.length && !book.castDone) return;
  const list = $("castList");
  const nar = list.querySelector("[data-narrator]");
  const cast = [...list.querySelectorAll("[data-name]")].map((row) => ({
    name: row.dataset.name, rename: row.querySelector(".rename").value.trim(),
    gender: row.querySelector(".seg input:checked").value, age: row.querySelector(".age").value,
    voice: voiceOf(row), locked: row.querySelector(".locked").checked }));
  const r = await api(`/api/projects/${book.id}/cast`, { json: { cast, narrator: nar ? voiceOf(nar) : null } });
  if (!r.ok) { $("castSaved").textContent = r.error || "Not saved."; return; }
  if (!quiet) {
    $("castSaved").textContent = book.files.m4b ? "Saved. Record again to hear it (only the changed voices are redone)." : "Saved.";
    await openBook(book.id, "cast");
  }
}
$("saveCast").onclick = () => saveCast(false);
$("findCast").onclick = () => startJob("cast");
$("recast").onclick = () => {
  if (confirm("Read the whole book again and redo who says each line? Locked characters keep their gender and age; your own changes to lines are replaced.")) startJob("cast", { redo: true });
};

// ───────────────────────── 2 · script ─────────────────────────
function speakerSelect(cur) {
  const names = ["Narrator", ...book.cast.map((c) => c.name)];
  if (cur && !names.includes(cur)) names.push(cur);
  return el("select", { class: "who-sel", "aria-label": "Who says this" }, names.map((n) => el("option", { value: n, text: n, selected: n === (cur || "Narrator") })));
}
function whoClass(name) {
  if (!name || name === "Narrator") return "n";
  const c = book.cast.find((x) => x.name === name);
  return c ? (c.gender === "female" ? "she" : "he") : "";
}
function renderScript() {
  const pick = $("chapterPick");
  pick.replaceChildren(...book.chapters.map((ch, i) => {
    const n = ch.sections.reduce((a, s) => a + s.lines.filter((l) => l.check).length, 0);
    return el("option", { value: i, text: `${ch.title}${n ? ` · ${n} to check` : ""}`, selected: i === chapterIx });
  }));
  const list = $("script");
  list.replaceChildren();
  $("saveScript").parentElement.hidden = !book.castDone;
  if (!book.castDone) { list.append(el("li", { class: "empty", text: "The script is ready once the cast is found (step 1)." })); return; }
  const onlyCheck = $("onlyCheck").checked, onlyTalk = $("onlyTalk").checked;
  const ch = book.chapters[chapterIx] || book.chapters[0];
  let shown = 0;
  ch.sections.forEach((sec, si) => {
    if (ch.sections.length > 1) list.append(el("li", { class: "section-rule", text: `Scene ${si + 1}` }));
    for (const ln of sec.lines) {
      if (onlyCheck && !ln.check) continue;
      if (onlyTalk && ln.kind !== "dialogue") continue;
      shown++;
      const key = `${sec.id}|${ln.id}`, ed = edits.get(key) || {};
      const speaker = ed.speaker || ln.speaker || "Narrator";
      const li = el("li", { class: `line ${ln.kind === "dialogue" ? "talk" : "nar"}${ln.check ? " check" : ""}${edits.has(key) ? " changed" : ""}` });
      const who = speakerSelect(speaker);
      who.classList.add("who", whoClass(speaker));
      const text = el("div", { class: "text", contenteditable: "plaintext-only", spellcheck: "false", text: ed.text || ln.text });
      if (text.contentEditable !== "plaintext-only") text.contentEditable = "true";
      const emo = el("select", { class: "emo", "aria-label": "How it's said" }, me.emotions.map((e) => el("option", { value: e, text: e, selected: e === (ed.emotion || ln.emotion) })));
      const change = (field, value) => {
        const e = edits.get(key) || { section: sec.id, id: ln.id };
        e[field] = value; edits.set(key, e);
        li.classList.add("changed"); $("saveScript").disabled = false; $("scriptSaved").textContent = "";
      };
      who.addEventListener("change", () => { change("speaker", who.value); who.className = `who-sel who ${whoClass(who.value)}`; });
      emo.addEventListener("change", () => change("emotion", emo.value));
      text.addEventListener("input", () => change("text", text.textContent));
      const play = el("button", { type: "button", class: "play", "aria-label": "Listen to this line", title: "Listen", text: "▶" });
      play.addEventListener("click", async () => {
        if (edits.size) { play.textContent = "…"; await saveScript(true); }
        playUrl(`/api/projects/${book.id}/preview?section=${sec.id}&line=${ln.id}&t=${Date.now()}`, play, li);
      });
      li.append(who, text, emo, play);
      if (ln.check) li.append(el("p", { class: "note", text: ln.check }));
      else if (ln.fixed) li.append(el("p", { class: "note", text: "Matched to the speech tag in the text." }));
      list.append(li);
    }
  });
  if (!shown) list.append(el("li", { class: "empty", text: onlyCheck ? "Nothing to check in this chapter." : "No lines here." }));
  $("saveScript").disabled = !edits.size;
}
$("chapterPick").addEventListener("change", () => { chapterIx = Number($("chapterPick").value); renderScript(); });
$("onlyCheck").addEventListener("change", renderScript);
$("onlyTalk").addEventListener("change", renderScript);
async function saveScript(quiet) {
  if (!edits.size) return;
  const changes = [...edits.values()].map((e) => ({ ...e, text: e.text !== undefined ? e.text.replace(/\s+/g, " ").trim() : undefined }));
  const r = await api(`/api/projects/${book.id}/script`, { json: { changes } });
  if (!r.ok) { $("scriptSaved").textContent = r.error || "Not saved."; return; }
  edits.clear();
  const fresh = await api(`/api/projects/${book.id}`);
  if (fresh.ok) { book = fresh; }
  $("saveScript").disabled = true;
  if (!quiet) { renderScript(); $("scriptSaved").textContent = "Saved."; }
}
$("saveScript").onclick = () => saveScript(false);
window.addEventListener("beforeunload", (e) => { if (edits.size) { e.preventDefault(); e.returnValue = ""; } });

// ───────────────────────── 3 · sounds ─────────────────────────
function fxRow(fx = {}) {
  const row = el("div", { class: "fx-row" },
    el("input", { type: "text", class: "fx-s", value: fx.sound || "", placeholder: "door knock", maxlength: "60", "aria-label": "Sound" }),
    el("input", { type: "text", class: "fx-at", value: fx.at || "", placeholder: "when: words from the text", maxlength: "120", "aria-label": "When" }),
    el("button", { type: "button", class: "small-btn warn", text: "Remove", onclick: () => row.remove() }));
  return row;
}
function renderSounds() {
  $("wishes").value = book.soundWishes || "";
  const box = $("soundList");
  box.replaceChildren();
  book.chapters.forEach((ch) => ch.sections.forEach((sec, si) => {
    const words = sec.lines.map((l) => l.text).join(" ");
    const snd = sec.sound || { ambience: "", effects: [] };
    const fx = el("div", { class: "fx" }, (snd.effects || []).map(fxRow));
    box.append(el("div", { class: "sound-scene", "data-id": sec.id },
      el("div", { class: "scene-name" }, ch.title, ch.sections.length > 1 ? el("small", { text: ` · scene ${si + 1}` }) : ""),
      el("p", { class: "excerpt", text: words.slice(0, 170) + (words.length > 170 ? "…" : "") }),
      el("input", { type: "text", class: "amb", value: snd.ambience === "none" ? "" : snd.ambience || "", maxlength: "80",
        placeholder: "background, e.g. waves on a harbor (blank for none)", "aria-label": "Background" }),
      fx, el("button", { type: "button", class: "small-btn fx-add", text: "Add an effect", onclick: () => fx.append(fxRow()) })));
  }));
}
async function saveSounds(quiet) {
  const sounds = {};
  document.querySelectorAll(".sound-scene").forEach((s) => {
    sounds[s.dataset.id] = { ambience: s.querySelector(".amb").value.trim() || "none",
      effects: [...s.querySelectorAll(".fx-row")].map((r) => ({ sound: r.querySelector(".fx-s").value.trim(), at: r.querySelector(".fx-at").value.trim() })).filter((f) => f.sound) };
  });
  const r = await api(`/api/projects/${book.id}/sounds`, { json: { sounds, wishes: $("wishes").value.trim() } });
  if (!quiet) $("soundsSaved").textContent = r.ok ? "Saved." : r.error || "Not saved.";
  return r.ok;
}
$("saveSounds").onclick = () => saveSounds(false);
$("designSounds").onclick = async () => {
  if (!confirm("Let the AI choose every scene's sounds again? Your sound wishes are kept.")) return;
  await api(`/api/projects/${book.id}/sounds`, { json: { sounds: {}, wishes: $("wishes").value.trim() } });
  startJob("sounds", { redo: true });
};

// ───────────────────────── 4 · record ─────────────────────────
function renderRecord() {
  const cb = document.querySelector("input[name=engine][value=chatterbox]");
  cb.disabled = !me.chatterbox;
  $("cbMissing").hidden = me.chatterbox;
  const eng = book.engine === "chatterbox" && !me.chatterbox ? "kokoro" : book.engine || me.engine;
  document.querySelectorAll("input[name=engine]").forEach((r) => { r.checked = r.value === eng; });
  $("recordAll").textContent = book.files.chapters.some((c) => c.audio) ? "Record what's changed" : "Record the whole book";
  const list = $("chapterList");
  list.replaceChildren(...book.chapters.map((ch, i) => el("li", { class: "chapter-row", "data-i": i },
    el("div", { class: "t", text: ch.title }, el("small", { text: `${ch.sections.reduce((a, s) => a + s.words, 0).toLocaleString()} words` })),
    el("div", { class: "slot" }),
    el("button", { type: "button", class: "small-btn", text: "Record again", onclick: () => startJob("record", { chapters: [i] }) }))));
  updateChapters();
}
function updateChapters() {
  document.querySelectorAll(".chapter-row").forEach((row) => {
    const c = book.files.chapters[Number(row.dataset.i)];
    const slot = row.querySelector(".slot");
    const key = c && c.audio ? `${c.audio}?${c.stamp}` : "";
    if (slot.dataset.key === key) return;
    slot.dataset.key = key;
    slot.replaceChildren(...(key ? [el("audio", { controls: true, preload: "none", src: key }),
      el("a", { class: "dl", href: `${c.audio}?download=1`, text: "Download", "aria-label": "Download this chapter" })]
      : [el("span", { class: "waiting", text: "Not recorded yet" })]));
  });
}
document.querySelectorAll("input[name=engine]").forEach((r) => r.addEventListener("change", async () => {
  await api(`/api/projects/${book.id}/settings`, { json: { engine: r.value } });
  book.engine = r.value;
}));
$("recordAll").onclick = () => startJob("record");

// ───────────────────────── 5 · listen ─────────────────────────
function renderListen() {
  const f = book.files, box = $("listen");
  box.replaceChildren();
  const art = f.cover ? el("img", { src: f.cover, alt: "" }) : el("div", { class: "noart", text: book.title[0] });
  const side = el("div", {}, el("h3", { text: book.title }));
  if (f.mp3) {
    const all = f.everything && el("a", { class: "go everything", href: f.everything, text: "Download everything" });
    if (all) all.addEventListener("click", () => {
      all.textContent = "Packing it up…";
      setTimeout(() => { all.textContent = "Download everything"; }, 15000);
    });
    side.append(el("p", { class: "sub", text: `${book.chapters.length} chapters, mastered for listening` }),
      el("audio", { controls: true, preload: "metadata", src: `${f.mp3}?${Date.now()}` }),
      all || "",
      el("p", { class: "hint", text: "One zip: the audiobook, the MP3, every chapter, the book as a PDF, the cover and the sound credits." }),
      el("div", { class: "downloads" },
        f.m4b && el("a", { href: `${f.m4b}?download=1`, text: "Audiobook (M4B, with chapters)" }),
        el("a", { href: `${f.mp3}?download=1`, text: "MP3" }),
        el("a", { href: `${f.pdf}?download=1`, text: "Book (PDF)" }),
        f.credits && el("a", { href: f.credits, target: "_blank", text: "Sound credits" })),
      el("p", { class: "hint", text: "The M4B plays in Apple Books and most audiobook apps, with a chapter list and the cover." }));
    if (f.canSend) {
      const msg = el("p", { class: "saved", text: f.sent ? `Sent to StoryMaker ${new Date(f.sent).toLocaleString()}.` : "" });
      const send = el("button", { type: "button", class: "small-btn", text: f.sent ? "Send to StoryMaker again" : "Send to StoryMaker" });
      send.addEventListener("click", async () => {
        send.disabled = true; msg.className = "saved"; msg.textContent = "Sending…";
        const r = await api(`/api/projects/${book.id}/send-to-storymaker`, { method: "POST" });
        send.disabled = false;
        if (!r.ok) { msg.className = "error"; msg.textContent = r.error || "Not sent."; return; }
        msg.textContent = "Sent. The story in StoryMaker now plays and downloads this audiobook. Its earlier audio is kept as a backup."
          + (r.had_video ? " (The video keeps its own soundtrack.)" : "");
        send.textContent = "Send to StoryMaker again";
      });
      side.append(el("div", { class: "send-sm" },
        el("strong", { text: "StoryMaker" }),
        el("span", { class: "hint", text: "Replace the story's audio in your StoryMaker library with this audiobook." }),
        send, msg));
    }
    const list = el("ol", { class: "chapter-downloads" });
    f.chapters.forEach((c) => {
      if (!c.audio) return;
      list.append(el("li", {}, el("span", { text: c.title }),
        el("a", { href: `${c.audio}?download=1`, text: "Download", "aria-label": `Download ${c.title}` })));
    });
    if (list.children.length) side.append(el("h4", { class: "chapters-h", text: "Chapters" }), list);
  } else {
    side.append(el("p", { class: "empty", text: "Not recorded yet. Press “Make the audiobook” at the top." }),
      el("div", { class: "downloads" }, el("a", { href: `${f.pdf}?download=1`, text: "Book (PDF)" })));
  }
  box.append(art, side);
}

// ───────────────────────── delete ─────────────────────────
$("deleteBook").onclick = () => { $("deleteConfirm").hidden = false; $("deleteBook").hidden = true; };
$("deleteNo").onclick = () => { $("deleteConfirm").hidden = true; $("deleteBook").hidden = false; };
$("deleteYes").onclick = async () => {
  const r = await api(`/api/projects/${book.id}`, { method: "DELETE" });
  $("deleteConfirm").hidden = true; $("deleteBook").hidden = false;
  if (r.ok) location.hash = ""; else alert(r.error);
};

// ───────────────────────── voice library ─────────────────────────
function renderLib() {
  const eng = $("libEngine").value;
  $("libList").replaceChildren(...voices.map((v) => el("div", { class: "lib-row" },
    el("div", { class: "t" }, el("strong", { text: v.name }), el("small", { text: ` ${v.gender === "female" ? "woman" : "man"}${v.accent ? ", " + v.accent : ""} · ${v.note}` })),
    playButton(() => `/api/voices/sample?voice=${encodeURIComponent(v.id)}&engine=${eng}`, `Listen to ${v.name}`))));
}
$("openLib").onclick = () => {
  $("libEngine").value = book && book.engine === "chatterbox" && me.chatterbox ? "chatterbox" : "kokoro";
  $("libEngine").querySelector("[value=chatterbox]").disabled = !me.chatterbox;
  renderLib(); $("voiceLib").showModal();
};
$("libEngine").addEventListener("change", renderLib);
$("voiceLib").addEventListener("close", () => $("player").pause());
$("uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("file", $("clipFile").files[0]); fd.append("name", $("clipName").value); fd.append("gender", $("clipGender").value);
  $("clipGo").disabled = true; $("clipMsg").textContent = "Adding…";
  const r = await api("/api/voices/upload", { method: "POST", body: fd });
  $("clipGo").disabled = false;
  $("clipMsg").textContent = r.ok ? "Added. Pick it for anyone in the cast." : r.error || "Not added.";
  if (r.ok) { voices = (await api("/api/voices")).voices || voices; renderLib(); $("uploadForm").reset(); if (book && tab === "cast") renderCast(); }
});
