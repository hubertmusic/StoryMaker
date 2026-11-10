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
const JOBS = { cast: "Finding who speaks", sounds: "Choosing the sounds", record: "Recording", all: "Making the audiobook",
  tidy: "Cleaning up the writing", video: "Making the pictures and video" };
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
// settings: the grown-up switch (the same one as StoryMaker's)
function openSettings() {
  const owner = me.role === "owner";
  $("grownupBlock").hidden = !owner; $("grownupMember").hidden = owner;
  $("grownupOn").checked = !!me.adultSwitch;
  $("grownupPwRow").hidden = $("grownupSave").hidden = true; $("grownupMsg").hidden = true; $("grownupPw").value = "";
  $("settingsDlg").showModal();
  showDrawThings();
}
// Draw Things: is its API server on? (the studio keeps it on)
function drawThingsText(d) {
  if (d.up) return "On: StoryMaker and the studio can draw pictures.";
  if (!d.open) return "Off: Draw Things is closed." + (d.watching ? " The studio will open it within a minute." : "");
  return "Off: Draw Things is open but its API Server switch is off." + (d.detail ? ` (${d.detail})` : "");
}
async function showDrawThings(d) {
  d = d || await api("/api/drawthings");
  if (!d || d.here === undefined) return;
  $("dtBlock").hidden = !d.here;
  $("dtState").textContent = drawThingsText(d);
  $("dtState").className = "dt-state " + (d.up ? "on" : "off");
  $("dtAllow").hidden = !!d.allowed;
  $("dtFix").hidden = !!d.up;
}
$("dtFix").onclick = async () => {
  $("dtFix").disabled = true; $("dtFix").textContent = "Turning it on…";
  const r = await api("/api/drawthings/fix", { method: "POST" });
  $("dtFix").disabled = false; $("dtFix").textContent = "Turn it on now";
  if (r && r.here !== undefined) showDrawThings(r);
  if (r && !r.ok && r.detail) $("dtState").textContent = "Still off: " + r.detail;
};
async function setGrownup(on) {
  const r = await api("/api/grownup", { json: { on, current: $("grownupPw").value } });
  if (!r.ok) { $("grownupMsg").textContent = r.error || "That didn't work."; $("grownupMsg").hidden = false; $("grownupOn").checked = !on; return; }
  location.hash = ""; location.reload();             // the library and levels follow straight away
}
$("settingsBtn").onclick = openSettings;
$("grownupOn").addEventListener("change", () => {
  $("grownupMsg").hidden = true;
  if (!$("grownupOn").checked) { setGrownup(false); return; }          // hiding: right away, no password
  $("grownupPwRow").hidden = $("grownupSave").hidden = false;          // showing: password first
  $("grownupPw").focus();
});
$("grownupSave").onclick = () => setGrownup(true);
$("grownupPw").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); setGrownup(true); } });
$("settingsDlg").addEventListener("close", () => { if (!$("grownupSave").hidden) $("grownupOn").checked = !!me.adultSwitch; });
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
  loadNasBooks();
  const books = $("books");
  books.replaceChildren();
  if (!(p.projects || []).length) books.append(el("p", { class: "empty", text: "Nothing here yet. Bring in a story from StoryMaker or a book file, and it will show up here." }));
  for (const b of p.projects || []) {
    const pill = b.state === "running" || b.state === "queued" ? el("span", { class: "pill live", text: b.state === "queued" ? "Waiting" : "Working" })
      : b.state === "paused" ? el("span", { class: "pill paused", text: "Paused" })
      : b.has_book ? el("span", { class: "pill done", text: "Audiobook ready" })
      : b.state === "failed" ? el("span", { class: "pill bad", text: "Stopped by a problem" })
      : el("span", { class: "pill", text: b.cast_done ? "Cast" : "New" });
    const row = el("button", { type: "button", class: "book-row", onclick: () => { location.hash = `b=${b.id}`; } },
      b.has_cover ? el("img", { src: `/api/projects/${b.id}/file/cover.jpg`, alt: "" }) : el("span", { class: "noart", text: (b.title || "?")[0] }),
      el("span", {}, el("div", { class: "t", text: b.title }),
        el("div", { class: "m", text: `${b.chapters} chapter${b.chapters === 1 ? "" : "s"} · ${Number(b.words || 0).toLocaleString()} words · ${LEVEL_NAMES[b.level] || b.level}` })),
      pill);
    if (b.state === "running" || b.state === "queued") {
      const pause = el("button", { type: "button", class: "quiet", text: "Pause", "aria-label": `Pause ${b.title}` });
      pause.onclick = async () => {
        pause.disabled = true; pause.textContent = "Pausing…";
        await api(`/api/projects/${b.id}/pause`, { method: "POST" });
        setTimeout(showHome, 2500);
      };
      const stop = el("button", { type: "button", class: "quiet stop-row", text: "Stop", "aria-label": `Stop ${b.title}` });
      stop.onclick = async () => {
        if (!confirm(`Stop working on “${b.title}”? What's recorded so far is kept: start again to carry on.`)) return;
        stop.disabled = true; stop.textContent = "Stopping…";
        await api(`/api/projects/${b.id}/stop`, { method: "POST" });
        setTimeout(showHome, 2500);
      };
      books.append(el("div", { class: "book-line two" }, row, pause, stop));
    } else if (b.state === "paused") {
      const resume = el("button", { type: "button", class: "quiet resume-row", text: "Resume", "aria-label": `Resume ${b.title}` });
      resume.onclick = async () => {
        resume.disabled = true; resume.textContent = "Resuming…";
        const r = await api(`/api/projects/${b.id}/resume`, { method: "POST" });
        if (!r.ok) { resume.disabled = false; resume.textContent = "Resume"; alert(r.error || "Couldn't resume."); return; }
        setTimeout(showHome, 1200);
      };
      books.append(el("div", { class: "book-line" }, row, resume));
    } else books.append(row);
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
  $("srcFree").hidden = v !== "free"; $("srcNas").hidden = v !== "nas";
  $("titleRow").hidden = v === "sm" || v === "free" || v === "nas";
  $("levelRow").hidden = v === "sm" || v === "nas";
  // StoryMaker stories were proofread when they were written; a book or a PDF is cleaned up unless you say not to
  $("cleanUp").checked = v !== "sm" && v !== "nas";
  if (v === "nas") loadNasStories();
}));
$("cleanUp").checked = document.querySelector("input[name=src]:checked").value !== "sm";

