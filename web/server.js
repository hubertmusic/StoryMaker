// StoryMaker web page — a small Express server around storymaker.py.
// Every request becomes a storymaker.py command run with an argument list (never a shell),
// and every value from the browser is checked against a fixed list or a number range first.
//
// People sign in with their own accounts (kept in a small SQLite database next to the stories).
// The owner sees everything and manages accounts; Explicit (18+) is only ever available to the owner.

const express = require("express");
const crypto = require("crypto");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { DatabaseSync } = require("node:sqlite");

const PORT = Number(process.env.PORT || 3000);
const PYTHON = process.env.PYTHON || "python3";
const SCRIPT = process.env.STORYMAKER || path.join(__dirname, "..", "storymaker.py");
const OUTPUT_DIR = (process.env.OUTPUT_DIR || path.join(process.env.HOME || "", "StoryMaker/output"))
  .replace(/^~(?=\/)/, process.env.HOME || "");
// The owner's first user name and password: from the environment, or from storymaker.env (APP_USER= / APP_PASSWORD=)
function envFileValue(key) {
  try {
    const text = fs.readFileSync(process.env.STORYMAKER_ENV || path.join(__dirname, "storymaker.env"), "utf8");
    let val = "";
    for (const line of text.split("\n")) {
      const m = line.match(new RegExp(`^\\s*${key}\\s*=\\s*(.*)$`));
      if (m) val = m[1].split(/\s+#/)[0].trim().replace(/^["']|["']$/g, "");
    }
    return val;
  } catch { return ""; }
}
const APP_USER = process.env.APP_USER || envFileValue("APP_USER") || "story";
const APP_PASSWORD = process.env.APP_PASSWORD || envFileValue("APP_PASSWORD") || "";

const SAFE_LEVELS = ["young-kids", "kids", "middle-grade", "teen", "adult", "family"];
const ADULT_LEVELS = ["mature", "explicit"];
const LEVELS = [...SAFE_LEVELS, ...ADULT_LEVELS];
const MODES = ["story", "audio", "video"];
const VIDEOS = ["auto", "omni", "veo", "slideshow", "demo"];
const WRITERS = ["", "claude", "gemini", "openai", "groq", "ollama", "demo"];
const SESSION_DAYS = 30;

// ───────────────────────────── database ─────────────────────────────
fs.mkdirSync(OUTPUT_DIR, { recursive: true });
const db = new DatabaseSync(path.join(OUTPUT_DIR, ".storymaker.db"));
db.exec(`
  PRAGMA journal_mode = WAL;
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, salt TEXT NOT NULL, hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', mature INTEGER NOT NULL DEFAULT 0, adult_switch INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS stories (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT, story_id TEXT,
    created TEXT NOT NULL DEFAULT (datetime('now')));
`);
try { db.exec("ALTER TABLE jobs ADD COLUMN info TEXT"); } catch { /* already there */ }
db.exec("CREATE TABLE IF NOT EXISTS styles (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL DEFAULT '{}')");
const CUT_OFF = [];   // stories a restart cut off: they're picked up again by themselves once StoryMaker is ready
// StoryMaker just started, so nothing is being made yet: work that was cut off by a restart or an update shows
// as stopped (and Make it again carries on from where it was), not as running forever.
try {
  const jd = path.join(OUTPUT_DIR, ".jobs");
  for (const name of fs.readdirSync(jd)) {
    if (!name.endsWith(".status.json")) continue;
    const f = path.join(jd, name);
    try {
      const st = JSON.parse(fs.readFileSync(f, "utf8"));
      if (st.state === "running" || st.state === "queued") {
        st.state = "failed";
        st.error = "StoryMaker was restarted while this was being made.";
        st.interrupted = true;
        fs.writeFileSync(f, JSON.stringify(st));
        CUT_OFF.push(name.replace(".status.json", ""));
      }
    } catch { /* unreadable: leave it */ }
  }
} catch { /* no jobs yet */ }
const hashPw = (pw, salt) => crypto.scryptSync(String(pw), salt, 32).toString("hex");
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");

// First start: the owner comes from the earlier single-password settings, or APP_USER / APP_PASSWORD.
if (!db.prepare("SELECT COUNT(*) AS n FROM users").get().n) {
  let old = null;
  try { old = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, ".web-settings.json"), "utf8")); } catch {}
  if (old && old.hash) {
    db.prepare("INSERT INTO users (username, salt, hash, role, adult_switch) VALUES (?, ?, ?, 'owner', ?)")
      .run(old.user || APP_USER, old.salt, old.hash, old.adult ? 1 : 0);
  } else {
    const pw = APP_PASSWORD || "change-me";
    if (!APP_PASSWORD) console.warn('No APP_PASSWORD set: the owner password is "change-me". Change it in Settings.');
    const salt = crypto.randomBytes(16).toString("hex");
    db.prepare("INSERT INTO users (username, salt, hash, role) VALUES (?, ?, ?, 'owner')").run(APP_USER, salt, hashPw(pw, salt));
  }
}
const owner = () => db.prepare("SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").get();

