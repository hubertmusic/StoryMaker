// StoryMaker's status page: what's being made, what's waiting for tonight, and whether everything it uses answers.
const $ = (id) => document.getElementById(id);
const KIND = { make: "Making", rewrite: "Rewriting", cleanup: "Cleaning up", remaster: "Remaking the audio", portraits: "Drawing portraits" };
const DONE = { make: "Made", rewrite: "Rewritten", cleanup: "Cleaned up", remaster: "New audio", portraits: "Portraits drawn" };
const ago = (ms) => {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
};
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

function meter(pct) {
  const m = el("div", "meter"); const f = el("div", "meter-fill"); f.style.width = `${Math.max(0, Math.min(100, pct))}%`; m.appendChild(f); return m;
}

async function load() {
  $("refresh").disabled = true;
  let r;
  try {
    const res = await fetch("/api/health");
    if (res.status === 401) { location.href = "/login.html"; return; }
    r = await res.json();
  } catch {
    $("checked").textContent = "Can't reach StoryMaker. Is the server on?";
    $("refresh").disabled = false; return;
  } finally { $("refresh").disabled = false; }
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const up = r.uptime > 86400 ? plural(Math.round(r.uptime / 86400), "day") : r.uptime > 3600 ? plural(Math.round(r.uptime / 3600), "hour") : plural(Math.max(1, Math.round(r.uptime / 60)), "minute");
  $("checked").textContent = `Checked at ${new Date(r.checked).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. StoryMaker has been running for ${up}. This page checks again every 15 seconds.`;

  const now = $("nowList"); now.replaceChildren();
  if (!r.now.length) now.appendChild(el("p", "empty", "Nothing is being made right now."));
  for (const j of r.now) {
    const row = el("div", "now-row");
    const top = el("div", "now-top");
    top.append(el("span", "now-title", j.title), el("span", "now-pct", `${j.progress}%`));
    const sub = el("p", "small", `${KIND[j.kind] || "Working"}${j.state === "queued" ? ", waiting its turn" : ""}${j.detail ? `: ${j.detail}` : ""}${j.by ? `, for ${j.by}` : ""}${j.fromQueue ? " (from tonight's list)" : ""}`);
    row.append(top, meter(j.progress), sub); now.appendChild(row);
  }

  const q = r.queue;
  $("queueHint").textContent = q.items.length
    ? (q.night ? `It's night time (${q.from} to ${q.to}): these are made one after another.` : `These are made between ${q.from} and ${q.to}, one after another.`)
    : `Nothing on tonight's list. Use "Make it tonight" after checking a story's price. Night hours: ${q.from} to ${q.to}.`;
  const ql = $("queueList"); ql.replaceChildren();
  q.items.forEach((x, i) => {
    const row = el("div", "queue-row");
    const t = el("div", "q-text"); t.append(el("div", "q-title", `${i + 1}. ${x.title}`),
      el("div", "small", [x.mode, x.chapters > 1 ? `${x.chapters} chapters` : "", x.bedtime ? "bedtime" : "", x.by ? `by ${x.by}` : "",
        x.error ? `last try: ${x.error.slice(0, 120)}` : ""].filter(Boolean).join(", ")));
    row.appendChild(t); ql.appendChild(row);
  });

  const parts = $("parts"); parts.replaceChildren();
  for (const p of r.parts) {
    const li = el("li", p.ok ? "ok" : "bad");
    li.append(el("span", "mark", p.ok ? "✓" : "✕"), el("span", "part-name", p.name), el("span", "part-detail", p.detail));
    parts.appendChild(li);
  }
  const li = el("li", r.notify ? "ok" : "off");
  li.append(el("span", "mark", r.notify ? "✓" : "–"), el("span", "part-name", "Phone notifications"),
    el("span", "part-detail", r.notify ? "On" : "Off. Turn them on in Settings (the gear on the main page)."));
  parts.appendChild(li);

  const sb = $("studioBlock"), body = $("studioBody");
  sb.hidden = !r.studio; body.replaceChildren();
  if (r.studio) {
    if (r.studio.current) {
      const c = r.studio.current;
      const row = el("div", "now-row");
      const top = el("div", "now-top"); top.append(el("span", "now-title", c.title), el("span", "now-pct", `${c.progress || 0}%`));
      row.append(top, meter(c.progress || 0), el("p", "small", c.detail || ""));
      body.appendChild(row);
    } else body.appendChild(el("p", "empty", "Free."));
    if (r.studio.waiting.length) body.appendChild(el("p", "small", `Waiting: ${r.studio.waiting.join(", ")}`));
  }

  const rec = $("recent"); rec.replaceChildren();
  if (!r.recent.length) rec.appendChild(el("li", "empty", "Nothing in the last few days."));
  for (const j of r.recent) {
    const li2 = el("li", j.state === "done" ? "ok" : "bad");
    const name = j.state === "done" && j.runId ? Object.assign(el("a", "part-name", j.title), { href: `/#story=${encodeURIComponent(j.runId)}` }) : el("span", "part-name", j.title);
    li2.append(el("span", "mark", j.state === "done" ? "✓" : "✕"), name,
      el("span", "part-detail", `${j.state === "done" ? (DONE[j.kind] || "Done") : `Stopped: ${(j.error || "something went wrong").slice(0, 160)}`}, ${ago(j.when)}${j.by ? `, for ${j.by}` : ""}`));
    rec.appendChild(li2);
  }
}
$("refresh").addEventListener("click", load);
load();
setInterval(() => { if (!document.hidden) load(); }, 15000);