// free books: Project Gutenberg, found by title, author or a description (the AI works out which book you mean)
let pickedBook = null;
async function searchBooks() {
  const q = $("bookQuery").value.trim();
  const box = $("bookResults");
  if (!q) return;
  pickedBook = null;
  box.replaceChildren(el("p", { class: "hint", text: "Looking…" }));
  $("bookSearch").disabled = true;
  const r = await api(`/api/books/search?q=${encodeURIComponent(q)}`);
  $("bookSearch").disabled = false;
  box.replaceChildren();
  if (!r.ok) { box.append(el("p", { class: "error", text: r.error || "The search didn't work." })); return; }
  if (!r.results.length && !r.not_free.length) box.append(el("p", { class: "hint", text: "Nothing found. Try the title, the author, or describe the book." }));
  for (const b of r.results) {
    const input = el("input", { type: "radio", name: "freeBook", value: String(b.id), disabled: !b.readable });
    input.addEventListener("change", () => { pickedBook = b; });
    box.append(el("label", { class: `book-hit${b.readable ? "" : " off"}` }, input,
      el("span", { class: "bh-main" },
        el("span", { class: "bh-title", text: b.title }),
        el("span", { class: "bh-meta", text: [b.authors, b.years ? `(${b.years})` : "", b.readable ? "" : "no text version"].filter(Boolean).join(" ") }),
        b.note ? el("span", { class: "bh-note", text: b.note }) : "")));
  }
  for (const b of r.not_free) {
    box.append(el("div", { class: "book-hit off" }, el("span", { class: "bh-main" },
      el("span", { class: "bh-title", text: `${b.title}${b.author ? `, ${b.author}` : ""}` }),
      el("span", { class: "bh-note", text: b.note }))));
  }
  const first = box.querySelector("input[name=freeBook]:not([disabled])");
  if (first) { first.checked = true; pickedBook = r.results.find((b) => String(b.id) === first.value); }
}
$("bookSearch").addEventListener("click", searchBooks);
$("bookQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchBooks(); } });
function showError(msg) { $("importErr").textContent = msg || "That didn't work."; $("importErr").hidden = false; }
$("importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("importErr").hidden = true;
  const src = document.querySelector("input[name=src]:checked").value;
  const level = (document.querySelector("input[name=level]:checked") || {}).value || "family";
  let r;
  $("importGo").disabled = true; $("importGo").textContent = "Bringing it in…";
  const clean = $("cleanUp").checked;
  if (src === "sm") {
    if (!$("smStory").value) { showError("Pick a story first."); r = null; }
    else r = await api("/api/import/storymaker", { json: { id: $("smStory").value, clean } });
  } else if (src === "nas") {
    const id = $("nasStory").value;
    if (!id) { showError("Pick a story on the NAS first."); r = null; }
    else {
      $("importGo").textContent = "Copying it back from the NAS…";
      const back = await api("/api/nas/storymaker/restore", { json: { id } });
      if (!back.ok) r = back;
      else {
        const st = await waitNas(back.task, (t) => { $("importGo").textContent = `Copying it back from the NAS… ${t.progress || 0}%`; });
        if (st.state !== "done") r = { ok: false, error: (st.failed && st.failed[0] && st.failed[0].error) || "It couldn't be copied back." };
        else { $("importGo").textContent = "Bringing it in…"; r = await api("/api/import/storymaker", { json: { id, clean } }); }
      }
    }
  } else if (src === "free") {
    if (!pickedBook) { showError("Search for a book and pick one first."); r = null; }
    else { $("importGo").textContent = "Downloading the book…"; r = await api("/api/import/gutenberg", { json: { id: pickedBook.id, level, clean } }); }
  } else if (src === "file") {
    const f = $("bookFile").files[0];
    if (!f) { showError("Choose a book file first."); r = null; }
    else {
      const fd = new FormData();
      fd.append("file", f); fd.append("title", $("bookTitle").value); fd.append("level", level); fd.append("clean", clean ? "1" : "");
      r = await api("/api/import/file", { method: "POST", body: fd });
    }
  } else {
    if (!$("pasteText").value.trim()) { showError("Paste some text first."); r = null; }
    else r = await api("/api/import/text", { json: { title: $("bookTitle").value, text: $("pasteText").value, level, clean } });
  }
  $("importGo").disabled = false; $("importGo").textContent = "Bring it in";
  if (!r) return;
  if (!r.ok) return showError(r.error);
  location.hash = `b=${r.id}`;
});