function passwordOk(user, pw) {
  if (!user) return false;
  const a = Buffer.from(hashPw(pw || "", user.salt), "hex"), b = Buffer.from(user.hash, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// What each person may make and see. Explicit: the owner only, and only with the switch on.
function allowedLevels(u) {
  const levels = [...SAFE_LEVELS];
  if (u.role === "owner" ? u.adult_switch : u.mature) levels.push("mature");
  if (u.role === "owner" && u.adult_switch) levels.push("explicit");
  return levels;
}
function readResult(id) {
  try { return JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, id, "result.json"), "utf8")); } catch { return null; }
}
// Folders made before accounts existed, or by a background job, get their owner here.
function syncStories() {
  let dirs = [];
  try { dirs = fs.readdirSync(OUTPUT_DIR, { withFileTypes: true }).filter((d) => d.isDirectory() && !d.name.startsWith(".")); } catch {}
  const known = new Set(db.prepare("SELECT id FROM stories").all().map((r) => r.id));
  const ownerId = owner().id;
  for (const d of dirs) {
    if (known.has(d.name)) continue;
    const r = readResult(d.name);
    if (!r) continue;
    const job = r.job_id && db.prepare("SELECT user_id FROM jobs WHERE id = ?").get(r.job_id);
    db.prepare("INSERT OR IGNORE INTO stories (id, user_id) VALUES (?, ?)").run(d.name, job ? job.user_id : ownerId);
  }
  return dirs.map((d) => d.name);
}
function canSee(u, id) {
  const row = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(id);
  if (!row) return false;
  if (u.role !== "owner" && row.user_id !== u.id) return false;
  const r = readResult(id);
  return !!r && allowedLevels(u).includes(r.level || "family");
}

// ───────────────────────────── app & sign-in ─────────────────────────────
const app = express();
app.use(express.json({ limit: "256kb" }));

function cookies(req) {
  return Object.fromEntries((req.headers.cookie || "").split(";").map((c) => c.trim().split("=")).filter((p) => p[0])
    .map(([k, ...v]) => [k, decodeURIComponent(v.join("="))]));
}
function currentUser(req) {
  const token = cookies(req).sm_session;
  if (!token) return null;
  const s = db.prepare("SELECT * FROM sessions WHERE token = ?").get(sha(token));
  if (!s || s.expires < Date.now()) return null;
  return db.prepare("SELECT * FROM users WHERE id = ?").get(s.user_id) || null;
}

const attempts = new Map();   // slow down password guessing: 8 tries per 10 minutes per address
app.post("/api/login", (req, res) => {
  const key = req.ip;
  const now = Date.now();
  const a = (attempts.get(key) || []).filter((t) => now - t < 10 * 60 * 1000);
  if (a.length >= 8) return res.status(429).json({ ok: false, error: "Too many tries. Wait a few minutes and try again." });
  const { username, password } = req.body || {};
  const u = db.prepare("SELECT * FROM users WHERE username = ?").get(String(username || "").trim());
  if (!passwordOk(u, password)) {
    a.push(now); attempts.set(key, a);
    return res.status(401).json({ ok: false, error: "That user name and password don't match." });
  }
  attempts.delete(key);
  const token = crypto.randomBytes(32).toString("hex");
  db.prepare("INSERT INTO sessions (token, user_id, expires) VALUES (?, ?, ?)").run(sha(token), u.id, now + SESSION_DAYS * 864e5);
  db.prepare("DELETE FROM sessions WHERE expires < ?").run(now);
  res.set("Set-Cookie", `sm_session=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}`);
  res.json({ ok: true });
});
app.post("/api/logout", (req, res) => {
  const token = cookies(req).sm_session;
  if (token) db.prepare("DELETE FROM sessions WHERE token = ?").run(sha(token));
  res.set("Set-Cookie", "sm_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0").json({ ok: true });
});

// The sign-in page and what it needs are open; everything else needs a signed-in person.
const OPEN = /^\/(login\.html|login\.js|style\.css|fonts\/[\w.-]+)$/;
app.use((req, res, next) => {
  if (OPEN.test(req.path)) return next();
  req.user = currentUser(req);
  if (req.user) return next();
  if (req.path.startsWith("/api/") || req.path.startsWith("/media/")) return res.status(401).json({ ok: false, error: "Please sign in." });
  res.redirect("/login.html");
});

app.use(express.static(path.join(__dirname, "public")));

// Finished stories (videos, audio, books): only for people allowed to see that story.
app.use("/media", (req, res, next) => {
  const id = decodeURIComponent(req.path.split("/")[1] || "");
  if (!id || id.startsWith(".") || !canSee(req.user, id)) return res.sendStatus(404);
  next();
}, express.static(OUTPUT_DIR, { dotfiles: "deny", fallthrough: false }));

function runStorymaker(user, args, stdinText = "") {
  return new Promise((resolve) => {
    const explicitOk = user && allowedLevels(user).includes("explicit");
    const style = user ? styleFor(user) : {};
    if (user && style.writing) for (const p of PROFILES) if (!allowedProfiles(user).includes(p)) delete style.writing[p];
    const child = spawn(PYTHON, [SCRIPT, ...args], {
      env: { ...process.env, MEDIA_BASE_URL: process.env.MEDIA_BASE_URL || "/media", ADULT_CONTENT: explicitOk ? "on" : "off",
        STORYMAKER_STYLE: JSON.stringify(style) },
    });
    let out = "", err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => {
      const last = out.trim().split("\n").pop() || "";
      try {
        resolve(JSON.parse(last));
      } catch {
        const msg = (err.trim().split("\n").pop() || `storymaker exited with code ${code}`).replace(/^\[storymaker[^\]]*\]\s*/, "");
        resolve({ ok: false, error: msg });
      }
    });
    child.stdin.end(stdinText);
  });
}

