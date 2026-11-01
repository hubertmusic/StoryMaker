"""
Audio Studio web page. Runs on the Mac itself (not in Docker) so Chatterbox can use the graphics chip.
Sign-in is StoryMaker's: same user names, passwords and grown-up settings.

    python server.py            (or ./start.sh)   →  http://localhost:8096
"""

import json
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

import engine as E

cfg = E.load_config()
PUBLIC = E.HERE / "public"
SM = cfg["STORYMAKER_URL"].rstrip("/")
COOKIE = "as_session"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
worker = E.Worker(cfg)

def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def tidy_stale_jobs():
    """A job whose studio has really gone (the Mac restarted, the studio crashed) shows as stopped, and starting
    it again carries on where it left off. A job another running copy of the studio is doing is left alone."""
    for f in (E.studio_dir(cfg) / "projects").glob("*/status.json"):
        st = E.read_status(f.parent)
        if st.get("state") in ("running", "queued") and not (st.get("pid") and st["pid"] != os.getpid() and alive(st["pid"])):
            E.write_status(f.parent, state="stopped",
                           detail="The studio was restarted. Start again to carry on where it left off.")


def wait_for_port(port):
    """If another copy of the studio is already running (one started by hand while the automatic one starts),
    wait quietly until it closes, then take over, instead of fighting over the port."""
    said = False
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            s.close()
            return
        except OSError:
            if not said:
                E.log(f"Another copy of the studio is using port {port}; waiting for it to close.")
                said = True
            time.sleep(5)
        finally:
            s.close()


# ───────────────────────────── sign-in (StoryMaker's accounts) ─────────────────────────────

_me_cache = {}


def sm_get(token, path, **kw):
    return requests.get(SM + path, cookies={"sm_session": token}, timeout=kw.pop("timeout", 20), **kw)


def current_user(request: Request):
    # StoryMaker's own sign-in counts too: on the same address its cookie reaches the studio (one sign-in for both)
    token = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        token = request.cookies.get("sm_session", "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise HTTPException(401, "Please sign in.")
    hit = _me_cache.get(token)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        r = sm_get(token, "/api/me", timeout=8)
    except requests.RequestException:
        raise HTTPException(503, f"StoryMaker isn't answering at {SM}. Is it running?")
    if r.status_code != 200 or not r.json().get("ok"):
        _me_cache.pop(token, None)
        raise HTTPException(401, "Please sign in.")
    me = r.json()
    user = {"id": str(me.get("id")), "username": me.get("username"), "role": me.get("role"), "levels": me.get("levels") or [], "token": token}
    _me_cache[token] = (time.time(), user)
    return user


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


_tries = {}


@app.post("/api/login")
def login(request: Request, body: dict = Body(...)):
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _tries.get(ip, []) if now - t < 600]
    if len(recent) >= 8:
        return JSONResponse({"ok": False, "error": "Too many tries. Wait a few minutes and try again."}, 429)
    try:
        r = requests.post(SM + "/api/login", json={"username": str(body.get("username", ""))[:60],
                                                   "password": str(body.get("password", ""))[:200]}, timeout=10)
    except requests.RequestException:
        return JSONResponse({"ok": False, "error": f"StoryMaker isn't answering at {SM}. Start it first."}, 503)
    token = r.cookies.get("sm_session")
    if r.status_code != 200 or not token:
        _tries[ip] = recent + [now]
        try:
            msg = r.json().get("error")
        except Exception:
            msg = None
        return JSONResponse({"ok": False, "error": msg or "That user name and password don't match."}, 401)
    _tries.pop(ip, None)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax")
    return resp


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE, "") or request.cookies.get("sm_session", "")
    if token:
        _me_cache.pop(token, None)
        try:
            requests.post(SM + "/api/logout", cookies={"sm_session": token}, timeout=5)
        except requests.RequestException:
            pass
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    resp.delete_cookie("sm_session")
    return resp


# ───────────────────────────── helpers ─────────────────────────────