// ───────────────────────── the NAS ─────────────────────────
const gb = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round((n || 0) / 1e6))} MB`);
const day = (s) => { const d = new Date(s || ""); return isNaN(d) ? "" : d.toLocaleDateString(); };
function waitNas(tid, onTick) {
  return new Promise((resolve) => {
    const t = setInterval(async () => {
      const st = await api(`/api/nas/task/${tid}`);
      if (!st.ok && !st.state) { clearInterval(t); resolve({ state: "failed", failed: [{ error: st.error }] }); return; }
      if (onTick) onTick(st);
      if (["done", "failed", "partly"].includes(st.state)) { clearInterval(t); resolve(st); }
    }, 1500);
  });
}
async function loadNasStories() {
  const sel = $("nasStory");
  const r = await api("/api/nas/storymaker");
  sel.replaceChildren();
  const away = (r.items || []).filter((it) => !it.onComputer);
  if (!r.ok) sel.append(el("option", { value: "", text: r.error || "Can't reach the NAS right now" }));
  else if (!away.length) sel.append(el("option", { value: "", text: "No StoryMaker stories on the NAS" }));
  else for (const it of away) sel.append(el("option", { value: it.id, text: `${it.title} · ${gb(it.size)}` }));
}
// Audiobooks | Archive (the books on the NAS, as a plain list)
let shelfTab = "books";
try { if (localStorage.getItem("shelfTab") === "archive") shelfTab = "archive"; } catch { /* private window */ }
let nasReady = false;
function setShelfTab(t) {
  shelfTab = t === "archive" && nasReady ? "archive" : "books";
  try { localStorage.setItem("shelfTab", shelfTab); } catch { /* private window */ }
  const arch = shelfTab === "archive";
  $("tabBooks").setAttribute("aria-selected", String(!arch)); $("tabArchive").setAttribute("aria-selected", String(arch));
  $("books").hidden = arch; $("nasBooks").hidden = !arch;
}
$("tabBooks").onclick = () => setShelfTab("books");
$("tabArchive").onclick = () => setShelfTab("archive");
async function loadNasBooks() {
  const r = await api("/api/nas");
  nasReady = r.configured !== false;
  $("srcNasChoice").hidden = !nasReady;
  $("nasMoveBtn").hidden = !nasReady;
  $("libTabs").hidden = !nasReady;
  const list = $("nasBookList");
  if (!r.ok) {
    setShelfTab(shelfTab);
    list.replaceChildren(); $("nasBooksEmpty").hidden = true;
    $("nasBooksHint").textContent = `${r.error || "Can't reach the NAS right now."} Books there are safe; they'll show again once it's back.`;
    return;
  }
  const away = (r.books || []).filter((b) => !b.on_computer);
  $("tabArchive").textContent = away.length ? `Archive (${away.length})` : "Archive";
  $("nasBooksEmpty").hidden = away.length > 0;
  setShelfTab(shelfTab);
  list.replaceChildren(...away.map((b) => {
    const msg = el("p", { class: "hint" });
    const back = el("button", { type: "button", class: "quiet", text: "Bring back" });
    const row = el("div", { class: "nas-book" },
      el("span", { class: "nb-t" }, el("strong", { text: b.title }),
        el("span", { class: "hint", text: `${gb(b.size)} · moved ${day(b.archived)} · ${LEVEL_NAMES[b.level] || b.level || ""}` })),
      back, msg);
    back.onclick = async () => {
      back.disabled = true; back.textContent = "Bringing it back…";
      const res = await api("/api/nas/restore", { json: { ids: [b.id] } });
      if (!res.ok) { back.disabled = false; back.textContent = "Bring back"; msg.textContent = res.error; return; }
      const st = await waitNas(res.task, (t) => { back.textContent = `Bringing it back… ${t.progress || 0}%`; });
      if (st.state === "done") showHome();
      else { back.disabled = false; back.textContent = "Bring back"; msg.textContent = (st.failed[0] || {}).error || "It couldn't be brought back."; }
    };
    return row;
  }));
}
$("nasMoveBtn").onclick = async () => {
  const box = $("nasMove");
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(el("p", { class: "hint", text: "Checking the NAS…" }));
  const [nas, c] = await Promise.all([api("/api/nas"), api("/api/nas/candidates")]);
  if (!nas.ok) { box.replaceChildren(el("p", { class: "error", text: nas.error || "The NAS isn't reachable." })); return; }
  const list = c.books || [];
  const total = el("span", { class: "hint" });
  const keep = el("input", { type: "checkbox" });
  const go = el("button", { type: "button", class: "go", text: "Move to the NAS" });
  const msg = el("p", { class: "hint", role: "status" });
  const boxes = list.map((b) => el("input", { type: "checkbox", value: b.id, disabled: b.busy, "data-size": b.size }));
  const update = () => {
    const picked = boxes.filter((x) => x.checked);
    const size = picked.reduce((n, x) => n + Number(x.dataset.size || 0), 0);
    total.textContent = picked.length ? `${picked.length} picked, ${gb(size)}` : "";
    go.disabled = !picked.length;
    go.textContent = keep.checked ? "Back up to the NAS" : "Move to the NAS";
  };
  boxes.forEach((x) => x.addEventListener("change", update)); keep.addEventListener("change", update);
  box.replaceChildren(
    el("p", { class: "hint", text: `Frees space on this computer. Each book is copied to the NAS and checked file by file, and only then removed from here.${nas.free ? ` The NAS has ${gb(nas.free)} free.` : ""}` }),
    ...(list.length ? list.map((b, i) => el("label", { class: "check nas-pick" }, boxes[i], el("span", { text: b.title }),
      el("span", { class: "hint", text: b.busy ? "being worked on" : `${gb(b.size)}${b.has_book ? " · audiobook ready" : ""}` })))
      : [el("p", { text: "No books on this computer to move." })]),
    el("label", { class: "check" }, keep, " Keep a copy on this computer too (just back up)"),
    el("div", { class: "nas-go" }, go, total), msg);
  update();
  go.onclick = async () => {
    const picked = boxes.filter((x) => x.checked);
    const size = picked.reduce((n, x) => n + Number(x.dataset.size || 0), 0);
    const what = `${picked.length} book${picked.length === 1 ? "" : "s"} (${gb(size)})`;
    if (!confirm(keep.checked ? `Back up ${what} to the NAS? They stay here too.`
      : `Move ${what} to the NAS? Each one is removed from this computer once it's safely copied and checked.`)) return;
    go.disabled = true;
    const r = await api("/api/nas/archive", { json: { ids: picked.map((x) => x.value), keep: keep.checked } });
    if (!r.ok) { go.disabled = false; msg.textContent = r.error; return; }
    const st = await waitNas(r.task, (t) => { msg.textContent = `${t.progress || 0}% · ${t.detail || ""}`; });
    msg.textContent = `${(st.done || []).length} ${keep.checked ? "backed up" : "moved"}.` +
      ((st.failed || []).length ? ` ${(st.failed || []).length} couldn't be: ${st.failed.map((f) => `${f.label}: ${f.error}`).join(" ")}` : "");
    showHome();
  };
};

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
  $("archiveBook").hidden = !nasReady; $("archiveBook").disabled = false; $("archiveNote").textContent = "";
  if (!nasReady) api("/api/nas").then((n) => { nasReady = n.configured !== false; $("archiveBook").hidden = !nasReady; });
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
  const paused = st.state === "paused";
  $("makeAll").disabled = busy; $("makeAll").hidden = paused;
  $("stopJob").hidden = !busy; $("pauseJob").hidden = !busy; $("resumeJob").hidden = !paused;
  if (!busy) { $("stopJob").disabled = false; $("stopJob").textContent = "Stop"; $("pauseJob").disabled = false; $("pauseJob").textContent = "Pause"; }
  if (!paused) { $("resumeJob").disabled = false; $("resumeJob").textContent = "Resume"; }
  $("runMeterWrap").hidden = !busy;
  $("runMeter").style.width = `${st.progress || 0}%`;
  let state, detail = st.detail || "";
  if (busy) state = `${JOBS[st.job] || "Working"}… ${st.state === "running" ? `${st.progress || 0}%` : ""}`;
  else if (st.state === "failed") { state = "Stopped by a problem."; detail = `${st.error || ""} Press the button again to carry on.`; }
  else if (st.state === "stopped") state = "Stopped.";
  else if (paused) { state = `Paused: ${(JOBS[(st.resume || {}).kind] || "working").toLowerCase()}.`; detail = "Everything done so far is kept. Other books can use the studio now; press Resume to carry on from here."; }
  else if (book.files.m4b) { state = "The audiobook is ready."; detail = "Listen in step 5. Change anything and record again: only what changed is redone."; }
  else if (book.tidyPending) { state = "Ready when you are. Nothing has started yet."; detail = "Press Make the audiobook to pick the voices, sounds and music first. The writing is cleaned up before anything is recorded."; }
  else if (!book.castDone) { state = "Ready when you are. Nothing has started yet."; detail = "Press Make the audiobook to pick the voices, sounds and music first, or check the cast in step 1."; }
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
  openMakeChoices();
};
// The choices for this book, before anything starts: voices, narrator, clean-up, sounds, music, pictures.
function openMakeChoices() {
  const again = !!book.files.m4b;
  $("makeH").textContent = again ? "Make it again" : "Before it starts";
  $("makeIntro").textContent = again
    ? "Change anything you like. Only what changed is recorded again. Nothing starts until you press Start."
    : "Pick how this book is made. Nothing starts until you press Start.";
  const eng = book.engine === "chatterbox" && !me.chatterbox ? "kokoro" : book.engine || me.engine;
  document.querySelectorAll("input[name=mkEngine]").forEach((r) => {
    r.checked = r.value === eng; r.disabled = r.value === "chatterbox" && !me.chatterbox;
  });
  const narr = $("mkNarrator");
  narr.replaceChildren(el("option", { value: "", text: "Automatic", selected: !book.narrator }), ...voiceOptions("", book.narrator || ""));
  if (book.narrator && !narr.value) narr.value = "";
  const tidyDone = !!book.tidyResult;
  $("mkTidy").checked = !!book.tidyPending;
  $("mkTidyLabel").textContent = tidyDone ? "Clean up the spelling and sentences again" : "Clean up the spelling and sentences first";
  $("mkSoundsRow").hidden = !book.soundsAvailable;
  $("mkSounds").checked = (book.soundscapes || "on") === "on";
  $("mkMusic").checked = (book.music || "on") === "on";
  $("mkVideoRow").hidden = !book.files.pictures;
  $("mkVideo").checked = false;
  $("mkStyle").value = book.pictureStyle || "storybook";
  $("mkStyleRow").hidden = true;
  $("mkStart").disabled = false; $("mkStart").textContent = "Start";
  updateMakePlan();
  $("makeDlg").showModal();
}
function updateMakePlan() {
  const steps = [];
  if ($("mkTidy").checked) steps.push("Clean up the writing");
  if (!book.castDone) steps.push("Find the cast and give everyone a voice");
  if ($("mkSounds").checked && !$("mkSoundsRow").hidden && !book.soundsDone) steps.push("Choose the sounds");
  const eng = (document.querySelector("input[name=mkEngine]:checked") || {}).value || "kokoro";
  steps.push(`Record ${book.files.chapters.some((c) => c.audio) ? "what's changed" : "every chapter"} with ${eng === "chatterbox" ? "Chatterbox" : "Kokoro"}`
    + `${$("mkMusic").checked ? ", with music" : ""}${$("mkSounds").checked && !$("mkSoundsRow").hidden ? " and sounds" : ""}`);
  $("mkStyleRow").hidden = !($("mkVideo").checked && !$("mkVideoRow").hidden);
  if ($("mkVideo").checked && !$("mkVideoRow").hidden) {
    steps.push(`Draw ${$("mkStyle").options[$("mkStyle").selectedIndex].text.split(" (")[0].toLowerCase().replace(/^3d/, "3D")} pictures and make the video`);
  }
  $("mkPlan").replaceChildren(el("li", { class: "mk-plan-h", text: "It will:" }), ...steps.map((t) => el("li", { text: t })));
}
document.querySelectorAll("#makeDlg input, #makeDlg select").forEach((i) => i.addEventListener("change", updateMakePlan));
$("mkCancel").onclick = () => $("makeDlg").close();
$("mkStart").onclick = async () => {
  $("mkStart").disabled = true; $("mkStart").textContent = "Starting…";
  const engine = (document.querySelector("input[name=mkEngine]:checked") || {}).value;
  const choices = { narrator: $("mkNarrator").value, tidy: $("mkTidy").checked,
    music: $("mkMusic").checked ? "on" : "off", soundscapes: $("mkSounds").checked ? "on" : "off" };
  if (engine) choices.engine = engine;
  if ($("mkVideo").checked) choices.pictureStyle = $("mkStyle").value;
  const r = await api(`/api/projects/${book.id}/settings`, { json: choices });
  if (!r.ok) { $("mkStart").disabled = false; $("mkStart").textContent = "Start"; alert(r.error || "Couldn't save the choices."); return; }
  Object.assign(book, { narrator: choices.narrator, tidyPending: choices.tidy, music: choices.music, soundscapes: choices.soundscapes,
    pictureStyle: choices.pictureStyle || book.pictureStyle,
    engine: engine || book.engine });
  $("makeDlg").close();
  startJob("all", { video: $("mkVideo").checked && !$("mkVideoRow").hidden });
};
$("pauseJob").onclick = async () => {
  $("pauseJob").disabled = true; $("pauseJob").textContent = "Pausing…";
  $("runDetail").textContent = "Pausing: recording stops straight away; anything else finishes the step it's on first.";
  await api(`/api/projects/${book.id}/pause`, { method: "POST" });
};
$("resumeJob").onclick = async () => {
  $("resumeJob").disabled = true; $("resumeJob").textContent = "Resuming…";
  const r = await api(`/api/projects/${book.id}/resume`, { method: "POST" });
  if (!r.ok) { $("resumeJob").disabled = false; $("resumeJob").textContent = "Resume"; $("runDetail").textContent = r.error || "Couldn't resume."; return; }
  renderRun({ state: "queued", progress: 0, detail: "Waiting its turn" });
  startPolling();
};
$("stopJob").onclick = async () => {
  if (!confirm("Stop? What's done so far is kept: press the button again later to carry on from there.")) return;
  $("stopJob").disabled = true; $("stopJob").textContent = "Stopping…";
  $("runDetail").textContent = "Stopping: recording stops straight away; anything else finishes the step it's on first.";
  await api(`/api/projects/${book.id}/stop`, { method: "POST" });
};
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
const VOICE_GROUPS = [["own", "Your own voices"], ["standard", "Voices"], ["character", "Character voices"], ["accent", "With an accent"]];
function voiceOptions(gender, current) {
  const base = (current || "").replace(/\^[\d.]+$/, "");
  const list = voices.filter((v) => !gender || v.gender === gender || v.id === base);
  const opt = (v) => el("option", { value: v.id, selected: v.id === base,
    text: `${v.name} · ${v.accent ? v.accent + ", " : ""}${v.note}${v.own ? " (your clip)" : ""}` });
  return VOICE_GROUPS.map(([g, label]) => {
    const inGroup = list.filter((v) => (v.group || (v.own ? "own" : "standard")) === g);
    return inGroup.length ? el("optgroup", { label }, inGroup.map(opt)) : null;
  }).filter(Boolean);
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
function renderTidy() {
  const n = $("tidyNote"), t = book.tidyResult;
  const w = book.adultWording || 0;
  n.hidden = !(book.tidyPending || t || w);
  let msg = "";
  if (book.tidyPending) msg = "The spelling and sentences will be cleaned up before anything is cast or recorded.";
  else if (t) msg = `Writing cleaned up: ${t.changed} of ${t.sections} sections changed; sentence problems ${t.before} → ${t.after}.`;
  if (w) msg += `${msg ? " " : ""}“Young girl” and “young boy” were changed to “young woman” and “young man” in ${w} place${w === 1 ? "" : "s"}, since everyone in a grown-up book is an adult.`;
  n.textContent = msg;
}
$("tidyBtn").onclick = async () => {
  if (!confirm("Clean up the spelling and sentences of the whole book? Changed sections are cast again, and their lines are recorded again next time you record.")) return;
  if (await startJob("tidy")) { book.tidyPending = true; renderTidy(); }
};
function renderScript() {
  renderTidy();
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
    el("label", { class: "check every", title: "Play it each time these words are read, not just once" },
      el("input", { type: "checkbox", class: "fx-every", checked: !!fx.every }), " every time"),
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
      el("input", { type: "text", class: "amb", value: snd.chosen ? snd.ambience || "" : "", maxlength: "80",
        placeholder: "background: automatic (type a sound, or none)", "aria-label": "Background" }),
      fx, el("button", { type: "button", class: "small-btn fx-add", text: "Add an effect", onclick: () => fx.append(fxRow()) })));
  }));
}
async function saveSounds(quiet) {
  const sounds = {};
  document.querySelectorAll(".sound-scene").forEach((s) => {
    sounds[s.dataset.id] = { ambience: s.querySelector(".amb").value.trim(),
      effects: [...s.querySelectorAll(".fx-row")].map((r) => ({ sound: r.querySelector(".fx-s").value.trim(), at: r.querySelector(".fx-at").value.trim(),
        every: r.querySelector(".fx-every").checked })).filter((f) => f.sound) };
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
const ACTING_WORDS = (v) => (v < 0.8 ? "calm" : v < 0.95 ? "gentle" : v <= 1.05 ? "natural" : v <= 1.3 ? "expressive" : "dramatic");
$("acting").addEventListener("input", () => { $("actingOut").textContent = ACTING_WORDS(Number($("acting").value)); });
$("acting").addEventListener("change", async () => {
  await api(`/api/projects/${book.id}/settings`, { json: { acting: Number($("acting").value) } });
  book.acting = Number($("acting").value);
});
$("voiceModel").addEventListener("change", async () => {
  await api(`/api/projects/${book.id}/settings`, { json: { voiceModel: $("voiceModel").value } });
  book.voiceModel = $("voiceModel").value;
});
$("musicOn").addEventListener("change", async () => {
  book.music = $("musicOn").checked ? "on" : "off";
  await api(`/api/projects/${book.id}/settings`, { json: { music: book.music } });
});
function renderRecord() {
  $("musicOn").checked = (book.music || "on") === "on";
  $("acting").value = book.acting || 1; $("actingOut").textContent = ACTING_WORDS(Number($("acting").value));
  $("voiceModel").value = book.voiceModel || "standard";
  $("actingRow").hidden = !me.chatterbox;
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
// pictures and a video, timed to this reading (and a picture book)
function videoBlock(f) {
  const msg = el("p", { class: "hint" });
  const go = el("button", { type: "button", class: "small-btn", text: f.video ? "Make the pictures and video again" : "Make pictures and a video" });
  go.disabled = !f.pictures;
  const style = el("select", { "aria-label": "Picture style" },
    ...[["storybook", "Storybook (illustrated)"], ["realistic", "Realistic (like photographs)"], ["cinematic", "Cinematic (like a film)"], ["3d", "3D animated"], ["epic", "Epic action"]]
      .map(([v, t]) => el("option", { value: v, text: t, selected: (book.pictureStyle || "storybook") === v })));
  go.addEventListener("click", async () => {
    const changed = style.value !== (book.pictureStyle || "storybook");
    if (changed) {
      await api(`/api/projects/${book.id}/settings`, { json: { pictureStyle: style.value } });
      book.pictureStyle = style.value;
    }
    if (await startJob("video")) {
      msg.textContent = changed ? "Drawing every picture again in the new look… Progress is at the top."
        : "Drawing… Progress is at the top. Pictures already drawn are kept, so this is quicker the second time.";
      go.disabled = true;
    }
  });
  const box = el("div", { class: "send-sm video-block" },
    el("strong", { text: "Pictures and video" }),
    el("span", { class: "hint", text: f.pictures
      ? "A picture for every section, drawn in one look for the whole book (long sections get more than one), and a video of this audiobook with them: each picture stays on screen exactly while its part is read, with subtitles. Also a picture book (PDF)."
        + (book.level === "explicit" || book.level === "mature" ? " Grown-up books get tasteful pictures only: everyone is an adult and fully clothed." : "")
      : "Needs a picture app: open Draw Things, turn on Settings → API Server (HTTP, port 7860), and set LOCAL_IMAGE_URL in storymaker.env." }),
    f.pictures ? el("label", { class: "style-line" }, "Picture style ", style) : null, go, msg);
  if (f.video) {
    box.append(el("video", { controls: true, preload: "metadata", src: `${f.video}?${f.videoMade || ""}`, class: "book-video" }),
      el("div", { class: "downloads" },
        el("a", { href: `${f.video}?download=1`, text: "Video (MP4)" }),
        f.subtitles && el("a", { href: `${f.subtitles}?download=1`, text: "Subtitles (SRT)" }),
        f.pictureBook && el("a", { href: `${f.pictureBook}?download=1`, text: "Picture book (PDF)" })));
  }
  return box;
}

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
      el("p", { class: "hint", text: "One zip: the audiobook, the MP3, every chapter, the book as a PDF, the cover and the sound credits (and the video and picture book, once made)." }),
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
          + (r.video === "remade" ? " Its video was made again with this reading: the same pictures, each lasting as long as its scene is read now, with new subtitles (the old video is kept as story.before.mp4)."
            : r.had_video ? ` The video was ${r.video || "not remade"}.` : "");
        send.textContent = "Send to StoryMaker again";
      });
      side.append(el("div", { class: "send-sm" },
        el("strong", { text: "StoryMaker" }),
        el("span", { class: "hint", text: "Replace the story's audio in your StoryMaker library with this audiobook." }),
        send, msg));
    }
    side.append(videoBlock(f));
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
$("archiveBook").onclick = async () => {
  if (!confirm(`Move “${book.title}” to the archive on the NAS? It's copied and checked first, then removed from this computer. Bring it back any time from the Archive tab.`)) return;
  $("archiveBook").disabled = true; $("archiveNote").textContent = "Moving to the archive…";
  const r = await api("/api/nas/archive", { json: { ids: [book.id] } });
  if (!r.ok) { $("archiveBook").disabled = false; $("archiveNote").textContent = r.error || "Couldn't move it."; return; }
  const st = await waitNas(r.task, (t) => { $("archiveNote").textContent = `Moving to the archive… ${t.progress || 0}%`; });
  if (st.state === "done") { location.hash = ""; showHome(); }
  else { $("archiveBook").disabled = false; $("archiveNote").textContent = ((st.failed || [])[0] || {}).error || "Couldn't move it."; }
};
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
// record a voice right here (needs the page open on the Mac itself, or at a secure https address)
let recBlob = null, recorder = null, recTimer = null;
$("recBtn").addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") { recorder.stop(); return; }
  if (!window.isSecureContext || !navigator.mediaDevices || !window.MediaRecorder) {
    $("recState").textContent = "Recording works when this page is open on the Mac itself (localhost) or at its https address. Or choose a recording below.";
    return;
  }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: false } }); }
  catch { $("recState").textContent = "The microphone wasn't allowed. Allow it for this page and try again."; return; }
  const type = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const parts = [];
  recorder.ondataavailable = (ev) => { if (ev.data.size) parts.push(ev.data); };
  const started = Date.now();
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop()); clearInterval(recTimer);
    const secs = Math.round((Date.now() - started) / 1000);
    recBlob = new Blob(parts, { type: recorder.mimeType || "audio/webm" });
    $("recPreview").src = URL.createObjectURL(recBlob); $("recPreview").hidden = false;
    $("recBtn").textContent = "Record again"; $("recScript").hidden = true;
    $("recState").textContent = secs < 10 ? `Only ${secs} seconds: 20–30 sounds much more like you. Record again?` : `Recorded ${secs} seconds. Listen back, then name it and press Add the voice.`;
  };
  recorder.start(500);
  $("recScript").hidden = false; $("recPreview").hidden = true; recBlob = null;
  $("recBtn").textContent = "Stop recording";
  recTimer = setInterval(() => {
    const t = Math.round((Date.now() - started) / 1000);
    $("recState").textContent = `Recording… ${t}s`;
    if (t >= 40) recorder.stop();
  }, 500);
});
$("uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("clipFile").files[0];
  if (!file && !recBlob) { $("clipMsg").textContent = "Record your voice or choose a recording first."; return; }
  const fd = new FormData();
  if (file) fd.append("file", file);
  else fd.append("file", recBlob, (recBlob.type || "").includes("mp4") ? "recording.mp4" : "recording.webm");
  fd.append("name", $("clipName").value); fd.append("gender", $("clipGender").value);
  $("clipGo").disabled = true; $("clipMsg").textContent = "Adding…";
  const r = await api("/api/voices/upload", { method: "POST", body: fd });
  $("clipGo").disabled = false;
  $("clipMsg").textContent = r.ok ? "Added. Pick it for anyone in the cast." : r.error || "Not added.";
  if (r.ok) {
    voices = (await api("/api/voices")).voices || voices; renderLib(); $("uploadForm").reset();
    recBlob = null; $("recPreview").hidden = true; $("recState").textContent = ""; $("recBtn").textContent = "Record my voice here";
    $("clipMsg").textContent = "Added. Pick it as the narrator (Make the audiobook) or for anyone in the cast. It's used with Chatterbox.";
    if (book && tab === "cast") renderCast();
  }
});