// Turn the form into a safe argument list.
function buildArgs(b) {
  const args = [];
  const pick = (v, list, def) => (list.includes(v) ? v : def);
  args.push("--mode", pick(b.mode, MODES, "video"));
  const scenes = Math.max(1, Math.min(12, parseInt(b.scenes, 10) || 5));
  args.push("--scenes", String(scenes));
  const chapters = Math.max(1, Math.min(20, parseInt(b.chapters, 10) || 1));
  if (chapters > 1) args.push("--chapters", String(chapters));
  const detail = ["brief", "standard", "rich", "epic"].includes(b.detail) ? b.detail : "standard";
  if (detail !== "standard") args.push("--detail", detail);
  if (typeof b.genre === "string" && b.genre.trim()) args.push("--genre", b.genre.trim().slice(0, 120));
  if (b.series && typeof b.series === "string") {
    args.push("--series", b.series.slice(0, 120));
  } else if (b.level) {
    args.push("--level", pick(b.level, LEVELS, "family"));
  }
  const video = pick(b.video, VIDEOS, "auto");
  if (video !== "auto") args.push("--video", video);
  const writer = pick(b.writer || "", WRITERS, "");
  if (writer) args.push("--provider", writer);
  if (b.portrait) args.push("--portrait");
  if (b.useStyle === false) args.push("--no-style");
  if (b.book) args.push("--book");
  if (typeof b.keywords === "string" && b.keywords.trim()) args.push("--keywords", b.keywords.trim().slice(0, 600));
  if (typeof b.sounds === "string" && b.sounds.trim()) args.push("--sounds", b.sounds.trim().slice(0, 300));
  if (VOICE_SPEC.test(b.narrator || "")) args.push("--narrator", b.narrator);
  if (["first", "third"].includes(b.pov)) args.push("--pov", b.pov);
  if (b.level === "explicit" && !b.series) {          // Explicit's own controls (the level is checked separately)
    const heat = parseInt(b.heat, 10);
    if (heat >= 1 && heat <= 5) args.push("--heat", String(heat));
    if (["slow", "steady", "fast"].includes(b.pacing)) args.push("--pacing", b.pacing);
    if (["tasteful", "frank", "crude"].includes(b.language)) args.push("--language", b.language);
    if (typeof b.turnOns === "string" && b.turnOns.trim()) args.push("--turn-ons", b.turnOns.trim().slice(0, 400));
    if (typeof b.limits === "string" && b.limits.trim()) args.push("--limits", b.limits.trim().slice(0, 400));
  }
  return args;
}
const VOICE_SPEC = /^[ab][fm]_[a-z]+(\+[ab][fm]_[a-z]+)?(\^(0\.[89]\d?|1\.[01]\d?))?$/;
const idea = (b) => (typeof b.idea === "string" ? b.idea.slice(0, 2000) : "");

// The series a person may continue: those from stories they can see.
function visibleSeries(u) {
  const names = new Set();
  for (const id of syncStories()) {
    if (!canSee(u, id)) continue;
    const r = readResult(id);
    if (r && r.series) names.add(r.series);
  }
  return names;
}
function levelError(u, b) {
  if (b.series) {
    if (!visibleSeries(u).has(b.series)) return "That series isn't one of yours.";
    return null;
  }
  if (!allowedLevels(u).includes(b.level || "family")) {
    return u.role === "owner" ? "Mature and Explicit are switched off. Turn them on in Settings (the gear icon)."
      : "That reading level isn't available on your account.";
  }
  return null;
}

app.post("/api/estimate", async (req, res) => {
  const e = levelError(req.user, req.body);
  if (e) return res.json({ ok: false, error: e });
  res.json(await runStorymaker(req.user, [...buildArgs(req.body), "--estimate"], idea(req.body)));
});

app.post("/api/make", async (req, res) => {
  const e = levelError(req.user, req.body);
  if (e) return res.json({ ok: false, error: e });
  if (!req.body.series && !idea(req.body).trim()) return res.json({ ok: false, error: "Type what the story should be about first." });
  const r = await runStorymaker(req.user, [...buildArgs(req.body), "--yes", "--background"], idea(req.body));
  if (r.ok && r.job_id) {
    // everything needed to carry on later from any device, if it's ever cut off
    const info = JSON.stringify({ mode: req.body.mode || "story", book: !!req.body.book, idea: idea(req.body).slice(0, 140),
      body: req.body });
    db.prepare("INSERT OR REPLACE INTO jobs (id, user_id, kind, info) VALUES (?, ?, 'make', ?)").run(r.job_id, req.user.id, info);
  }
  res.json(r);
});

function ownJob(u, id) {
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  return j && (j.user_id === u.id || u.role === "owner");
}
app.get("/api/status/:id", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  if (!ownJob(req.user, id)) return res.json({ ok: false, error: "Unknown job" });
  const f = path.join(OUTPUT_DIR, ".jobs", `${id}.status.json`);
  if (!fs.existsSync(f)) return res.json({ ok: false, error: "Unknown job" });
  const st = JSON.parse(fs.readFileSync(f, "utf8"));
  if (st.state === "done" && st.result && st.result.run_id) {
    db.prepare("INSERT OR IGNORE INTO stories (id, user_id) VALUES (?, ?)").run(st.result.run_id,
      db.prepare("SELECT user_id FROM jobs WHERE id = ?").get(id).user_id);
  }
  res.json(st);
});