def can_use(user, p):
    if user["role"] != "owner" and str(p.get("owner")) != user["id"]:
        return False
    return p.get("level", "family") in user["levels"]


def project_for(user, pid):
    try:
        d, p, s = E.load_project(cfg, pid)
    except ValueError:
        raise HTTPException(404, "That book isn't here any more.")
    if not can_use(user, p):
        raise HTTPException(404, "That book isn't here any more.")
    return d, p, s


def check_level(user, level):
    level = level if level in E.LEVELS else "family"
    if level not in user["levels"]:
        raise HTTPException(403, "Your account can't use that level.")
    return level


def files_of(d, p):
    out = d / "out"
    url = lambda name: f"/api/projects/{p['id']}/file/{name}" if (out / name).exists() else None
    chapters = []
    for ci, ch in enumerate(p["chapters"]):
        f = out / "chapters" / f"{ci + 1:03d}.mp3"
        chapters.append({"index": ci, "title": ch["title"],
                         "audio": f"/api/projects/{p['id']}/file/chapters/{ci + 1:03d}.mp3" if f.exists() else None,
                         "stamp": int(f.stat().st_mtime) if f.exists() else 0})
    ready = (out / "story.m4b").exists()
    return {"m4b": url("story.m4b"), "mp3": url("story.mp3"), "credits": url("sound-credits.txt"),
            "cover": url("cover.jpg"), "chapters": chapters, "pdf": f"/api/projects/{p['id']}/book.pdf",
            "everything": f"/api/projects/{p['id']}/everything" if ready else None,
            "canSend": ready and (p.get("source") or {}).get("type") == "storymaker",
            "sent": p.get("sent_to_storymaker")}


# ───────────────────────────── pages ─────────────────────────────

STATIC = {"login.html", "login.js", "style.css", "studio.css", "app.js", "index.html"}


@app.get("/")
def home(request: Request):
    try:
        current_user(request)
    except HTTPException as e:
        if e.status_code == 401:
            q = request.url.query
            return RedirectResponse("/login.html" + (f"?{q}" if q else ""))
        raise
    return FileResponse(PUBLIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/fonts/{name}")
def font(name: str):
    f = PUBLIC / "fonts" / name
    if not re.fullmatch(r"[\w.-]+", name) or not f.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(f, headers={"Cache-Control": "max-age=86400"})


@app.get("/{name}")
def static(name: str):
    if name not in STATIC:
        raise HTTPException(404, "Not found")
    return FileResponse(PUBLIC / name, headers={"Cache-Control": "no-cache"})


# ───────────────────────────── the studio ─────────────────────────────

@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"ok": True, "username": user["username"], "role": user["role"], "levels": user["levels"],
            "version": E.STUDIO_VERSION,
            "chatterbox": E.Chatterbox.available(), "engine": cfg["STUDIO_ENGINE"],
            "chatterboxModel": cfg.get("CHATTERBOX_MODEL", "standard"), "emotions": E.EMOTIONS,
            "storymaker": cfg.get("STORYMAKER_PUBLIC_URL", "")}


@app.get("/api/projects")
def projects(user=Depends(current_user)):
    return {"ok": True, "projects": [p for p in E.list_projects(cfg) if can_use(user, p)]}


@app.get("/api/storymaker/stories")
def sm_stories(user=Depends(current_user)):
    r = sm_get(user["token"], "/api/stories")
    if r.status_code != 200:
        raise HTTPException(502, "Couldn't get your StoryMaker stories.")
    imported = {}
    for f in (E.studio_dir(cfg) / "projects").glob("*/project.json"):
        try:
            p = json.loads(f.read_text())
            if (p.get("source") or {}).get("type") == "storymaker" and can_use(user, p):
                imported[p["source"]["id"]] = p["id"]
        except Exception:
            pass
    out = [{"id": s["id"], "title": s.get("title"), "level": s.get("level"), "created": s.get("created"),
            "logline": s.get("logline"), "series": s.get("series"), "episode": s.get("episode"),
            "imported": imported.get(s["id"])} for s in r.json().get("stories", [])]
    return {"ok": True, "stories": out}


