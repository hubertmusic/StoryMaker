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
// Your time zone (TZ= in storymaker.env), so "tonight" and the times on the page are yours, not the server's.
if (!process.env.TZ && envFileValue("TZ")) process.env.TZ = envFileValue("TZ");
const STARTED = Date.now();
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
// The database and the stories being made can live on a local disk (DB_PATH, JOBS_DIR) while the library itself
// (OUTPUT_DIR) is on a NAS: SQLite and busy work folders don't belong on a network share.
const JOBS_DIR = process.env.JOBS_DIR || path.join(OUTPUT_DIR, ".jobs");
fs.mkdirSync(JOBS_DIR, { recursive: true });
const DB_PATH = process.env.DB_PATH || path.join(OUTPUT_DIR, ".storymaker.db");
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
const db = new DatabaseSync(DB_PATH);
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
// Where each person stopped listening (so a story picks up there on any device), and their private podcast feed.
db.exec(`
  CREATE TABLE IF NOT EXISTS positions (user_id INTEGER NOT NULL, story_id TEXT NOT NULL, pos REAL NOT NULL,
    dur REAL NOT NULL DEFAULT 0, updated INTEGER NOT NULL, PRIMARY KEY (user_id, story_id));
  CREATE TABLE IF NOT EXISTS feeds (user_id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE,
    explicit INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, body TEXT NOT NULL, title TEXT,
    created INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'waiting', job_id TEXT, error TEXT, started INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0, next_try INTEGER NOT NULL DEFAULT 0, pos REAL NOT NULL DEFAULT 0);
`);
try { db.exec("ALTER TABLE users ADD COLUMN ntfy_topic TEXT"); } catch { /* already there */ }
try { db.exec("ALTER TABLE feeds ADD COLUMN studio INTEGER NOT NULL DEFAULT 1"); } catch { /* already there */ }
try { db.exec("ALTER TABLE feeds ADD COLUMN archive INTEGER NOT NULL DEFAULT 0"); } catch { /* already there */ }
try {   // jobs from before notifications existed are counted as told about, so turning them on doesn't flood the phone
  db.exec("ALTER TABLE jobs ADD COLUMN notified INTEGER NOT NULL DEFAULT 0");
  db.exec("UPDATE jobs SET notified = 1");
} catch { /* already there */ }
try { db.exec("ALTER TABLE jobs ADD COLUMN from_queue INTEGER NOT NULL DEFAULT 0"); } catch { /* already there */ }
const CUT_OFF = [];   // stories a restart cut off: they're picked up again by themselves once StoryMaker is ready
// StoryMaker just started, so nothing is being made yet: work that was cut off by a restart or an update shows
// as stopped (and Make it again carries on from where it was), not as running forever.
try {
  const jd = JOBS_DIR;
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

// ───────────────────────────── private podcast feed ─────────────────────────────
// A secret address per person: podcast apps can't sign in, so the long random token in the address is the key.
// It's checked on the feed and on every file it points to; "New address" makes the old one stop working.
function feedUser(token) {
  if (!/^[a-f0-9]{48}$/.test(token || "")) return null;
  const f = db.prepare("SELECT * FROM feeds WHERE token = ?").get(token);
  const u = f && db.prepare("SELECT * FROM users WHERE id = ?").get(f.user_id);
  return u ? { user: u, feed: f } : null;
}
function feedFile(id) {        // what a podcast app plays: the MP3 (with chapters), else the video
  for (const [name, type] of [["story.mp3", "audio/mpeg"], ["story.m4a", "audio/mp4"], ["story.mp4", "video/mp4"]]) {
    const f = path.join(OUTPUT_DIR, id, name);
    if (fs.existsSync(f)) return { file: f, name, type, size: fs.statSync(f).size };
  }
  return null;
}
function publicBase(req) {
  const set = process.env.PUBLIC_URL || envFileValue("PUBLIC_URL");
  if (set) return set.replace(/\/+$/, "");
  const proto = String(req.headers["x-forwarded-proto"] || req.protocol).split(",")[0].trim();
  return `${proto}://${req.headers["x-forwarded-host"] || req.get("host")}`;
}
const xml = (t) => String(t == null ? "" : t).replace(/[<>&'"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", '"': "&quot;" }[c]));
function idDate(id) {
  const m = String(id).match(/^(\d{4})-(\d\d)-(\d\d)_(\d\d)(\d\d)(\d\d)/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : new Date();
}
// Besides the library, the feed can carry Audio Studio's finished audiobooks and archived stories, both read straight
// from the NAS (NAS_ROOT: the share's top folder, /data/nas on the server), so they play even with the Mac asleep.
const NAS_ROOT = () => process.env.NAS_ROOT || envFileValue("NAS_ROOT") || "";
const ARCHIVED_DIR = () => NAS_ROOT() && path.join(NAS_ROOT(), "StoryMaker Archive", "storymaker");
const STUDIO_BOOKS_DIR = () => NAS_ROOT() && path.join(NAS_ROOT(), "StoryMaker Audiobooks");
const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch { return null; } };
const SAFE_DIR = /^[\w.-]{3,120}$/;
function mayHear(u, f, owner, level) {
  level = level || "family";
  if (u.role !== "owner" && String(owner) !== String(u.id)) return false;
  return allowedLevels(u).includes(level) && (level !== "explicit" || !!f.explicit);
}
const MEDIA = [["story.mp3", "audio/mpeg"], ["story.m4a", "audio/mp4"], ["story.m4b", "audio/mp4"], ["story.mp4", "video/mp4"]];
function mediaIn(dir, names = MEDIA) {
  for (const [name, type] of names) {
    const f = path.join(dir, name);
    if (fs.existsSync(f)) return { file: f, name, type, size: fs.statSync(f).size };
  }
  return null;
}
// Everything one person's feed holds: [{kind, id, dir, media, title, summary, level, at, thumb, episode}]
function feedItems(u, f) {
  const out = [];
  const library = new Set();
  for (const id of syncStories()) {
    if (!canSee(u, id)) continue;
    const r = readResult(id) || {};
    library.add(id);
    if (r.level === "explicit" && !f.explicit) continue;
    const media = feedFile(id);
    if (!media) continue;
    out.push({ kind: "s", id, dir: path.join(OUTPUT_DIR, id), media, title: r.title || id, level: r.level,
      summary: (r.series && r.episode ? `${r.series}, episode ${r.episode}. ` : "") + (r.logline || ""),
      episode: r.series && r.episode ? r.episode : 0, at: idDate(id), thumb: "thumb.jpg" });
  }
  if (f.studio && STUDIO_BOOKS_DIR()) {
    let names = []; try { names = fs.readdirSync(STUDIO_BOOKS_DIR()); } catch { /* not there yet */ }
    for (const id of names) {
      if (!SAFE_DIR.test(id)) continue;
      const dir = path.join(STUDIO_BOOKS_DIR(), id), b = readJson(path.join(dir, "info.json"));
      if (!b || !b.file || !mayHear(u, f, b.owner, b.level)) continue;
      // a book sent back into its StoryMaker story is already in the feed as that story
      if (b.sent_to_storymaker && b.source && b.source.type === "storymaker" && library.has(b.source.id)) continue;
      const media = mediaIn(dir, [[b.file, b.file.endsWith(".mp3") ? "audio/mpeg" : "audio/mp4"]]);
      if (!media) continue;
      out.push({ kind: "b", id, dir, media, title: b.title || id, level: b.level,
        summary: `Audiobook from Audio Studio${b.author ? `, by ${b.author}` : ""}.`, episode: 0,
        at: new Date(b.finished || b.created || Date.now()), thumb: b.cover ? "cover.jpg" : "" });
    }
  }
  if (f.archive && ARCHIVED_DIR()) {
    let names = []; try { names = fs.readdirSync(ARCHIVED_DIR()); } catch { /* none */ }
    for (const id of names) {
      if (!SAFE_DIR.test(id) || library.has(id)) continue;              // brought back: it's in the library
      const dir = path.join(ARCHIVED_DIR(), id), a = readJson(path.join(dir, "archive.json"));
      if (!a || !mayHear(u, f, a.owner, a.level)) continue;
      const r = readJson(path.join(dir, "result.json")) || {};
      const media = mediaIn(dir);
      if (!media) continue;
      out.push({ kind: "a", id, dir, media, title: a.title || r.title || id, level: a.level,
        summary: (r.series && r.episode ? `${r.series}, episode ${r.episode}. ` : "") + (a.logline || r.logline || ""),
        episode: r.series && r.episode ? r.episode : 0, at: idDate(id), thumb: fs.existsSync(path.join(dir, "thumb.jpg")) ? "thumb.jpg" : "" });
    }
  }
  return out.sort((a, b) => b.at - a.at);
}
app.get("/feed/:token.xml", (req, res) => {
  const fu = feedUser(req.params.token);
  if (!fu) return res.sendStatus(404);
  const base = `${publicBase(req)}/feed/${fu.feed.token}`;
  const items = feedItems(fu.user, fu.feed);
  const at = (it, file) => `${base}/${it.kind}/${encodeURIComponent(it.id)}/${file}`;
  const body = items.map((it) => {
    const thumb = it.thumb && fs.existsSync(path.join(it.dir, it.thumb)) ? at(it, it.thumb) : "";
    return `    <item>
      <title>${xml(it.title)}</title>
      <description>${xml(it.summary)}</description>
      <itunes:summary>${xml(it.summary)}</itunes:summary>
      <guid isPermaLink="false">${xml(`${it.kind === "s" ? "" : it.kind + ":"}${it.id}`)}</guid>
      <pubDate>${it.at.toUTCString()}</pubDate>
      <enclosure url="${xml(at(it, `play${path.extname(it.media.name)}`))}" length="${it.media.size}" type="${it.media.type}"/>
${thumb ? `      <itunes:image href="${xml(thumb)}"/>\n` : ""}${it.episode ? `      <itunes:episode>${parseInt(it.episode, 10) || 1}</itunes:episode>\n` : ""}      <itunes:explicit>${["mature", "explicit"].includes(it.level) ? "true" : "false"}</itunes:explicit>
    </item>`;
  }).join("\n");
  const first = items.find((it) => it.thumb && fs.existsSync(path.join(it.dir, it.thumb)));
  const cover = first ? at(first, first.thumb) : "";
  res.type("application/rss+xml").set("Cache-Control", "no-cache").send(`<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>${xml(`StoryMaker (${fu.user.username})`)}</title>
    <description>Your stories and audiobooks from StoryMaker, private to you.</description>
    <link>${xml(publicBase(req))}</link>
    <language>en</language>
    <itunes:author>StoryMaker</itunes:author>
    <itunes:block>Yes</itunes:block>
    <itunes:explicit>false</itunes:explicit>
${cover ? `    <itunes:image href="${xml(cover)}"/>\n    <image><url>${xml(cover)}</url><title>StoryMaker</title><link>${xml(publicBase(req))}</link></image>\n` : ""}${body}
  </channel>
</rss>
`);
});
// A file from the feed: s = a library story, b = a studio audiobook, a = an archived story (checked like the feed)
app.get("/feed/:token/:kind/:id/:file", (req, res) => {
  const fu = feedUser(req.params.token);
  const { kind, id, file } = req.params;
  if (!fu || !["s", "b", "a"].includes(kind) || !SAFE_DIR.test(id)) return res.sendStatus(404);
  const it = feedItems(fu.user, fu.feed).find((x) => x.kind === kind && x.id === id);
  if (!it) return res.sendStatus(404);
  if (file === "thumb.jpg" || file === "cover.jpg") {
    const t = path.join(it.dir, file);
    return file === it.thumb && fs.existsSync(t) ? res.sendFile(t) : res.sendStatus(404);
  }
  if (file !== `play${path.extname(it.media.name)}`) return res.sendStatus(404);
  res.sendFile(it.media.file, { headers: { "Content-Type": it.media.type } });   // supports skipping (Range)
});
// (addresses from before the audiobooks and archive came along still work)
app.get("/feed/:token/:id/:file", (req, res) => res.redirect(301, `/feed/${req.params.token}/s/${encodeURIComponent(req.params.id)}/${req.params.file}`));

// The sign-in page and what it needs are open; everything else needs a signed-in person.
const OPEN = /^\/(login\.html|login\.js|style\.css|fonts\/[\w.-]+|manifest\.webmanifest|icon-\d+\.png|apple-touch-icon\.png)$/;
app.use((req, res, next) => {
  if (OPEN.test(req.path)) return next();
  req.user = currentUser(req);
  if (req.user) { if (req.path === "/" || req.path === "/api/me") lastBase = publicBase(req); return next(); }
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
const PICTURE_STYLES = ["storybook", "realistic", "cinematic", "3d", "epic"];
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
  if (b.bedtime) args.push("--bedtime");
  const bm = parseInt(b.bigMoments, 10);
  if (bm >= 1 && bm <= 5 && !b.adventure) args.push("--big-moments", String(bm));
  if (b.adventure) {                     // a choose-your-own-adventure: read aloud, but no video
    args.push("--adventure");
    const i = args.indexOf("--mode");
    if (i >= 0 && args[i + 1] === "video") args[i + 1] = "audio";
    const si = args.indexOf("--series");
    if (si >= 0) args.splice(si, 2);
  }
  if (PICTURE_STYLES.includes(b.pictureStyle)) args.push("--picture-style", b.pictureStyle);
  if (b.useStyle === false) args.push("--no-style");
  if (b.proofread === false) args.push("--no-proofread");
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
const VOICE_SPEC = /^[abefhijpz][fm]_[a-z]+(\+[abefhijpz][fm]_[a-z]+)?(\^(0\.[89]\d?|1\.[01]\d?))?$/;
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

async function startMake(user, body, fromQueue = false) {
  const e = levelError(user, body);
  if (e) return { ok: false, error: e };
  if (!body.series && !idea(body).trim()) return { ok: false, error: "Type what the story should be about first." };
  const r = await runStorymaker(user, [...buildArgs(body), "--yes", "--background"], idea(body));
  if (r.ok && r.job_id) {
    // everything needed to carry on later from any device, if it's ever cut off
    const info = JSON.stringify({ mode: body.mode || "story", book: !!body.book, idea: idea(body).slice(0, 140), body });
    db.prepare("INSERT OR REPLACE INTO jobs (id, user_id, kind, info, from_queue) VALUES (?, ?, 'make', ?, ?)")
      .run(r.job_id, user.id, info, fromQueue ? 1 : 0);
  }
  return r;
}
app.post("/api/make", async (req, res) => res.json(await startMake(req.user, req.body || {})));

// ───────────────────────────── the overnight queue ─────────────────────────────
// Stories added for tonight wait until the night hours (QUEUE_START to QUEUE_END in storymaker.env, 11pm to 6:30am
// unless you change them), then they're made one after another while the Mac is free.
const hm = (v, d) => { const m = String(v || d).match(/^(\d{1,2}):(\d{2})$/); return m ? +m[1] * 60 + +m[2] : hm(d, d); };
function nightWindow() {
  return { start: hm(envFileValue("QUEUE_START"), "23:00"), end: hm(envFileValue("QUEUE_END"), "06:30") };
}
function inNight(d = new Date()) {
  const { start, end } = nightWindow(), t = d.getHours() * 60 + d.getMinutes();
  return start <= end ? t >= start && t < end : t >= start || t < end;
}
function nextNight(d = new Date()) {     // when tonight's run begins (now, if it's already night)
  if (inNight(d)) return d;
  const { start } = nightWindow(), n = new Date(d);
  n.setHours(Math.floor(start / 60), start % 60, 0, 0);
  if (n <= d) n.setDate(n.getDate() + 1);
  return n;
}
const fmtHm = (mins) => { const h = Math.floor(mins / 60), m = mins % 60; return `${(h % 12) || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`; };
function busyJobs() {        // anything being made right now (by anyone)
  const out = [];
  let names = [];
  try { names = fs.readdirSync(JOBS_DIR).filter((n) => n.endsWith(".status.json")); } catch { /* none */ }
  for (const n of names) {
    const f = path.join(JOBS_DIR, n);
    try {
      if (Date.now() - fs.statSync(f).mtimeMs > 60 * 60 * 1000) continue;
      const st = JSON.parse(fs.readFileSync(f, "utf8"));
      if (st.state === "running" || st.state === "queued") out.push({ id: n.replace(".status.json", ""), st });
    } catch { /* being written */ }
  }
  return out;
}
function queueRow(q, u) {
  let b = {};
  try { b = JSON.parse(q.body); } catch { /* bad row */ }
  return { id: q.id, title: q.title, state: q.state, error: q.error, jobId: q.job_id, attempts: q.attempts,
    mode: b.mode || "video", level: b.level || "", series: b.series || "", chapters: parseInt(b.chapters, 10) || 1,
    bedtime: !!b.bedtime, created: q.created, started: q.started,
    by: u && u.role === "owner" && q.user_id !== u.id ? (db.prepare("SELECT username FROM users WHERE id = ?").get(q.user_id) || {}).username : null };
}
app.get("/api/queue", (req, res) => {
  const mine = req.user.role === "owner" ? "" : " AND user_id = ?";
  const rows = db.prepare(`SELECT * FROM queue WHERE (state = 'waiting' OR (state IN ('started', 'failed') AND created > ?))${mine} ORDER BY state = 'waiting' DESC, pos, id`)
    .all(...[Date.now() - 2 * 864e5, ...(mine ? [req.user.id] : [])]);
  const w = nightWindow();
  res.json({ ok: true, items: rows.map((q) => queueRow(q, req.user)), night: inNight(), from: fmtHm(w.start), to: fmtHm(w.end),
    next: nextNight().toISOString() });
});
app.post("/api/queue", (req, res) => {
  const b = req.body || {};
  const e = levelError(req.user, b);
  if (e) return res.json({ ok: false, error: e });
  if (!b.series && !idea(b).trim()) return res.json({ ok: false, error: "Type what the story should be about first." });
  const n = db.prepare("SELECT COUNT(*) AS n FROM queue WHERE state = 'waiting'").get().n;
  if (n >= 20) return res.json({ ok: false, error: "Tonight's list is full (20 stories)." });
  const title = b.series ? `Next episode of ${b.series}${idea(b).trim() ? `: ${idea(b).trim()}` : ""}` : idea(b).trim();
  const pos = (db.prepare("SELECT MAX(pos) AS p FROM queue").get().p || 0) + 1;
  db.prepare("INSERT INTO queue (user_id, body, title, created, pos) VALUES (?, ?, ?, ?, ?)")
    .run(req.user.id, JSON.stringify(b), title.slice(0, 160), Date.now(), pos);
  const w = nightWindow();
  res.json({ ok: true, waiting: n + 1, night: inNight(), from: fmtHm(w.start), to: fmtHm(w.end) });
});
function ownQueue(u, id) {
  const q = db.prepare("SELECT * FROM queue WHERE id = ?").get(parseInt(id, 10) || 0);
  return q && (q.user_id === u.id || u.role === "owner") ? q : null;
}
app.delete("/api/queue/:id", (req, res) => {
  const q = ownQueue(req.user, req.params.id);
  if (!q) return res.json({ ok: false, error: "It's not on the list." });
  db.prepare("DELETE FROM queue WHERE id = ?").run(q.id);
  res.json({ ok: true });
});
app.post("/api/queue/:id/move", (req, res) => {          // {dir: -1 | 1}
  const q = ownQueue(req.user, req.params.id);
  if (!q || q.state !== "waiting") return res.json({ ok: false, error: "It's not waiting." });
  const rows = db.prepare("SELECT id, pos FROM queue WHERE state = 'waiting' ORDER BY pos, id").all();
  const i = rows.findIndex((r) => r.id === q.id), j = i + (req.body && req.body.dir < 0 ? -1 : 1);
  if (j >= 0 && j < rows.length) {
    rows.forEach((r, k) => db.prepare("UPDATE queue SET pos = ? WHERE id = ?").run(k, r.id));
    db.prepare("UPDATE queue SET pos = ? WHERE id = ?").run(j, rows[i].id);
    db.prepare("UPDATE queue SET pos = ? WHERE id = ?").run(i, rows[j].id);
  }
  res.json({ ok: true });
});
app.post("/api/queue/:id/now", async (req, res) => {      // don't wait for tonight
  const q = ownQueue(req.user, req.params.id);
  if (!q || q.state !== "waiting") return res.json({ ok: false, error: "It's not waiting." });
  res.json(await startQueued(q));
});
async function startQueued(q) {
  const u = db.prepare("SELECT * FROM users WHERE id = ?").get(q.user_id);
  if (!u) { db.prepare("DELETE FROM queue WHERE id = ?").run(q.id); return { ok: false, error: "Unknown person." }; }
  db.prepare("UPDATE queue SET state = 'starting', started = ? WHERE id = ?").run(Date.now(), q.id);
  let r;
  try { r = await startMake(u, JSON.parse(q.body), true); } catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (r.ok && r.job_id) {
    db.prepare("UPDATE queue SET state = 'started', job_id = ?, error = NULL WHERE id = ?").run(r.job_id, q.id);
  } else if (q.attempts + 1 < 3) {      // the Mac asleep, Draw Things closed…: try again in half an hour
    db.prepare("UPDATE queue SET state = 'waiting', attempts = attempts + 1, next_try = ?, error = ? WHERE id = ?")
      .run(Date.now() + 30 * 60 * 1000, String(r.error || "Couldn't start").slice(0, 400), q.id);
  } else {
    db.prepare("UPDATE queue SET state = 'failed', error = ? WHERE id = ?").run(String(r.error || "Couldn't start").slice(0, 400), q.id);
    notify(u, "Tonight's story couldn't start", `${q.title.slice(0, 80)}: ${String(r.error || "").slice(0, 200)}`, "warning");
  }
  return r;
}
let queueTurning = false;
async function queueTick() {
  if (queueTurning) return;
  queueTurning = true;
  try {
    db.prepare("UPDATE queue SET state = 'waiting' WHERE state = 'starting' AND started < ?").run(Date.now() - 10 * 60 * 1000);
    if (!inNight() || busyJobs().length) return;
    const q = db.prepare("SELECT * FROM queue WHERE state = 'waiting' AND next_try <= ? ORDER BY pos, id LIMIT 1").get(Date.now());
    if (q) await startQueued(q);
  } finally { queueTurning = false; }
}
setInterval(() => { queueTick().catch((e) => console.error("queue:", e)); }, 30 * 1000);

// ───────────────────────────── phone notifications (ntfy) ─────────────────────────────
// Free: install the ntfy app and subscribe to your private topic. Sent when a story is ready or something went wrong.
const NTFY_URL = () => (envFileValue("NTFY_URL") || "https://ntfy.sh").replace(/\/+$/, "");
let lastBase = "";                 // the address people use for StoryMaker, for the links in notifications
async function notify(u, title, message, tag = "books", link = "") {
  if (!u || !u.ntfy_topic) return false;
  try {
    const r = await fetch(NTFY_URL(), { method: "POST", headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(15000),
      body: JSON.stringify({ topic: u.ntfy_topic, title: title.slice(0, 120), message: message.slice(0, 1000), tags: [tag],
        ...(link ? { click: link } : {}) }) });
    return r.ok;
  } catch (e) { console.error("notification:", e.message); return false; }
}
const KIND_DONE = { make: "is ready", rewrite: "was rewritten", cleanup: "was cleaned up", remaster: "has new audio",
  portraits: "portraits are drawn" };
function notifyTick() {
  const rows = db.prepare("SELECT * FROM jobs WHERE notified = 0 AND created > datetime('now', '-3 days')").all();
  for (const j of rows) {
    let st;
    try { st = JSON.parse(fs.readFileSync(path.join(JOBS_DIR, `${j.id}.status.json`), "utf8")); } catch { continue; }
    if (!["done", "failed", "stopped", "paused"].includes(st.state)) continue;
    db.prepare("UPDATE jobs SET notified = 1 WHERE id = ?").run(j.id);
    const u = db.prepare("SELECT * FROM users WHERE id = ?").get(j.user_id);
    if (!u || !u.ntfy_topic || st.state === "stopped" || st.state === "paused") continue;
    const took = Date.now() - Date.parse(`${j.created.replace(" ", "T")}Z`);
    if (j.kind !== "make" && !j.from_queue && took < 3 * 60 * 1000) continue;      // quick jobs: you were watching
    const runId = (st.result && st.result.run_id) || j.story_id;
    const r = runId ? readResult(runId) || {} : {};
    const secret = r.level === "explicit";                     // a lock screen shows it: no title for these
    const name = secret ? "Your story" : (r.title || st.title || "Your story");
    const link = lastBase && runId ? `${lastBase}/#story=${encodeURIComponent(runId)}` : lastBase;
    if (st.state === "done") {
      notify(u, `${name} ${KIND_DONE[j.kind] || "is done"}`, secret ? "Open StoryMaker to see it." :
        (r.logline || "Open StoryMaker to watch or listen.").slice(0, 300), j.kind === "make" ? "tada" : "books", link);
    } else {
      notify(u, `${secret ? "A story" : name} stopped with a problem`,
        `${String(st.error || "Something went wrong").slice(0, 240).replace(/[.!?]?\s*$/, ".")} What's done is kept: Recover carries on.`, "warning", lastBase);
    }
  }
}
setInterval(() => { try { notifyTick(); } catch (e) { console.error("notify:", e); } }, 20 * 1000);
function syncStudioNotify() {           // Audio Studio tells the owner's phone too, using the same topic
  const o = owner();
  return helper("POST", "/api/helper/notify", { url: NTFY_URL(), topic: (o && o.ntfy_topic) || "" });
}
setTimeout(() => { syncStudioNotify().catch(() => {}); }, 20 * 1000);
setInterval(() => { syncStudioNotify().catch(() => {}); }, 30 * 60 * 1000);
app.get("/api/notify", (req, res) => {
  const t = req.user.ntfy_topic;
  res.json({ ok: true, on: !!t, topic: t || null, server: NTFY_URL(),
    subscribe: t ? `${NTFY_URL()}/${t}` : null });
});
app.post("/api/notify", async (req, res) => {
  const b = req.body || {};
  if (b.on === true && !req.user.ntfy_topic) {
    const topic = `storymaker-${crypto.randomBytes(9).toString("hex")}`;
    db.prepare("UPDATE users SET ntfy_topic = ? WHERE id = ?").run(topic, req.user.id);
  } else if (b.on === false) {
    db.prepare("UPDATE users SET ntfy_topic = NULL WHERE id = ?").run(req.user.id);
  }
  const u = db.prepare("SELECT * FROM users WHERE id = ?").get(req.user.id);
  let studio = null;
  if (u.role === "owner" && b.on !== undefined) studio = await syncStudioNotify();
  let sent = null;
  if (b.test && u.ntfy_topic) {
    sent = await notify(u, "StoryMaker", "Notifications are working. You'll hear when a story is ready.", "tada", lastBase);
    if (u.role === "owner") helper("POST", "/api/helper/notify", { url: NTFY_URL(), topic: u.ntfy_topic, test: true }).catch(() => {});
  }
  res.json({ ok: true, on: !!u.ntfy_topic, topic: u.ntfy_topic || null, server: NTFY_URL(),
    subscribe: u.ntfy_topic ? `${NTFY_URL()}/${u.ntfy_topic}` : null, sent, studio: studio ? !!studio.ok : null });
});

function ownJob(u, id) {
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  return j && (j.user_id === u.id || u.role === "owner");
}
app.get("/api/status/:id", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  if (!ownJob(req.user, id)) return res.json({ ok: false, error: "Unknown job" });
  const f = path.join(JOBS_DIR, `${id}.status.json`);
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
    const f = path.join(JOBS_DIR, `${j.id}.status.json`);
    let st, age;
    try { st = JSON.parse(fs.readFileSync(f, "utf8")); age = Date.now() - fs.statSync(f).mtimeMs; } catch { continue; }
    const live = (st.state === "running" || st.state === "queued") && age < 60 * 60 * 1000;
    const justDone = ["done", "failed", "stopped", "paused"].includes(st.state) && age < 10 * 60 * 1000;
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
function jobFolder(id) { return path.join(JOBS_DIR, id); }
function jobScenes(id) {
  let names = [];
  try { names = fs.readdirSync(jobFolder(id)).filter((n) => /^scene_c\d+_s\d+\.txt$/.test(n)).sort(); } catch { /* none */ }
  return names;
}
function jobOutline(id) {
  try { return JSON.parse(fs.readFileSync(path.join(jobFolder(id), "outline.json"), "utf8")); } catch { /* none */ }
  try {                                    // a story that was fully written before it stopped (voices, video…)
    const st = JSON.parse(fs.readFileSync(path.join(jobFolder(id), "story.json"), "utf8"));
    return { title: st.title, logline: st.logline, written: (st.scenes || []).length,
      chapters: [{ scenes: st.scenes || [] }], scenesText: (st.scenes || []).map((x) => x.narration || "") };
  } catch { return null; }
}
app.get("/api/jobs/unfinished", (req, res) => {
  const rows = db.prepare("SELECT * FROM jobs WHERE user_id = ? AND kind = 'make' AND created > datetime('now', '-60 days') "
    + "ORDER BY created DESC").all(req.user.id);
  const out = [];
  for (const j of rows) {
    const f = path.join(JOBS_DIR, `${j.id}.status.json`);
    let st = {}, age = 0;
    try { st = JSON.parse(fs.readFileSync(f, "utf8")); age = Date.now() - fs.statSync(f).mtimeMs; } catch { continue; }
    const stale = (st.state === "running" || st.state === "queued") && age > 60 * 60 * 1000;
    if (st.state !== "failed" && st.state !== "stopped" && st.state !== "paused" && !stale) continue;
    const scenes = jobScenes(j.id), outline = jobOutline(j.id);
    if (!scenes.length && !outline) continue;              // nothing was written: nothing to find
    let info = {};
    try { info = JSON.parse(j.info || "{}"); } catch { /* old row */ }
    const planned = outline && Array.isArray(outline.chapters)
      ? outline.chapters.reduce((n, c) => n + ((c.scenes || []).length || 0), 0) : 0;
    out.push({ id: j.id, title: (outline && outline.title) || st.title || "Untitled story", idea: info.idea || "",
      level: (info.body && info.body.level) || null, started: j.created,
      written: scenes.length || (outline && outline.written) || 0, planned,
      error: stale ? "It was cut off (StoryMaker or the Mac restarted)." : st.state === "stopped" ? "You stopped it." : st.state === "paused" ? "Paused." : (st.error || ""),
      paused: st.state === "paused",
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
  if (!jobScenes(id).length && outline.scenesText) for (const t of outline.scenesText) parts.push(t.trim(), "");
  for (const name of jobScenes(id)) {
    const [, c] = name.match(/^scene_c(\d+)_s(\d+)/).map(Number);
    if (c !== lastCh && chapters.length > 1) {
      parts.push(`## Chapter ${c}: ${(chapters[c - 1] || {}).title || ""}`, "");
      lastCh = c;
    }
    try { parts.push(fs.readFileSync(path.join(jobFolder(id), name), "utf8").trim(), ""); } catch { /* skip */ }
  }
  res.json({ ok: true, text: parts.join("\n"), written: jobScenes(id).length || outline.written || 0 });
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
    if (!j || !u || !jobOutline(id)) continue;
    const r = await recoverJob(u, j);
    console.log(`Carrying on story ${id} after the restart: ${r.ok ? "started" : r.error}`);
  }
}, Number(process.env.RECOVER_DELAY_SECONDS || 300) * 1000);   // not straight away: the Mac may still be starting up

// Stop: anything being made (a story, a remake, a clean-up, a rewrite). The background copy of StoryMaker that's
// doing it is asked to stop (it saves "stopped" and keeps what it made so far); if it hasn't stopped in 20 seconds
// it, and everything it started (voices, ffmpeg), is ended.
function jobPid(id, st) {
  if (st && Number.isInteger(st.pid) && st.pid > 1) return st.pid;
  try { return parseInt(fs.readFileSync(path.join(jobFolder(id), "running.pid"), "utf8"), 10) || null; } catch { return null; }
}
function isStorymaker(pid) {
  try { return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").includes("storymaker"); } catch { return false; }
}
function alive(pid) { try { process.kill(pid, 0); return true; } catch { return false; } }
function markStopped(f, st, why) {
  const pauseFlag = f.replace(/\.status\.json$/, ".pause");
  const paused = fs.existsSync(pauseFlag);
  try { fs.unlinkSync(pauseFlag); } catch { /* none */ }
  if (paused) why = "Paused. Everything made so far is kept: press Resume in Unfinished stories to carry on from here.";
  try {
    const now = JSON.parse(fs.readFileSync(f, "utf8"));
    if (now.state === "running" || now.state === "queued") {
      fs.writeFileSync(f, JSON.stringify({ ...now, state: paused ? "paused" : "stopped", detail: why, error: null }));
    }
  } catch { if (st) fs.writeFileSync(f, JSON.stringify({ ...st, state: "stopped", detail: why })); }
}
app.post("/api/jobs/:id/pause", (req, res) => {
  // Pause = Stop that says "paused": the story waits in Unfinished stories with Resume (only for new stories)
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  if (!id || !ownJob(req.user, id)) return res.status(404).json({ ok: false, error: "Not found." });
  stopJob(req, res, true);
});
app.post("/api/jobs/:id/stop", (req, res) => stopJob(req, res, false));
function stopJob(req, res, pause) {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  if (!id || !ownJob(req.user, id)) return res.status(404).json({ ok: false, error: "Not found." });
  const f = path.join(JOBS_DIR, `${id}.status.json`);
  let st = null;
  try { st = JSON.parse(fs.readFileSync(f, "utf8")); } catch { return res.json({ ok: false, error: "That isn't running." }); }
  if (st.state !== "running" && st.state !== "queued") return res.json({ ok: true, state: st.state });
  if (pause) { try { fs.writeFileSync(path.join(JOBS_DIR, `${id}.pause`), "pause"); } catch { /* stops anyway */ } }
  const wrote = !!jobOutline(id) || jobScenes(id).length > 0;
  const why = st.kind && st.kind !== "make" ? "Stopped." : wrote
    ? "Stopped. What was already made is kept: carry it on from Unfinished stories, or delete it there."
    : "Stopped before anything was written. Nothing was kept or spent.";
  const pid = jobPid(id, st);
  if (!pid || !alive(pid) || !isStorymaker(pid)) {         // nothing is actually running it any more
    markStopped(f, st, why);
    return res.json({ ok: true, state: "stopped" });
  }
  const signal = (sig) => { try { process.kill(-pid, sig); } catch { try { process.kill(pid, sig); } catch { /* gone */ } } };
  signal("SIGTERM");
  let waited = 0;
  const check = setInterval(() => {
    waited += 1;
    if (!alive(pid) || waited >= 20) {
      clearInterval(check);
      if (alive(pid)) signal("SIGKILL");
      markStopped(f, st, why);
    }
  }, 1000);
  res.json({ ok: true, state: "stopping" });
}

app.delete("/api/jobs/:id", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const j = db.prepare("SELECT * FROM jobs WHERE id = ?").get(id);
  if (!j || j.user_id !== req.user.id) return res.status(404).json({ ok: false, error: "Not found." });
  fs.rmSync(jobFolder(id), { recursive: true, force: true });
  fs.rmSync(path.join(JOBS_DIR, `${id}.status.json`), { force: true });
  db.prepare("DELETE FROM jobs WHERE id = ?").run(id);
  res.json({ ok: true });
});

// Preview pictures of a story that's still being made (small JPEGs in its work folder).
app.get("/api/status/:id/preview/:name", (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9]/g, "");
  const name = String(req.params.name);
  if (!id || !ownJob(req.user, id) || !/^preview_[\w-]+\.jpg$/.test(name)) return res.sendStatus(404);
  const f = path.join(JOBS_DIR, id, name);
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
    localPictures: !!(pyFeatures && pyFeatures.local_pictures), studioUrl: process.env.AUDIO_STUDIO_URL || envFileValue("AUDIO_STUDIO_URL") || "" });
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

// The grown-up switch on its own (used by Audio Studio too): hiding Mature and Explicit never needs a password;
// showing them again needs the owner's password.
const switchTries = new Map();
app.post("/api/me/adult", (req, res) => {
  const u = req.user, b = req.body || {};
  if (u.role !== "owner") return res.status(403).json({ ok: false, error: "Only the owner can change this." });
  const on = b.on === true;
  if (on) {
    const t = switchTries.get(u.id) || { n: 0, at: 0 };
    if (t.n >= 5 && Date.now() - t.at < 10 * 60 * 1000) return res.json({ ok: false, error: "Too many wrong passwords. Try again in 10 minutes." });
    if (!passwordOk(u, b.current)) {
      switchTries.set(u.id, { n: t.n + 1, at: Date.now() });
      return res.json({ ok: false, error: "That password isn't right." });
    }
    switchTries.delete(u.id);
  }
  db.prepare("UPDATE users SET adult_switch = ? WHERE id = ?").run(on ? 1 : 0, u.id);
  res.json({ ok: true, adultSwitch: on });
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

// ───────────────────────────── a series' bible ─────────────────────────────
// Its characters (name, kind, age, look, outfit, voice), look, picture style and narrator, used for every new
// episode; plus what the writer remembers (each episode's recap, where things stand, open threads).
const SERIES_DIR = () => path.join(OUTPUT_DIR, "series");
const BIBLE_TYPES = ["female", "male", "girl", "boy", "old_woman", "old_man", "creature"];
const BIBLE_STYLES = ["storybook", "realistic", "cinematic", "3d", "epic"];
const VOICE_OK = /^[a-z]{2}_[a-z0-9]{2,20}(\^[+-]?\d{1,2})?$/;
function seriesBySlug(u, slug) {
  slug = String(slug || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  if (!slug) return null;
  const f = path.join(SERIES_DIR(), slug, "series.json");
  let sr;
  try { sr = JSON.parse(fs.readFileSync(f, "utf8")); } catch { return null; }
  if (!visibleSeries(u).has(sr.name)) return null;
  return { slug, file: f, series: sr };
}
function bibleFromSeries(sr) {        // the first bible: the characters as the writer described them
  const types = Object.fromEntries((sr.cast || []).map((c) => [String(c.name || "").toLowerCase(), c.voice]));
  return String(sr.characters || "").split(/;\s*/).map((part) => {
    part = part.trim();
    if (!part) return null;
    // the name ends at the first "(", ":", "," or spaced dash: "Mira, a curious girl…", "Tom (60s, …)"
    const cut = part.match(/\s*(?:\(|:|,|\s[—–-]\s)\s*/);
    const m = [null, cut ? part.slice(0, cut.index) : part, cut ? part.slice(cut.index + cut[0].length).replace(/\)\s*$/, "") : ""];
    if (!m[1].trim()) return null;
    let desc = (m[2] || "").trim(), age = "", outfit = "";
    const a = desc.match(/^(\d{1,3}s?|teen|adult|elderly)\s*,\s*/i); if (a) { age = a[1]; desc = desc.slice(a[0].length); }
    const w = desc.match(/\bwearing\s+(.+)$/i); if (w) { outfit = w[1].replace(/[ ,.]+$/, ""); desc = desc.slice(0, w.index).replace(/[ ,]+$/, ""); }
    const name = m[1].trim().slice(0, 60);
    return { name, type: types[name.toLowerCase()] || "", age, look: desc.slice(0, 300), outfit: outfit.slice(0, 200), voice: "" };
  }).filter(Boolean).slice(0, 24);
}
app.get("/api/series/:slug", (req, res) => {
  const hit = seriesBySlug(req.user, req.params.slug);
  if (!hit) return res.status(404).json({ ok: false, error: "Unknown series." });
  const sr = hit.series, bible = sr.bible || {};
  if (!(bible.characters || []).length) bible.characters = bibleFromSeries(sr);
  const pdir = path.join(SERIES_DIR(), hit.slug, "portraits");
  for (const c of bible.characters) {
    c.portraitUrl = c.portrait && fs.existsSync(path.join(pdir, c.portrait))
      ? `/api/series/${hit.slug}/portrait/${encodeURIComponent(c.portrait)}?v=${Math.round(fs.statSync(path.join(pdir, c.portrait)).mtimeMs)}` : null;
  }
  res.json({ ok: true, slug: hit.slug, name: sr.name, level: sr.level || "family",
    bible: { characters: bible.characters, visual_style: bible.visual_style || sr.visual_style || "",
      picture_style: bible.picture_style || "", narrator: bible.narrator || "" },
    episodes: (sr.episodes || []).filter((e) => e.complete !== false).map((e) => ({ title: e.title, logline: e.logline, recap: e.recap || "", date: e.date })),
    state: sr.state || [], threads: sr.threads || [] });
});
app.put("/api/series/:slug", (req, res) => {
  const hit = seriesBySlug(req.user, req.params.slug);
  if (!hit) return res.status(404).json({ ok: false, error: "Unknown series." });
  const b = req.body || {}, bb = b.bible || {};
  const str = (x, n) => String(x == null ? "" : x).replace(/[\u0000-\u001f]/g, " ").trim().slice(0, n);
  const old = Object.fromEntries(((hit.series.bible || {}).characters || []).map((c) => [String(c.name).toLowerCase(), c]));
  const chars = (Array.isArray(bb.characters) ? bb.characters : []).slice(0, 24).map((c) => {
    const name = str(c.name, 60);
    if (!name) return null;
    const was = old[name.toLowerCase()] || {};
    const look = str(c.look, 300), age = str(c.age, 20), outfit = str(c.outfit, 200);
    return { name, type: BIBLE_TYPES.includes(c.type) ? c.type : "", age, look, outfit,
      voice: VOICE_OK.test(c.voice || "") ? c.voice : "",
      // a portrait only stays while the words it was drawn from are the same
      ...(was.portrait && was.look === look && was.age === age && was.outfit === outfit ? { portrait: was.portrait } : {}) };
  }).filter(Boolean);
  const sr = hit.series;
  sr.bible = { characters: chars, visual_style: str(bb.visual_style, 400),
    picture_style: BIBLE_STYLES.includes(bb.picture_style) ? bb.picture_style : "",
    narrator: VOICE_OK.test(bb.narrator || "") ? bb.narrator : "" };
  if (Array.isArray(b.state)) sr.state = b.state.map((x) => str(x, 240)).filter(Boolean).slice(0, 12);
  if (Array.isArray(b.threads)) sr.threads = b.threads.map((x) => str(x, 240)).filter(Boolean).slice(0, 12);
  fs.writeFileSync(hit.file, JSON.stringify(sr, null, 2));
  res.json({ ok: true });
});
app.post("/api/series/:slug/portraits", async (req, res) => {
  const hit = seriesBySlug(req.user, req.params.slug);
  if (!hit) return res.status(404).json({ ok: false, error: "Unknown series." });
  res.json(startJob(req, "portraits", null, await runStorymaker(req.user, ["--draw-portraits", hit.slug, "--background"])));
});
app.get("/api/series/:slug/portrait/:file", (req, res) => {
  const hit = seriesBySlug(req.user, req.params.slug);
  const file = String(req.params.file || "");
  if (!hit || !/^[a-z0-9-]+\.png$/.test(file)) return res.status(404).end();
  res.sendFile(path.join(SERIES_DIR(), hit.slug, "portraits", file));
});
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

// The read-along player: chapter times (saved when the audiobook is made; read from the audiobook for older stories).
const chapterCache = new Map();
function probeChapters(file) {
  return new Promise((resolve) => {
    let out = "";
    const p = spawn("ffprobe", ["-v", "error", "-print_format", "json", "-show_chapters", file]);
    p.stdout.on("data", (d) => { out += d; });
    p.on("error", () => resolve([]));
    p.on("close", () => {
      try {
        resolve((JSON.parse(out).chapters || []).map((c) => ({ start: Math.round(parseFloat(c.start_time) * 100) / 100,
          title: (c.tags && c.tags.title) || "" })));
      } catch { resolve([]); }
    });
  });
}
app.get("/api/stories/:id/chapters", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const dir = path.join(OUTPUT_DIR, id);
  try { return res.json({ ok: true, chapters: JSON.parse(fs.readFileSync(path.join(dir, "chapters.json"), "utf8")) }); } catch {}
  for (const name of ["story.m4b", "story.mp3"]) {
    const f = path.join(dir, name);
    if (!fs.existsSync(f)) continue;
    const key = `${f}:${fs.statSync(f).mtimeMs}`;
    if (!chapterCache.has(key)) chapterCache.set(key, await probeChapters(f));
    const ch = chapterCache.get(key);
    if (ch.length) {
      try { fs.writeFileSync(path.join(dir, "chapters.json"), JSON.stringify(ch, null, 1)); } catch { /* read-only: fine */ }
      return res.json({ ok: true, chapters: ch });
    }
  }
  res.json({ ok: true, chapters: [] });
});

// Where you stopped listening, kept per person so it picks up there on your phone, Mac or iPad.
app.get("/api/stories/:id/position", (req, res) => {
  const id = guard(req, res); if (!id) return;
  const p = db.prepare("SELECT pos, dur, updated FROM positions WHERE user_id = ? AND story_id = ?").get(req.user.id, id);
  res.json({ ok: true, pos: p ? p.pos : 0, dur: p ? p.dur : 0, updated: p ? p.updated : 0 });
});
app.put("/api/stories/:id/position", (req, res) => {
  const id = guard(req, res); if (!id) return;
  const pos = Math.max(0, Number((req.body || {}).pos) || 0);
  const dur = Math.max(0, Number((req.body || {}).dur) || 0);
  db.prepare(`INSERT INTO positions (user_id, story_id, pos, dur, updated) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (user_id, story_id) DO UPDATE SET pos = excluded.pos, dur = excluded.dur, updated = excluded.updated`)
    .run(req.user.id, id, pos, dur, Date.now());
  res.json({ ok: true });
});

// Your podcast feed's address (made the first time you ask), a new one (the old stops working), Explicit in it or not.
app.get("/api/feed", (req, res) => {
  let f = db.prepare("SELECT * FROM feeds WHERE user_id = ?").get(req.user.id);
  if (!f && req.query.make === "1") {
    db.prepare("INSERT INTO feeds (user_id, token) VALUES (?, ?)").run(req.user.id, crypto.randomBytes(24).toString("hex"));
    f = db.prepare("SELECT * FROM feeds WHERE user_id = ?").get(req.user.id);
  }
  res.json({ ok: true, url: f ? `${publicBase(req)}/feed/${f.token}.xml` : null, explicit: !!(f && f.explicit),
    studio: f ? !!f.studio : true, archive: !!(f && f.archive), nas: !!NAS_ROOT(),
    canExplicit: allowedLevels(req.user).includes("explicit"), https: publicBase(req).startsWith("https:") });
});
app.post("/api/feed", (req, res) => {
  const b = req.body || {};
  const f = db.prepare("SELECT * FROM feeds WHERE user_id = ?").get(req.user.id);
  if (!f) return res.json({ ok: false, error: "There's no feed yet." });
  if (b.reset) db.prepare("UPDATE feeds SET token = ? WHERE user_id = ?").run(crypto.randomBytes(24).toString("hex"), req.user.id);
  if (typeof b.explicit === "boolean") {
    db.prepare("UPDATE feeds SET explicit = ? WHERE user_id = ?").run(b.explicit && allowedLevels(req.user).includes("explicit") ? 1 : 0, req.user.id);
  }
  for (const k of ["studio", "archive"]) {
    if (typeof b[k] === "boolean") db.prepare(`UPDATE feeds SET ${k} = ? WHERE user_id = ?`).run(b[k] ? 1 : 0, req.user.id);
  }
  const n = db.prepare("SELECT * FROM feeds WHERE user_id = ?").get(req.user.id);
  res.json({ ok: true, url: `${publicBase(req)}/feed/${n.token}.xml`, explicit: !!n.explicit, studio: !!n.studio, archive: !!n.archive });
});

// E-book, print-ready picture book, and the chapter list YouTube wants in a video's description.
app.get("/api/stories/:id/epub", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = await runStorymaker(req.user, ["--export-epub", id]);
  if (!r.ok || !r.epub) return res.status(404).send(r.error || "Couldn't make the e-book");
  res.download(r.epub, `${niceName(id)}.epub`);
});
app.get("/api/stories/:id/print", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = await runStorymaker(req.user, ["--export-print", id]);
  if (!r.ok || !r.pdf) return res.status(404).send(r.error || "Couldn't make the print book");
  const cover = req.query.cover === "1";
  res.download(cover ? r.cover : r.pdf, `${niceName(id)} (print${cover ? " cover" : ""}).pdf`);
});
app.get("/api/stories/:id/youtube", (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = readResult(id) || {};
  let ch = [];
  try { ch = JSON.parse(fs.readFileSync(path.join(OUTPUT_DIR, id, "chapters.json"), "utf8")); } catch { /* none */ }
  const stamp = (t) => { t = Math.max(0, Math.floor(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s2 = String(t % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${s2}` : `${m}:${s2}`; };
  // YouTube's rules: the first at 0:00, at least three, each at least ten seconds long
  const marks = [];
  for (const c of ch) if (!marks.length || c.start - marks[marks.length - 1].start >= 10) marks.push(c);
  if (marks.length) marks[0] = { ...marks[0], start: 0 };
  const lines = marks.length >= 3 ? marks.map((c) => `${stamp(c.start)} ${c.title}`) : [];
  const desc = [r.logline || "", r.series && r.episode > 1 ? `${r.series}, episode ${r.episode}.` : ""].filter(Boolean).join("\n\n");
  const text = [desc, lines.length ? `Chapters\n${lines.join("\n")}` : "", "Made with StoryMaker."].filter(Boolean).join("\n\n");
  res.json({ ok: true, title: (r.title || "").slice(0, 100), text, chapters: lines.length,
    note: lines.length ? "" : "This one is too short for YouTube chapters (it needs at least three, ten seconds apart)." });
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
// Clean up the writing of stories already made: the proofreader and the sentence checker, then the audio again.
app.get("/api/stories/:id/writing-check", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const r = await runStorymaker(req.user, ["--check-writing", id]);
  res.json(r.ok ? { ok: true, ...(r.stories[0] || {}) } : r);
});
app.post("/api/stories/:id/cleanup", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const args = ["--cleanup", id, "--background", ...(b.remake === false ? ["--no-remake"] : [])];
  res.json(startJob(req, "cleanup", id, await runStorymaker(req.user, args)));
});
function cleanableStories(user) {           // your own stories (the owner: everyone's), that you're allowed to change
  const mine = new Set(db.prepare("SELECT id FROM stories WHERE user_id = ?").all(user.id).map((r) => r.id));
  return syncStories().filter((id) => canSee(user, id) && (user.role === "owner" || mine.has(id)))
    .filter((id) => { const r = readResult(id) || {}; return r.level !== "explicit" || allowedLevels(user).includes("explicit"); });
}
app.get("/api/writing-check", async (req, res) => {
  const ids = cleanableStories(req.user);
  if (!ids.length) return res.json({ ok: true, stories: [] });
  res.json(await runStorymaker(req.user, ["--check-writing", ids.join(",")]));
});
app.post("/api/cleanup", async (req, res) => {
  const b = req.body || {};
  const allowed = new Set(cleanableStories(req.user));
  const ids = (Array.isArray(b.ids) ? b.ids : []).map((x) => storyId(String(x))).filter((x) => x && allowed.has(x));
  if (!ids.length) return res.json({ ok: false, error: "Pick at least one story." });
  const args = ["--cleanup", ids.join(","), "--background", ...(b.remake === false ? ["--no-remake"] : [])];
  res.json(startJob(req, "cleanup", ids[0], await runStorymaker(req.user, args)));
});
app.post("/api/stories/:id/remaster", async (req, res) => {
  const id = guard(req, res); if (!id) return;
  const b = req.body || {};
  const args = ["--remaster", id, "--background", ...(b.revoice ? ["--revoice"] : []), ...(b.redesign ? ["--redesign"] : []),
    ...(b.redraw ? ["--redraw"] : []),
    ...(b.redraw && PICTURE_STYLES.includes(b.pictureStyle) ? ["--picture-style", b.pictureStyle] : [])];
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
      effects: (Array.isArray(plan.effects) ? plan.effects : []).slice(0, 8)
        .map((f) => ({ sound: String((f && f.sound) || "").slice(0, 60), at: String((f && f.at) || "").slice(0, 120), every: !!(f && f.every) })) };
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

// ───────────────────────────── the NAS archive ─────────────────────────────
// Audio Studio (on the Mac, later the server) does the copying, where the NAS share is connected; StoryMaker
// decides whose stories can be moved, seen and brought back.
const HELPER_URL = (process.env.STUDIO_HELPER_URL || envFileValue("STUDIO_HELPER_URL") || "http://host.docker.internal:8096").replace(/\/$/, "");
async function helper(method, p, body) {
  let token = "";
  try { token = fs.readFileSync(path.join(OUTPUT_DIR, ".helper-token"), "utf8").trim(); } catch { /* studio not run yet */ }
  if (!token) return { ok: false, error: "Audio Studio isn't running, and it's what moves stories to and from the NAS. Start it on the Mac." };
  try {
    const r = await fetch(HELPER_URL + p, { method, headers: { "Content-Type": "application/json", "X-Helper-Token": token },
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(60000) });
    const j = await r.json().catch(() => ({}));
    return r.ok ? j : { ok: false, error: j.error || j.detail || `Audio Studio said ${r.status}` };
  } catch {
    return { ok: false, error: "Can't reach Audio Studio, which moves stories to and from the NAS. Is it running?" };
  }
}
function folderSize(dir) {
  let total = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const f = path.join(d, e.name);
      if (e.isDirectory()) walk(f); else if (e.isFile()) total += fs.statSync(f).size;
    }
  };
  try { walk(dir); } catch { /* gone */ }
  return total;
}
function storyBusy(id) {
  const rows = db.prepare("SELECT id FROM jobs WHERE story_id = ? AND created > datetime('now', '-1 days')").all(id);
  return rows.some((j) => {
    try {
      const f = path.join(JOBS_DIR, `${j.id}.status.json`);
      const st = JSON.parse(fs.readFileSync(f, "utf8"));
      return (st.state === "running" || st.state === "queued") && Date.now() - fs.statSync(f).mtimeMs < 60 * 60 * 1000;
    } catch { return false; }
  });
}
function mayMove(u, id) {
  const row = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(id);
  return !!row && (u.role === "owner" || row.user_id === u.id) && canSee(u, id);
}
// stories that can go to the NAS, with their size
app.get("/api/nas/candidates", (req, res) => {
  const out = [];
  for (const id of syncStories()) {
    if (!mayMove(req.user, id)) continue;
    const r = readResult(id) || {};
    out.push({ id, title: r.title || id, level: r.level, created: r.created || id.slice(0, 17),
      size: folderSize(path.join(OUTPUT_DIR, id)), busy: storyBusy(id),
      thumb: fs.existsSync(path.join(OUTPUT_DIR, id, "thumb.jpg")) ? `/media/${encodeURIComponent(id)}/thumb.jpg` : null });
  }
  out.sort((a, b) => (a.id < b.id ? 1 : -1));
  res.json({ ok: true, stories: out });
});
// what's on the NAS that you may see
app.get("/api/nas", async (req, res) => {
  const h = await helper("GET", "/api/helper/nas");
  if (h.ok === false && !("configured" in h)) return res.json({ ok: false, error: h.error, items: [] });
  const items = (h.items || []).filter((it) => {
    const row = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(it.id);
    const mine = row ? row.user_id === req.user.id : it.owner_name === req.user.username;
    return (req.user.role === "owner" || mine) && allowedLevels(req.user).includes(it.level || "family");
  }).map((it) => ({ id: it.id, title: it.title, level: it.level, created: it.created, archived: it.archived,
    size: it.size, logline: it.logline, onComputer: !!it.on_computer, mode: it.mode,
    by: req.user.role === "owner" && it.owner_name && it.owner_name !== req.user.username ? it.owner_name : null }));
  res.json({ ok: !!h.ok, error: h.ok ? undefined : h.error, configured: h.configured !== false, free: h.free || null, items });
});
// move (or back up) stories to the NAS
app.post("/api/nas/archive", async (req, res) => {
  const b = req.body || {};
  const ids = [...new Set((Array.isArray(b.ids) ? b.ids : []).map((x) => storyId(String(x))).filter(Boolean))];
  const items = [];
  for (const id of ids) {
    if (!mayMove(req.user, id)) return res.json({ ok: false, error: "You can only move your own stories." });
    if (storyBusy(id)) return res.json({ ok: false, error: `“${(readResult(id) || {}).title || id}” is being worked on. Stop it or wait until it's done.` });
    const r = readResult(id) || {};
    const row = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(id);
    const owner = row && db.prepare("SELECT username FROM users WHERE id = ?").get(row.user_id);
    items.push({ id, title: r.title || id, level: r.level || "family", owner: row ? row.user_id : null,
      owner_name: owner ? owner.username : null, created: r.created || id.slice(0, 17), mode: r.mode, logline: r.logline || "" });
  }
  if (!items.length) return res.json({ ok: false, error: "Pick at least one story." });
  const h = await helper("POST", "/api/helper/nas/archive", { items, keep: !!b.keep });
  res.json(h.ok ? { ok: true, task: h.task } : { ok: false, error: h.error });
});
// bring stories back from the NAS
app.post("/api/nas/restore", async (req, res) => {
  const want = new Set((Array.isArray((req.body || {}).ids) ? req.body.ids : []).map(String));
  const listing = await helper("GET", "/api/helper/nas");
  if (!listing.ok) return res.json({ ok: false, error: listing.error });
  const ids = [];
  for (const it of listing.items || []) {
    if (!want.has(it.id) || it.on_computer) continue;
    const row = db.prepare("SELECT user_id FROM stories WHERE id = ?").get(it.id);
    const mine = row ? row.user_id === req.user.id : it.owner_name === req.user.username;
    if ((req.user.role === "owner" || mine) && allowedLevels(req.user).includes(it.level || "family")) {
      ids.push(it.id);
      // a fresh StoryMaker (on the new server, say) learns whose it is again
      if (!row) {
        const u = it.owner_name && db.prepare("SELECT id FROM users WHERE username = ?").get(it.owner_name);
        db.prepare("INSERT OR IGNORE INTO stories (id, user_id) VALUES (?, ?)").run(it.id, u ? u.id : req.user.id);
      }
    }
  }
  if (!ids.length) return res.json({ ok: false, error: "Nothing to bring back (it may be here already)." });
  const h = await helper("POST", "/api/helper/nas/restore", { ids });
  res.json(h.ok ? { ok: true, task: h.task } : { ok: false, error: h.error });
});
app.get("/api/nas/task/:tid", async (req, res) => {
  const tid = String(req.params.tid).replace(/[^a-f0-9]/g, "");
  res.json(await helper("GET", `/api/helper/nas/task/${tid}`));
});

app.get("/api/stories", (req, res) => {
  const names = Object.fromEntries(db.prepare("SELECT id, username FROM users").all().map((u) => [u.id, u.username]));
  const where = Object.fromEntries(db.prepare("SELECT story_id, pos, dur FROM positions WHERE user_id = ?").all(req.user.id)
    .map((p) => [p.story_id, p]));
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
      request: r.request || null, bedtime: !!(r.bedtime || (r.request && r.request.bedtime)),
      adventure: r.adventure ? url("adventure.json") : null, pictures: !!url("scene_01.png"),
      listened: where[id] && where[id].dur ? Math.min(1, where[id].pos / where[id].dur) : 0,
      by: req.user.role === "owner" && by && by.user_id !== req.user.id ? names[by.user_id] : null,
    });
  }
  stories.sort((a, b) => (a.id < b.id ? 1 : -1));
  res.json({ ok: true, stories });
});

// ───────────────────────────── the status page ─────────────────────────────
// Everything StoryMaker relies on, checked live: the NAS, the disks, the writers, Draw Things, Audio Studio.
const setting = (k, d = "") => process.env[k] || envFileValue(k) || d;
async function ping(url, ms = 3500) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(ms) });
    let j = null; try { j = await r.json(); } catch { /* not JSON */ }
    return { ok: r.ok, status: r.status, json: j };
  } catch (e) { return { ok: false, error: e.name === "TimeoutError" ? "didn't answer" : "can't reach it" }; }
}
function space(dir) {
  try { const s = fs.statfsSync(dir); return { free: s.bavail * s.bsize, total: s.blocks * s.bsize }; } catch { return null; }
}
const gb = (n) => (n >= 1e12 ? `${(n / 1e12).toFixed(1)} TB` : `${Math.round(n / 1e9)} GB`);
app.get("/api/health", async (req, res) => {
  const parts = [];
  const add = (name, ok, detail, kind = "") => parts.push({ name, ok, detail, kind });
  // storage
  const check = process.env.LIBRARY_CHECK_FILE;
  const libOk = check ? fs.existsSync(check) : fs.existsSync(OUTPUT_DIR);
  const ls = space(OUTPUT_DIR), js = space(JOBS_DIR);
  add(check ? "Library on the NAS" : "Library", libOk && !!ls,
    libOk ? (ls ? `${gb(ls.free)} free of ${gb(ls.total)}` : "connected") : "Not connected. New stories wait until it's back.", "storage");
  if (check && js) add("Server disk (work in progress)", js.free > 5e9, `${gb(js.free)} free of ${gb(js.total)}${js.free <= 5e9 ? ". Running low." : ""}`, "storage");
  // writers
  const provider = setting("STORY_PROVIDER", "claude").toLowerCase();
  const keyName = { claude: "ANTHROPIC_API_KEY", gemini: "GEMINI_API_KEY", openai: "OPENAI_API_KEY", groq: "GROQ_API_KEY" }[provider];
  if (keyName) add(`Writer: ${provider[0].toUpperCase() + provider.slice(1)}`, !!setting(keyName), setting(keyName) ? "Key is set" : `No ${keyName} in storymaker.env`, "writer");
  const [main, backup, dt, studio] = await Promise.all([
    setting("OLLAMA_URL") ? ping(`${setting("OLLAMA_URL").replace(/\/$/, "")}/api/tags`) : null,
    setting("OLLAMA_FALLBACK_URL") ? ping(`${setting("OLLAMA_FALLBACK_URL").replace(/\/$/, "")}/api/tags`) : null,
    setting("LOCAL_IMAGE_URL") ? ping(`${setting("LOCAL_IMAGE_URL").replace(/\/$/, "")}/`) : null,
    helper("GET", "/api/helper/status"),
  ]);
  const models = (r) => ((r && r.json && r.json.models) || []).map((m) => m.name);
  const want = setting("OLLAMA_MODEL", "llama3.1:8b"), adult = setting("OLLAMA_ADULT_MODEL", "dolphin3");
  const has = (list, m) => list.some((n) => n === m || n.split(":")[0] === m.split(":")[0]);
  if (main) {
    const l = models(main);
    add("Ollama on the Mac", main.ok, main.ok ? `${l.length} model${l.length === 1 ? "" : "s"}${has(l, want) || has(l, adult) ? "" : `; ${want} isn't downloaded`}` : `${main.error || "not answering"}. Is the Mac awake?`, "writer");
  }
  if (backup) {
    const l = models(backup);
    add("Backup Ollama (this server)", backup.ok, backup.ok ? `Ready${l.length ? ` (${l.slice(0, 3).join(", ")})` : ""}` : backup.error || "not answering", "writer");
  }
  if (dt) {
    const model = dt.json && (dt.json.model || (dt.json.settings && dt.json.settings.model));
    add("Draw Things", dt.ok, dt.ok ? (model ? `Ready: ${model}` : "Ready") : `${dt.error || "not answering"}. Open it on the Mac with its API Server on.`, "pictures");
  }
  let studioInfo = null;
  if (studio && studio.ok) {
    studioInfo = { version: studio.version, current: studio.current, waiting: studio.waiting || [], notify: studio.notify };
    add("Audio Studio", true, `Version ${studio.version}${studio.current ? `, making ${studio.current.title}` : ", free"}`, "studio");
  } else {
    add("Audio Studio", false, (studio && studio.error) || "Not answering", "studio");
  }
  // what's being made, what's next, what finished
  const owner_ = req.user.role === "owner";
  const names = Object.fromEntries(db.prepare("SELECT id, username FROM users").all().map((u) => [u.id, u.username]));
  const rows = db.prepare(`SELECT * FROM jobs WHERE created > datetime('now', '-3 days')${owner_ ? "" : " AND user_id = ?"} ORDER BY created DESC`)
    .all(...(owner_ ? [] : [req.user.id]));
  const now = [], recent = [];
  for (const j of rows) {
    let st, mt;
    try { const f = path.join(JOBS_DIR, `${j.id}.status.json`); st = JSON.parse(fs.readFileSync(f, "utf8")); mt = fs.statSync(f).mtimeMs; } catch { continue; }
    const runId = (st.result && st.result.run_id) || j.story_id;
    const r = runId ? readResult(runId) || {} : {};
    let info = {}; try { info = JSON.parse(j.info || "{}"); } catch { /* old */ }
    const item = { id: j.id, kind: j.kind, state: st.state, title: r.title || st.title || info.idea || "A story", progress: st.progress || 0,
      detail: st.detail || "", error: st.error || null, when: mt, runId: runId || null, by: owner_ && j.user_id !== req.user.id ? names[j.user_id] : null,
      fromQueue: !!j.from_queue };
    if ((st.state === "running" || st.state === "queued") && Date.now() - mt < 60 * 60 * 1000) now.push(item);
    else if (["done", "failed"].includes(st.state) && recent.length < 12) recent.push(item);
  }
  const q = db.prepare(`SELECT * FROM queue WHERE state = 'waiting'${owner_ ? "" : " AND user_id = ?"} ORDER BY pos, id`).all(...(owner_ ? [] : [req.user.id]));
  const w = nightWindow();
  res.json({ ok: true, checked: Date.now(), tz: process.env.TZ || Intl.DateTimeFormat().resolvedOptions().timeZone,
    uptime: Math.round((Date.now() - STARTED) / 1000), parts, now, recent, studio: studioInfo,
    queue: { items: q.map((x) => queueRow(x, req.user)), night: inNight(), from: fmtHm(w.start), to: fmtHm(w.end), next: nextNight().toISOString() },
    notify: !!req.user.ntfy_topic });
});

app.listen(PORT, () => console.log(`StoryMaker web page on http://localhost:${PORT}`));