// Everything this person has running, so every device they're signed in on shows it (Mac, iPad, iPhone…).
// A job that hasn't reported anything for an hour has stopped (the Mac restarted, say) and isn't shown.
app.get("/api/jobs/active", (req, res) => {
  const rows = db.prepare("SELECT * FROM jobs WHERE user_id = ? AND created > datetime('now', '-3 days') ORDER BY created DESC")
    .all(req.user.id);
  const jobs = [];
  for (const j of rows) {
    const f = path.join(OUTPUT_DIR, ".jobs", `${j.id}.status.json`);
    let st, age;
    try { st = JSON.parse(fs.readFileSync(f, "utf8")); age = Date.now() - fs.statSync(f).mtimeMs; } catch { continue; }
    const live = (st.state === "running" || st.state === "queued") && age < 60 * 60 * 1000;
    const justDone = (st.state === "done" || st.state === "failed") && age < 10 * 60 * 1000;
    if (!live && !justDone) continue;
    let info = {};
    try { info = JSON.parse(j.info || "{}"); } catch { /* old row */ }
    let storyTitle = null;
    if (j.story_id) storyTitle = (readResult(j.story_id) || {}).title || null;
    jobs.push({ id: j.id, kind: j.kind, storyId: j.story_id, storyTitle, info, state: st.state, step: st.step || null,
      progress: st.progress || 0, detail: st.detail || "", title: st.title || null, error: st.error || null,
      runId: st.result && st.result.run_id || null, updated: Math.round(age / 1000) });
  }
  res.json({ ok: true, jobs });
});

// Stories that were started but never finished (stopped, cut off by a restart, or still waiting): the scenes
// already written are kept in the job's folder. They can be read, carried on, or deleted from any device.
function jobFolder(id) { return path.join(OUTPUT_DIR, ".jobs", id); }
function jobScenes(id) {
  let names = [];
  try { names = fs.readdirSync(jobFolder(id)).filter((n) => /^scene_c\d+_s\d+\.txt$/.test(n)).sort(); } catch { /* none */ }
  return names;
}
function jobOutline(id) {
  try { return JSON.parse(fs.readFileSync(path.join(jobFolder(id), "outline.json"), "utf8")); } catch { return null; }
}
app.get("/api/jobs/unfinished", (req, res) => {
  const rows = db.prepare("SELECT * FROM jobs WHERE user_id = ? AND kind = 'make' AND created > datetime('now', '-60 days') "
    + "ORDER BY created DESC").all(req.user.id);
  const out = [];
  for (const j of rows) {
    const f = path.join(OUTPUT_DIR, ".jobs", `${j.id}.status.json`);
    let st = {}, age = 0;
    try { st = JSON.parse(fs.readFileSync(f, "utf8")); age = Date.now() - fs.statSync(f).mtimeMs; } catch { continue; }
    const stale = (st.state === "running" || st.state === "queued") && age > 60 * 60 * 1000;
    if (st.state !== "failed" && !stale) continue;
    const scenes = jobScenes(j.id), outline = jobOutline(j.id);
    if (!scenes.length && !outline) continue;              // nothing was written: nothing to find
    let info = {};
    try { info = JSON.parse(j.info || "{}"); } catch { /* old row */ }
    const planned = outline && Array.isArray(outline.chapters)
      ? outline.chapters.reduce((n, c) => n + ((c.scenes || []).length || 0), 0) : 0;
    out.push({ id: j.id, title: (outline && outline.title) || st.title || "Untitled story", idea: info.idea || "",
      level: (info.body && info.body.level) || null, started: j.created, written: scenes.length, planned,
      error: stale ? "It was cut off (StoryMaker or the Mac restarted)." : (st.error || ""),
      canResume: !!(info.body || outline) });
  }
  res.json({ ok: true, stories: out });
});
app.get("/api/jobs/:id/text", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  if (!j || j.user_id !== req.user.id) return res.status(404).json({ ok: false, error: "Not found." });
  const outline = jobOutline(id) || {};
  const chapters = outline.chapters || [];
  const parts = [`# ${outline.title || "Untitled story"}`, ""];
  if (outline.logline) parts.push(`*${outline.logline}*`, "");
  let lastCh = 0;
  for (const name of jobScenes(id)) {
    const [, c] = name.match(/^scene_c(\d+)_s(\d+)/).map(Number);
    if (c !== lastCh && chapters.length > 1) {
      parts.push(`## Chapter ${c}: ${(chapters[c - 1] || {}).title || ""}`, "");
      lastCh = c;
    }
    try { parts.push(fs.readFileSync(path.join(jobFolder(id), name), "utf8").trim(), ""); } catch { /* skip */ }
  }
  res.json({ ok: true, text: parts.join("\n"), written: jobScenes(id).length });
});
app.post("/api/jobs/:id/resume", async (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  if (!j || j.user_id !== req.user.id) return res.status(404).json({ ok: false, error: "Not found." });
  res.json(await recoverJob(req.user, j));
});
// Carry a stopped story on in its own folder: only the scenes it hasn't written yet are written. Uses the
// settings it was started with when they're saved; for older stories they're worked out from what it wrote.
async function recoverJob(user, j) {
  let info = {};
  try { info = JSON.parse(j.info || "{}"); } catch { /* old row */ }
  let args;
  if (info.body) {
    const e = levelError(user, info.body);
    if (e) return { ok: false, error: e };
    args = [...buildArgs(info.body)];
  } else {
    args = ["--mode", ["story", "audio", "video"].includes(info.mode) ? info.mode : "story", ...(info.book ? ["--book"] : [])];
  }
  const r = await runStorymaker(user, [...args, "--recover", j.id, "--yes", "--background"], info.body ? idea(info.body) : (info.idea || ""));
  if (r.ok && r.job_id) {
    db.prepare("UPDATE jobs SET created = datetime('now') WHERE id = ?").run(j.id);
    if (r.job_id !== j.id) db.prepare("INSERT OR REPLACE INTO jobs (id, user_id, kind, info) VALUES (?, ?, 'make', ?)")
      .run(r.job_id, j.user_id, j.info);
  }
  return r;
}
// After a restart (an update, the Mac restarting), stories that were being written carry on by themselves.
setTimeout(async () => {
  for (const id of CUT_OFF) {
    const j = db.prepare("SELECT * FROM jobs WHERE id = ? AND kind = 'make' AND created > datetime('now', '-2 days')").get(id);
    const u = j && db.prepare("SELECT * FROM users WHERE id = ?").get(j.user_id);
    if (!j || !u || !fs.existsSync(path.join(jobFolder(id), "outline.json"))) continue;
    const r = await recoverJob(u, j);
    console.log(`Carrying on story ${id} after the restart: ${r.ok ? "started" : r.error}`);
  }
}, 20000);