def keep_style(user, pid):
    """Your sound style from StoryMaker (sounds you love, sounds never to use) goes with the book."""
    try:
        r = sm_get(user["token"], "/api/style", timeout=8)
        st = (r.json() or {}).get("style") or {} if r.status_code == 200 else {}
    except Exception:
        st = {}
    if st.get("sounds"):
        d, p, _ = E.load_project(cfg, pid)
        p["style"] = {"sounds": st["sounds"]}
        E.save_project(d, p)


@app.post("/api/import/storymaker")
def import_sm(body: dict = Body(...), user=Depends(current_user)):
    sid = str(body.get("id", ""))
    if not re.fullmatch(r"[\w.-]{6,120}", sid):
        raise HTTPException(400, "Unknown story.")
    if not body.get("again"):
        for p in E.list_projects(cfg):
            try:
                _, full_p, _ = E.load_project(cfg, p["id"])
            except ValueError:
                continue
            if (full_p.get("source") or {}).get("id") == sid and can_use(user, full_p):
                return {"ok": True, "id": p["id"], "existed": True}
    r = sm_get(user["token"], f"/api/stories/{sid}/full")
    if r.status_code != 200 or not r.json().get("ok"):
        raise HTTPException(404, "That StoryMaker story can't be opened.")
    full = r.json()
    check_level(user, full.get("level") or "family")
    pid = E.import_storymaker(cfg, user["id"], full, sid)
    keep_style(user, pid)
    d, p, _ = E.load_project(cfg, pid)
    for name in ("cover.jpg", "thumb.jpg"):            # the story's cover art, if it has one
        try:
            img = sm_get(user["token"], f"/media/{sid}/{name}")
            if img.status_code == 200 and img.headers.get("content-type", "").startswith("image/"):
                (d / "out").mkdir(exist_ok=True)
                (d / "out" / "cover.jpg").write_bytes(img.content)
                break
        except requests.RequestException:
            pass
    return {"ok": True, "id": pid}


@app.post("/api/import/file")
def import_file(file: UploadFile = File(...), title: str = Form(""), level: str = Form("family"),
                user=Depends(current_user)):
    level = check_level(user, level)
    suffix = Path(file.filename or "book.txt").suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp, length=1 << 20)
        if tmp.tell() > 60 * 1024 * 1024:
            raise HTTPException(413, "That file is too big (60 MB at most).")
    try:
        pid = E.import_file(cfg, user["id"], tmp.name, file.filename or "book.txt", title.strip()[:120], level)
        keep_style(user, pid)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return {"ok": True, "id": pid}


@app.post("/api/import/text")
def import_text(body: dict = Body(...), user=Depends(current_user)):
    level = check_level(user, body.get("level", "family"))
    text = str(body.get("text", ""))
    if len(text) > 5_000_000:
        raise HTTPException(413, "That's too much text to paste; upload it as a file.")
    pid = E.import_text(cfg, user["id"], str(body.get("title", ""))[:120], text, level)
    keep_style(user, pid)
    return {"ok": True, "id": pid}


@app.get("/api/projects/{pid}")
def project(pid: str, user=Depends(current_user)):
    d, p, s = project_for(user, pid)
    chapters = []
    for ch in p["chapters"]:
        chapters.append({"title": ch["title"], "sections": [
            {"id": sec["id"], "sound": sec.get("sound"), "lines": s["sections"].get(sec["id"], []),
             "words": len(sec["text"].split())} for sec in ch["sections"]]})
    st = E.read_status(d)
    st["queued_or_running"] = st.get("state") in ("queued", "running")
    return {"ok": True, "id": pid, "title": p["title"], "level": p["level"], "engine": p.get("engine"),
            "narrator": p.get("narrator"), "cast": p["cast"], "castDone": p.get("cast_done", False),
            "soundsDone": p.get("sounds_done", False), "soundWishes": p.get("sound_wishes", ""),
            "narratorLines": p.get("narrator_lines", 0), "words": p.get("words", 0), "source": p.get("source"),
            "chapters": chapters, "status": st, "files": files_of(d, p)}


