// StoryMaker's offline helper. It never serves an old copy of the app while you're online: every page, script
// and list comes from the server first, and the copy kept here is only used when there's no connection.
// Stories you choose "Save for offline" for are kept here until you remove them, and play from here.
const SHELL = "storymaker-shell-v2";
const OFFLINE = "storymaker-offline-v1";       // the same name the page uses for saved stories
const SHELL_FILES = /^\/(|index\.html|app\.js|style\.css|status\.html|status\.js|manifest\.webmanifest|icon-\d+\.png|fonts\/[\w.-]+)$/;
const SHELL_API = /^\/api\/(stories|me|features|series)$/;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (![SHELL, OFFLINE].includes(k)) await caches.delete(k);
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (p.startsWith("/media/") || /^\/api\/stories\/[^/]+\/chapters$/.test(p)) return e.respondWith(saved(req, url));
  if (req.mode === "navigate" || SHELL_FILES.test(p) || SHELL_API.test(p)) return e.respondWith(networkFirst(req, url));
  if (p.startsWith("/api/")) {
    e.respondWith(fetch(req).catch(() => new Response(JSON.stringify({ ok: false, offline: true,
      error: "You're offline. Stories saved on this device still play." }), { status: 503, headers: { "Content-Type": "application/json" } })));
  }
});

async function networkFirst(req, url) {
  const key = url.pathname === "/index.html" ? "/" : url.pathname;
  try {
    const res = await fetch(req);
    if (res.ok && !res.redirected) (await caches.open(SHELL)).put(key, res.clone()).catch(() => {});
    return res;
  } catch (err) {
    const shell = await caches.open(SHELL);
    const hit = (await shell.match(key)) || (req.mode === "navigate" && (await shell.match("/")));
    if (hit) return hit;
    if (SHELL_API.test(url.pathname)) {
      return new Response(JSON.stringify({ ok: false, offline: true, error: "You're offline." }),
        { status: 503, headers: { "Content-Type": "application/json" } });
    }
    throw err;
  }
}

// A saved story's file, including the part a player asks for when you skip ahead (a Range request).
async function saved(req, url) {
  const hit = await (await caches.open(OFFLINE)).match(url.pathname);
  if (!hit) return fetch(req);
  const range = req.headers.get("range");
  if (!range) return hit;
  const blob = await hit.blob();
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start = m && m[1] ? parseInt(m[1], 10) : 0;
  let end = m && m[2] ? parseInt(m[2], 10) : blob.size - 1;
  if (m && !m[1] && m[2]) { start = Math.max(0, blob.size - parseInt(m[2], 10)); end = blob.size - 1; }
  end = Math.min(end, blob.size - 1);
  if (start > end || start >= blob.size) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${blob.size}` } });
  }
  return new Response(blob.slice(start, end + 1), { status: 206, headers: {
    "Content-Type": hit.headers.get("Content-Type") || "application/octet-stream",
    "Content-Range": `bytes ${start}-${end}/${blob.size}`, "Content-Length": String(end - start + 1), "Accept-Ranges": "bytes" } });
}