app.delete("/api/jobs/:id", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  if (!j || j.user_id !== req.user.id) return res.status(404).json({ ok: false, error: "Not found." });
  fs.rmSync(jobFolder(id), { recursive: true, force: true });
  fs.rmSync(path.join(OUTPUT_DIR, ".jobs", `${id}.status.json`), { force: true });
  db.prepare("DELETE FROM jobs WHERE id = ?").run(id);
  res.json({ ok: true });
});

// Preview pictures of a story that's still being made (small JPEGs in its work folder).
app.get("/api/status/:id/preview/:name", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const name = String(req.params.name);
  if (!id || !ownJob(req.user, id) || !/^preview_[\w-]+\.jpg$/.test(name)) return res.sendStatus(404);
  const f = path.join(OUTPUT_DIR, ".jobs", id, name);
  if (!fs.existsSync(f)) return res.sendStatus(404);
  res.set("Cache-Control", "no-store").sendFile(f);
});

// ───────────────────────────── your style ─────────────────────────────
// How you want stories written (tone, words, passages to match, and what you said about earlier stories), and
// how pictures, video and sounds should feel. Kept per person and used for everything they make.
const PROFILES = ["kids", "family", "sfw", "mature", "explicit"];
const LEVEL_PROFILE = { "young-kids": "kids", kids: "kids", "middle-grade": "kids", family: "family", teen: "sfw",
  adult: "sfw", mature: "mature", explicit: "explicit" };
function styleFor(u) {
  let st = {};
  try { st = JSON.parse((db.prepare("SELECT data FROM styles WHERE user_id = ?").get(u.id) || {}).data || "{}"); } catch { /* none */ }
  const w = st.writing || {};
  // styles saved before there were five kinds: everyday → safe for work, grown-up → mature and explicit
  if (w.everyday && !w.sfw) w.sfw = w.everyday;
  if (w.grown && !w.mature) w.mature = w.grown;
  if (w.grown && !w.explicit) w.explicit = JSON.parse(JSON.stringify(w.grown));
  if (w.kids && !w.family) w.family = JSON.parse(JSON.stringify(w.kids));
  delete w.everyday; delete w.grown;
  st.writing = w;
  return st;
}
function allowedProfiles(u) {
  const lv = allowedLevels(u);
  return PROFILES.filter((p) => (p === "mature" ? lv.includes("mature") : p === "explicit" ? lv.includes("explicit") : true));
}
function saveStyle(u, data) {
  db.prepare("INSERT INTO styles (user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data")
    .run(u.id, JSON.stringify(data));
}
const str = (v, n) => (typeof v === "string" ? v.slice(0, n) : "");
function cleanProfile(p) {
  p = p || {};
  return {
    tone: str(p.tone, 1500), use: str(p.use, 1200), avoid: str(p.avoid, 800), share: p.share !== false,
    references: (Array.isArray(p.references) ? p.references : []).map((r) => str(r, 4000)).filter((r) => r.trim()).slice(-5),
    feedback: (Array.isArray(p.feedback) ? p.feedback : []).filter((f) => f && typeof f === "object").map((f) => ({
      rating: ["up", "down"].includes(f.rating) ? f.rating : "", notes: str(f.notes, 800), title: str(f.title, 120),
      date: str(f.date, 30) })).filter((f) => f.notes.trim()).slice(-20),
  };
}
app.get("/api/style", (req, res) => {
  const st = styleFor(req.user);
  const allowed = allowedProfiles(req.user);
  for (const p of PROFILES) if (!allowed.includes(p)) delete st.writing[p];
  res.json({ ok: true, style: st, profiles: allowed });
});
app.post("/api/style", (req, res) => {
  const b = req.body || {};
  const w = b.writing || {};
  const data = {
    writing: Object.fromEntries(PROFILES.map((p) => [p, cleanProfile(w[p])])),
    pictures: { style: str((b.pictures || {}).style, 600), avoid: str((b.pictures || {}).avoid, 400) },
    video: { style: str((b.video || {}).style, 600) },
    sounds: { like: str((b.sounds || {}).like, 600), avoid: str((b.sounds || {}).avoid, 400) },
  };
  const before = styleFor(req.user).writing || {};
  for (const p of PROFILES) if (!allowedProfiles(req.user).includes(p)) data.writing[p] = cleanProfile(before[p]);  // untouched
  saveStyle(req.user, data);
  res.json({ ok: true });
});
// Feedback on a story: what you liked or didn't, and optionally a passage to match. Goes into the style used
// for stories like it (grown-up stories teach the grown-up style, everything else the everyday one).
app.post("/api/stories/:id/feedback", (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const r = readResult(id) || {};
  const prof = LEVEL_PROFILE[r.level] || "family";
  const st = styleFor(req.user);
  st.writing = st.writing || {};
  const p = cleanProfile(st.writing[prof]);
  const notes = str(b.notes, 800).trim();
  if (notes) p.feedback.push({ rating: ["up", "down"].includes(b.rating) ? b.rating : "", notes, title: str(r.title, 120),
    date: new Date().toISOString().slice(0, 10) });
  const passage = str(b.passage, 4000).trim();
  if (passage) p.references.push(passage);
  if (!notes && !passage) return res.json({ ok: false, error: "Write what you liked or didn't, or keep a passage." });
  st.writing[prof] = cleanProfile(p);
  saveStyle(req.user, st);
  res.json({ ok: true, profile: prof });
});

