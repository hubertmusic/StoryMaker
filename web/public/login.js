const $ = (id) => document.getElementById(id);
$("u").focus();
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("go").disabled = true; $("err").hidden = true;
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: $("u").value, password: $("p").value }) }).then((x) => x.json()).catch(() => ({ ok: false, error: "Can't reach StoryMaker." }));
  $("go").disabled = false;
  if (r.ok) { location.href = "/"; return; }
  $("err").textContent = r.error || "That didn't work."; $("err").hidden = false; $("p").select();
});