@app.get("/api/projects/{pid}/status")
def status(pid: str, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    return {"ok": True, "status": E.read_status(d), "files": files_of(d, p)}


@app.post("/api/projects/{pid}/settings")
def settings(pid: str, body: dict = Body(...), user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    if body.get("engine") in ("chatterbox", "kokoro"):
        p["engine"] = body["engine"]
    if isinstance(body.get("title"), str) and body["title"].strip():
        p["title"] = body["title"].strip()[:120]
    E.save_project(d, p)
    return {"ok": True}


@app.post("/api/projects/{pid}/cast")
def cast(pid: str, body: dict = Body(...), user=Depends(current_user)):
    project_for(user, pid)
    items = [c for c in (body.get("cast") or [])[:200] if isinstance(c, dict)]
    p = E.update_cast(cfg, pid, items, body.get("narrator"))
    src = p.get("source") or {}
    if src.get("type") == "storymaker":      # the genders you checked go back to StoryMaker, so they stay right there too
        tags = [{"name": c["name"], "voice": E.ga_type(c)} for c in p["cast"] if c.get("locked")]
        if tags:
            try:
                requests.post(f"{SM}/api/stories/{src['id']}/cast", json={"cast": tags, "remake": False},
                              cookies={"sm_session": user["token"]}, timeout=20)
            except requests.RequestException:
                pass
    return {"ok": True, "cast": p["cast"], "narrator": p["narrator"]}


@app.post("/api/projects/{pid}/script")
def script(pid: str, body: dict = Body(...), user=Depends(current_user)):
    project_for(user, pid)
    E.update_script(cfg, pid, [c for c in (body.get("changes") or [])[:2000] if isinstance(c, dict)])
    return {"ok": True}


@app.post("/api/projects/{pid}/sounds")
def sounds(pid: str, body: dict = Body(...), user=Depends(current_user)):
    project_for(user, pid)
    E.update_sounds(cfg, pid, body.get("sounds") or {}, body.get("wishes"))
    return {"ok": True}


@app.post("/api/projects/{pid}/job")
def job(pid: str, body: dict = Body(...), user=Depends(current_user)):
    d, p, s = project_for(user, pid)
    kind = body.get("kind")
    if kind not in ("cast", "sounds", "record", "all"):
        raise HTTPException(400, "Unknown job.")
    kw = {}
    if kind == "cast" and body.get("redo"):
        kw["only"] = [sec["id"] for ch in p["chapters"] for sec in ch["sections"]]
    if kind == "sounds":
        kw["redo"] = bool(body.get("redo"))
    if kind == "record" and isinstance(body.get("chapters"), list):
        kw["chapters"] = [int(c) for c in body["chapters"] if str(c).isdigit()][:500]
    if kind in ("record", "all") and (p.get("engine") or cfg["STUDIO_ENGINE"]) == "chatterbox" \
            and not E.Chatterbox.available():
        raise HTTPException(400, "Chatterbox isn't installed yet (see the studio's README). Switch this book to "
                                 "Kokoro, or install Chatterbox and restart the studio.")
    worker.submit(pid, kind, **kw)
    return {"ok": True}


@app.post("/api/projects/{pid}/stop")
def stop(pid: str, user=Depends(current_user)):
    project_for(user, pid)
    worker.stop(pid)
    d = E.project_dir(cfg, pid)
    if E.read_status(d).get("state") == "queued":
        E.write_status(d, state="stopped", detail="Stopped before it started.")
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete(pid: str, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    if E.read_status(d).get("state") in ("running", "queued"):
        raise HTTPException(409, "Stop the recording first.")
    E.delete_project(cfg, pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/preview")
def preview(pid: str, section: str, line: str, engine: str = "", user=Depends(current_user)):
    project_for(user, pid)
    f = E.preview_line(cfg, pid, section, line, engine if engine in ("chatterbox", "kokoro") else None)
    return FileResponse(f, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})


FILE_NAME = re.compile(r"^(story\.m4b|story\.mp3|sound-credits\.txt|cover\.jpg|chapters/\d{3}\.mp3)$")


@app.get("/api/projects/{pid}/file/{name:path}")
def file(pid: str, name: str, download: int = 0, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    f = d / "out" / name
    if not FILE_NAME.match(name) or not f.is_file():
        raise HTTPException(404, "Not made yet.")
    kind = {".m4b": "audio/mp4", ".mp3": "audio/mpeg", ".txt": "text/plain; charset=utf-8", ".jpg": "image/jpeg"}[f.suffix]
    nice = re.sub(r"[^\w .,'()-]", "", p["title"]).strip() or "Audiobook"
    fname = f"{nice}{f.suffix}" if name.startswith("story") else f"{nice} - {f.name}"
    m = re.match(r"chapters/(\d{3})\.mp3$", name)
    if m and int(m.group(1)) <= len(p["chapters"]):
        fname = f"{E.safe_name(p['title'])} - {int(m.group(1)):02d} {E.safe_name(p['chapters'][int(m.group(1)) - 1]['title'])}.mp3"
    return FileResponse(f, media_type=kind, filename=fname if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "no-cache"})


def not_busy(d):
    if E.read_status(d).get("state") in ("running", "queued"):
        raise HTTPException(409, "Wait until the studio has finished working on this book.")


@app.get("/api/projects/{pid}/book.pdf")
def book_pdf(pid: str, download: int = 0, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    f = E.book_pdf(cfg, pid)
    return FileResponse(f, media_type="application/pdf", filename=f"{E.safe_name(p['title'])}.pdf",
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/projects/{pid}/everything")
def everything(pid: str, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    not_busy(d)
    f = E.everything_zip(cfg, pid)
    return FileResponse(f, media_type="application/zip", filename=f"{E.safe_name(p['title'])}.zip")


@app.post("/api/projects/{pid}/send-to-storymaker")
def send_to_sm(pid: str, user=Depends(current_user)):
    d, p, _ = project_for(user, pid)
    not_busy(d)
    sid = (p.get("source") or {}).get("id", "")
    r = sm_get(user["token"], f"/api/stories/{sid}/full")          # you can still open that story in StoryMaker
    if r.status_code != 200 or not r.json().get("ok"):
        raise HTTPException(403, "StoryMaker won't open that story for your account any more.")
    return E.send_to_storymaker(cfg, pid)


@app.get("/api/voices")
def voices(user=Depends(current_user)):
    return {"ok": True, "voices": E.voice_library(cfg)}


@app.get("/api/voices/sample")
def voice_sample(voice: str, engine: str = "kokoro", user=Depends(current_user)):
    if not (re.fullmatch(r"kokoro:[ab][fm]_[a-z]+(\+[ab][fm]_[a-z]+)?(\^[\d.]+)?", voice) or
            re.fullmatch(r"clip:[\w.-]+", voice)):
        raise HTTPException(400, "Unknown voice.")
    f = E.voice_sample(cfg, voice, engine if engine in ("chatterbox", "kokoro") else "kokoro")
    return FileResponse(f, media_type="audio/mpeg")


@app.post("/api/voices/upload")
def voice_upload(file: UploadFile = File(...), name: str = Form(""), gender: str = Form("female"),
                 accent: str = Form(""), user=Depends(current_user)):
    data = file.file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "That clip is too big (25 MB at most).")
    vid = E.add_voice_clip(cfg, data, file.filename or "voice.wav", name.strip()[:40], gender, accent.strip())
    return {"ok": True, "id": vid}


if __name__ == "__main__":
    wait_for_port(int(cfg["STUDIO_PORT"]))
    tidy_stale_jobs()
    E.log(f"Audio Studio {E.STUDIO_VERSION} on http://localhost:{cfg['STUDIO_PORT']}  (sign-in from StoryMaker at {SM})")
    uvicorn.run(app, host="0.0.0.0", port=int(cfg["STUDIO_PORT"]), log_level="warning")