// ───────────────────────────── accounts ─────────────────────────────
const publicUser = (u) => ({ id: u.id, username: u.username, role: u.role, mature: !!u.mature, created: u.created });

app.get("/api/me", (req, res) => {
  const u = req.user;
  res.json({ ok: true, ...publicUser(u), adultSwitch: !!u.adult_switch, levels: allowedLevels(u) });
});
let pyFeatures = null;
app.get("/api/features", async (req, res) => {
  if (!pyFeatures) pyFeatures = await runStorymaker(req.user, ["--features"]);
  const levels = allowedLevels(req.user);
  res.json({ ok: true, mature: levels.includes("mature"), adult: levels.includes("explicit"), role: req.user.role,
    localPictures: !!(pyFeatures && pyFeatures.local_pictures), studioUrl: process.env.AUDIO_STUDIO_URL || "" });
});

// Your own account: user name, password, and (owner) the grown-up switch. Needs your current password.
app.post("/api/me", (req, res) => {
  const b = req.body || {}, u = req.user;
  if (!passwordOk(u, b.current)) return res.json({ ok: false, error: "Your current password isn't right." });
  const username = typeof b.username === "string" ? b.username.trim() : u.username;
  if (!/^[\w .@-]{1,40}$/.test(username)) return res.json({ ok: false, error: "Use a user name of up to 40 letters, numbers, spaces or . @ - _" });
  const clash = db.prepare("SELECT id FROM users WHERE username = ? AND id != ?").get(username, u.id);
  if (clash) return res.json({ ok: false, error: "Someone already has that user name." });
  let salt = u.salt, hash = u.hash;
  if (b.password) {
    if (String(b.password).length < 6) return res.json({ ok: false, error: "Use a new password of at least 6 characters." });
    salt = crypto.randomBytes(16).toString("hex");
    hash = hashPw(b.password, salt);
  }
  const adult = u.role === "owner" && typeof b.adult === "boolean" ? (b.adult ? 1 : 0) : u.adult_switch;
  db.prepare("UPDATE users SET username = ?, salt = ?, hash = ?, adult_switch = ? WHERE id = ?").run(username, salt, hash, adult, u.id);
  if (b.password) {   // sign out everywhere else
    const keep = sha(cookies(req).sm_session || "");
    db.prepare("DELETE FROM sessions WHERE user_id = ? AND token != ?").run(u.id, keep);
  }
  res.json({ ok: true });
});

// People (owner only): add, change, remove.
function ownerOnly(req, res, next) {
  if (req.user.role !== "owner") return res.status(403).json({ ok: false, error: "Only the owner can do that." });
  next();
}
app.get("/api/users", ownerOnly, (req, res) => {
  const counts = Object.fromEntries(db.prepare("SELECT user_id, COUNT(*) AS n FROM stories GROUP BY user_id").all().map((r) => [r.user_id, r.n]));
  res.json({ ok: true, users: db.prepare("SELECT * FROM users ORDER BY role DESC, username").all()
    .map((u) => ({ ...publicUser(u), stories: counts[u.id] || 0 })) });
});
app.post("/api/users", ownerOnly, (req, res) => {
  const { username, password, mature } = req.body || {};
  const name = String(username || "").trim();
  if (!/^[\w .@-]{1,40}$/.test(name)) return res.json({ ok: false, error: "Use a user name of up to 40 letters, numbers, spaces or . @ - _" });
  if (String(password || "").length < 6) return res.json({ ok: false, error: "Give them a password of at least 6 characters." });
  if (db.prepare("SELECT id FROM users WHERE username = ?").get(name)) return res.json({ ok: false, error: "Someone already has that user name." });
  const salt = crypto.randomBytes(16).toString("hex");
  db.prepare("INSERT INTO users (username, salt, hash, role, mature) VALUES (?, ?, ?, 'member', ?)").run(name, salt, hashPw(password, salt), mature ? 1 : 0);
  res.json({ ok: true });
});
app.patch("/api/users/:id", ownerOnly, (req, res) => {
  const u = db.prepare("SELECT * FROM users WHERE id = ?").get(Number(req.params.id));
  if (!u || u.role === "owner") return res.json({ ok: false, error: "Change your own account under My account." });
  const b = req.body || {};
  if (typeof b.mature === "boolean") db.prepare("UPDATE users SET mature = ? WHERE id = ?").run(b.mature ? 1 : 0, u.id);
  if (b.password) {
    if (String(b.password).length < 6) return res.json({ ok: false, error: "Use a password of at least 6 characters." });
    const salt = crypto.randomBytes(16).toString("hex");
    db.prepare("UPDATE users SET salt = ?, hash = ? WHERE id = ?").run(salt, hashPw(b.password, salt), u.id);
    db.prepare("DELETE FROM sessions WHERE user_id = ?").run(u.id);
  }
  res.json({ ok: true });
});
app.delete("/api/users/:id", ownerOnly, (req, res) => {
  const u = db.prepare("SELECT * FROM users WHERE id = ?").get(Number(req.params.id));
  if (!u || u.role === "owner") return res.json({ ok: false, error: "The owner account can't be removed." });
  db.prepare("UPDATE stories SET user_id = ? WHERE user_id = ?").run(req.user.id, u.id);   // their stories come to you
  db.prepare("DELETE FROM sessions WHERE user_id = ?").run(u.id);
  db.prepare("DELETE FROM users WHERE id = ?").run(u.id);
  res.json({ ok: true });
});

// ───────────────────────────── voices ─────────────────────────────
let voiceList = null;
app.get("/api/voices", async (req, res) => {
  if (!voiceList) voiceList = await runStorymaker(req.user, ["--voices-list"]);
  res.json(voiceList);
});
app.get("/api/voices/sample", async (req, res) => {
  const v = String(req.query.v || "");
  if (!VOICE_SPEC.test(v)) return res.status(404).send("Unknown voice");
  const r = await runStorymaker(req.user, ["--voice-sample", v]);
  if (!r.ok || !r.file) return res.status(500).send(r.error || "Couldn't make a sample");
  res.set("Cache-Control", "private, max-age=86400").sendFile(r.file);
});

// ───────────────────────────── stories ─────────────────────────────
app.get("/api/series", async (req, res) => {
  const r = await runStorymaker(req.user, ["--list-series"]);
  const mine = visibleSeries(req.user);
  if (r.ok) r.series = (r.series || []).filter((s) => mine.has(s.name));
  res.json(r);
});

// A finished story's folder name: letters, numbers, dash, underscore, dot — never a hidden folder.
const storyId = (id) => (/^[\w][\w.-]*$/.test(String(id)) && id !== "series" ? String(id) : null);
function guard(req, res) {
  const id = storyId(req.params.id);
  if (!id || !canSee(req.user, id)) { res.status(404).json({ ok: false, error: "Unknown story" }); return null; }
  return id;
}
function niceName(id) {
  const r = readResult(id);
  return ((r && r.title) || id).replace(/[^\w\s'-]/g, "").trim().slice(0, 80) || "story";
}

// Audio to keep: the audiobook (.m4b, with chapters and cover) or the MP3, named after the story.
app.get("/api/stories/:id/audio/:kind", (req, res) => {
  const id = guard(req, res); if (!id) return;
  const file = { m4b: "story.m4b", mp3: "story.mp3" }[req.params.kind];
  const f = file && path.join(OUTPUT_DIR, id, file);
  if (!f || !fs.existsSync(f)) return res.status(404).send("Not made for this story");
  res.download(f, `${niceName(id)}.${req.params.kind}`);
});

app.get("/api/stories/:id/pdf", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = await runStorymaker(req.user, ["--export-pdf", id]);
  if (!r.ok) return res.status(404).send(r.error || "Unknown story");
  const which = req.query.book === "1" && r.book ? r.book : r.pdf;
  res.download(which, `${niceName(id)}${which === r.book ? " (picture book)" : ""}.pdf`);
});

// The story's text by scene, for editing.
app.get("/api/stories/:id/full", (req, res) => {
  const id = guard(req, res); if (!id) return;
  try {
    const s = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, id, "story.json"), "utf8"));
    const r = readResult(id) || {};
    let used = {};
    try { used = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, id, ".stems", "voices.json"), "utf8")); } catch {}
    const usedVoices = (used.voices && used.voices.voices) || {};
    res.json({ ok: true, title: s.title, level: r.level, mode: r.mode, audioOutdated: !!r.audio_outdated,
      hasAudio: !!(r.audio_file || r.video_file), engine: used.engine || null, request: r.request || null,
      keywords: (s.keywords || []).join(", "), soundWishes: s.sound_wishes || "",
      voiceChoice: s.voice_choice || { narrator: "", characters: {} },
      cast: (s.cast || []).map((c) => ({ name: c.name, type: c.voice, confirmed: !!c.confirmed,
        voice: (usedVoices[String(c.name).toLowerCase()] || {}).voice || "" })),
      scenes: s.scenes.map((sc, i) => ({ index: i, chapter: sc.chapter || null, chapterTitle: sc.chapter_title || null,
        heading: sc.heading || "", narration: sc.narration, sound: sc.sound || null })) });
  } catch { res.status(404).json({ ok: false, error: "This story can't be edited." }); }
});

app.post("/api/stories/:id/edit", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const scenes = {};
  for (const [k, v] of Object.entries(b.scenes || {})) {
    if (/^\d{1,4}$/.test(k) && typeof v === "string") scenes[k] = v.slice(0, 40000);
  }
  const payload = { scenes, ...(typeof b.title === "string" ? { title: b.title.slice(0, 120) } : {}),
    ...(typeof b.characters === "string" ? { characters: b.characters.slice(0, 6000) } : {}) };
  res.json(await runStorymaker(req.user, ["--edit", id], JSON.stringify(payload)));
});

function startJob(req, kind, id, r) {
  if (r.ok && r.job_id) db.prepare("INSERT OR REPLACE INTO jobs (id, user_id, kind, story_id) VALUES (?, ?, ?, ?)").run(r.job_id, req.user.id, kind, id);
  return r;
}
app.post("/api/stories/:id/rewrite", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const target = /^(story|chapter:\d{1,3}|scene:\d{1,4})$/.test(b.target || "") ? b.target : "story";
  const args = ["--rewrite", id, "--target", target, "--background", ...(b.remake === false ? ["--no-remake"] : []),
    ...(typeof b.keywords === "string" ? ["--keywords", b.keywords.slice(0, 600)] : [])];
  res.json(startJob(req, "rewrite", id, await runStorymaker(req.user, args, String(b.instructions || "").slice(0, 2000))));
});
app.post("/api/stories/:id/remaster", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const args = ["--remaster", id, "--background", ...(b.revoice ? ["--revoice"] : []), ...(b.redesign ? ["--redesign"] : [])];
  res.json(startJob(req, "remaster", id, await runStorymaker(req.user, args)));
});

app.post("/api/stories/:id/voices", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const characters = {};
  for (const [name, spec] of Object.entries(b.characters || {})) if (VOICE_SPEC.test(spec || "")) characters[String(name).slice(0, 60)] = spec;
  const payload = { narrator: VOICE_SPEC.test(b.narrator || "") ? b.narrator : "", characters };
  const r = await runStorymaker(req.user, ["--set-voices", id], JSON.stringify(payload));
  if (!r.ok) return res.json(r);
  res.json(startJob(req, "remaster", id, await runStorymaker(req.user, ["--remaster", id, "--background"])));
});
// Tag who is a woman, man, girl, boy… (locked from then on), then remake the audio to match.
const VOICE_TYPES = ["female", "male", "girl", "boy", "old_woman", "old_man", "creature"];
app.post("/api/stories/:id/cast", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const cast = (Array.isArray((req.body || {}).cast) ? req.body.cast : []).slice(0, 60)
    .filter((c) => c && typeof c.name === "string" && VOICE_TYPES.includes(c.voice))
    .map((c) => ({ name: c.name.slice(0, 60), voice: c.voice }));
  const r = await runStorymaker(req.user, ["--set-cast", id], JSON.stringify({ cast }));
  if (!r.ok) return res.json(r);
  const rr = readResult(id) || {};
  if (req.body.remake === false || !(rr.audio_file || rr.video_file)) return res.json({ ok: true });
  res.json(startJob(req, "remaster", id, await runStorymaker(req.user, ["--remaster", id, "--background"])));
});

app.post("/api/stories/:id/sounds", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const scenes = {};
  for (const [k, plan] of Object.entries(b.scenes || {})) {
    if (!/^\d{1,4}$/.test(k) || typeof plan !== "object" || !plan) continue;
    scenes[k] = { ambience: String(plan.ambience || "").slice(0, 80),
      effects: (Array.isArray(plan.effects) ? plan.effects : []).slice(0, 6)
        .map((f) => ({ sound: String((f && f.sound) || "").slice(0, 60), at: String((f && f.at) || "").slice(0, 120) })) };
  }
  const payload = { wishes: String(b.wishes || "").slice(0, 300), ...(b.redesign ? {} : { scenes }) };
  const r = await runStorymaker(req.user, ["--set-sounds", id], JSON.stringify(payload));
  if (!r.ok) return res.json(r);
  const args = ["--remaster", id, "--background", ...(b.redesign ? ["--redesign"] : [])];
  res.json(startJob(req, "remaster", id, await runStorymaker(req.user, args)));
});

app.delete("/api/stories/:id", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = await runStorymaker(req.user, ["--delete", id]);
  if (r.ok) db.prepare("DELETE FROM stories WHERE id = ?").run(id);
  res.json(r);
});

app.get("/api/stories", (req, res) => {
  const names = Object.fromEntries(db.prepare("SELECT id, username FROM users").all().map((u) => [u.id, u.username]));
  const stories = [];
  for (const id of syncStories()) {
    if (!canSee(req.user, id)) continue;
    const r = readResult(id);
    const url = (name) => (fs.existsSync(path.join(OUTPUT_DIR, id, name)) ? `/media/${encodeURIComponent(id)}/${name}` : null);
    const by = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(id);
    stories.push({
      id, title: r.title, logline: r.logline, story: r.story, level: r.level,
      series: r.series, episode: r.episode, mode: r.mode, created: r.created || id.slice(0, 17),
      thumb: url("thumb.jpg"), video: url("story.mp4"), audio: url("story.mp3") || url("story.m4a"),
      book: url("story-book.pdf"), subtitles: url("story.vtt"), note: r.note || null,
      audiobook: url("story.m4b"), audioOutdated: !!r.audio_outdated, credits: url("sound-credits.txt"),
      request: r.request || null,
      by: req.user.role === "owner" && by && by.user_id !== req.user.id ? names[by.user_id] : null,
    });
  }
  stories.sort((a, b) => (a.id < b.id ? 1 : -1));
  res.json({ ok: true, stories });
});

app.listen(PORT, () => console.log(`StoryMaker web page on http://localhost:${PORT}`));
