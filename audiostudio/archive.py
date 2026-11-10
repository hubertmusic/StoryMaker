"""
The NAS archive: move finished stories (StoryMaker) and books (Audio Studio) off this computer onto the NAS, and
bring them back when you want to remake or listen to them again.

A move is a copy first: every file goes to the NAS, every file's size is checked there, and only then is the copy
on this computer removed. If anything doesn't match, nothing is removed. Bringing one back copies it home again
(and keeps the NAS copy as the backup).

On the NAS (ARCHIVE_DIR is the NAS share as this computer sees it, e.g. /Volumes/StoryMaker):
    StoryMaker Archive/storymaker/<story id>/   the whole story folder, plus archive.json (what it is, whose)
    StoryMaker Archive/studio/<book id>/        the whole studio book, plus archive.json

Audio Studio does the moving for both apps: it runs on the Mac (and later on the server) where the NAS share is
mounted, while StoryMaker runs inside Docker and asks it to (the same way it asks it to open Draw Things).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = "StoryMaker Archive"
KINDS = ("storymaker", "studio")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,160}")

_lock = threading.Lock()                  # one move at a time: the NAS and the disk aren't hammered by two
tasks = {}                                # task id → progress, for the pages to follow


def log(msg):
    try:
        import engine
        engine.log(msg)
    except Exception:
        print(msg, flush=True)


# ───────────────────────────── where the NAS is ─────────────────────────────

def _mount(url):
    """Mount the NAS share on a Mac (uses the password saved in the Keychain the first time you connected in
    Finder: Go → Connect to Server, with "Remember this password" ticked)."""
    if sys.platform != "darwin" or not url:
        return False
    try:
        r = subprocess.run(["osascript", "-e", f'mount volume "{url}"'], capture_output=True, text=True, timeout=45)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def base(cfg, mount=True):
    """The archive folder on the NAS, or raise with what to do."""
    where = str(cfg.get("ARCHIVE_DIR") or "").strip()
    if not where:
        raise ValueError("The NAS isn't set up yet: put ARCHIVE_DIR= (the NAS share, e.g. /Volumes/StoryMaker) in "
                         "audiostudio/studio.env.")
    root = Path(os.path.expanduser(where))
    if not root.is_dir() and mount and cfg.get("NAS_URL"):
        _mount(cfg["NAS_URL"])
        for _ in range(10):
            if root.is_dir():
                break
            time.sleep(1)
    if not root.is_dir():
        raise ValueError(f"Can't reach the NAS at {root}. Is it on, and is the share connected? "
                         + ("(On the Mac: Finder → Go → Connect to Server → " + cfg["NAS_URL"] + ")"
                            if cfg.get("NAS_URL") else ""))
    arch = root / ROOT
    for k in KINDS:
        (arch / k).mkdir(parents=True, exist_ok=True)
    return arch


def status(cfg):
    try:
        arch = base(cfg)
        probe = arch / f".write-test-{uuid.uuid4().hex[:6]}"
        probe.write_text("ok")
        probe.unlink()
        du = shutil.disk_usage(arch)
        return {"ok": True, "where": str(arch), "free": du.free, "total": du.total}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "configured": bool(str(cfg.get("ARCHIVE_DIR") or "").strip())}


# ───────────────────────────── copying, checked ─────────────────────────────

def files_of(folder):
    """[(path inside the folder, size)] for every file, hidden ones too (a story's .stems make remakes quick)."""
    out = []
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and not p.is_symlink():
            out.append((p.relative_to(folder).as_posix(), p.stat().st_size))
    return out


def size_of(folder):
    return sum(s for _, s in files_of(folder))


def copy_checked(src, dst, progress=lambda f, t: None, label=""):
    """Copy a folder to dst (as dst.partial first), check every file arrived whole, then put it in place.
    A copy already at dst is replaced only once the new one is complete. Raises if anything doesn't match."""
    src, dst = Path(src), Path(dst)
    listing = files_of(src)
    total = sum(s for _, s in listing) or 1
    tmp = dst.with_name(dst.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    done = 0
    for rel, size in listing:
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(src / rel, "rb") as fi, open(target, "wb") as fo:
            while True:
                chunk = fi.read(4 * 1024 * 1024)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                progress(done / total, f"{label}{done / 1e6:,.0f} of {total / 1e6:,.0f} MB")
        try:
            shutil.copystat(src / rel, target)          # dates (not always allowed on a NAS: that's fine)
        except OSError:
            pass
    got = dict(files_of(tmp))
    bad = [rel for rel, size in listing if got.get(rel) != size]
    if bad:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"{len(bad)} file(s) didn't copy whole (for example {bad[0]}). Nothing was removed.")
    if dst.exists():
        old = dst.with_name(dst.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        dst.rename(old)
        tmp.rename(dst)
        shutil.rmtree(old, ignore_errors=True)
    else:
        tmp.rename(dst)
    return total


def _remove(folder):
    """Remove the copy on this computer (only ever called after a checked copy is on the NAS)."""
    shutil.rmtree(folder)


# ───────────────────────────── moving and bringing back ─────────────────────────────

def _home_of(cfg, kind):
    if kind == "storymaker":
        return Path(cfg["SM_LIBRARY"])
    import engine
    return engine.studio_dir(cfg) / "projects"


def archive_one(cfg, kind, item_id, meta, keep=False, progress=lambda f, t: None):
    """Move (or with keep=True, back up) one story or book to the NAS. meta: title, level, owner… for the list."""
    if kind not in KINDS or not SAFE_ID.fullmatch(item_id or ""):
        raise ValueError("Unknown story.")
    src = _home_of(cfg, kind) / item_id
    if not src.is_dir():
        raise ValueError(f"“{meta.get('title') or item_id}” isn't on this computer.")
    arch = base(cfg)
    need = size_of(src)
    if shutil.disk_usage(arch).free < need * 1.05 + 50e6:
        raise RuntimeError(f"The NAS hasn't enough free space for “{meta.get('title') or item_id}” "
                           f"({need / 1e9:.1f} GB needed).")
    dst = arch / kind / item_id
    title = meta.get("title") or item_id
    copy_checked(src, dst, progress, f"{title}: ")
    info = {"app": kind, "id": item_id, "title": title, "level": meta.get("level"), "owner": meta.get("owner"),
            "owner_name": meta.get("owner_name"), "created": meta.get("created"), "mode": meta.get("mode"),
            "logline": (meta.get("logline") or "")[:300], "size": need,
            "archived": time.strftime("%Y-%m-%dT%H:%M:%S"), "kept_on_computer": bool(keep)}
    (dst / "archive.json").write_text(json.dumps(info, indent=2))
    if not keep:
        _remove(src)
    log(f"NAS: {'backed up' if keep else 'moved'} “{title}” ({need / 1e6:,.0f} MB).")
    return info


def restore_one(cfg, kind, item_id, progress=lambda f, t: None):
    """Copy one story or book back from the NAS. The NAS copy stays as the backup."""
    if kind not in KINDS or not SAFE_ID.fullmatch(item_id or ""):
        raise ValueError("Unknown story.")
    src = base(cfg) / kind / item_id
    if not src.is_dir():
        raise ValueError("That isn't on the NAS.")
    info = read_info(src)
    dst = _home_of(cfg, kind) / item_id
    if dst.exists():
        raise ValueError(f"“{info.get('title') or item_id}” is already on this computer.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dst.parent).free
    need = size_of(src)
    if free < need * 1.05 + 200e6:
        raise RuntimeError(f"Not enough free space on this computer ({need / 1e9:.1f} GB needed).")
    copy_checked(src, dst, progress, f"{info.get('title') or item_id}: ")
    (dst / "archive.json").unlink(missing_ok=True)
    log(f"NAS: brought back “{info.get('title') or item_id}”.")
    return info


def read_info(folder):
    try:
        return json.loads((Path(folder) / "archive.json").read_text())
    except (OSError, ValueError):
        return {"id": Path(folder).name, "title": Path(folder).name}


def listing(cfg, kind):
    """Everything of this kind on the NAS, newest first, with whether it's also on this computer."""
    arch = base(cfg)
    home = _home_of(cfg, kind)
    out = []
    for d in (arch / kind).iterdir():
        if not d.is_dir() or d.name.endswith((".partial", ".old")):
            continue
        info = read_info(d)
        info["id"] = d.name
        info["on_computer"] = (home / d.name).exists()
        out.append(info)
    out.sort(key=lambda x: x.get("archived") or "", reverse=True)
    return out


# ───────────────────────────── background tasks ─────────────────────────────

def start(name, steps):
    """Run steps — [(label, fn(progress) → result)] — one after another in the background. Returns a task id."""
    tid = uuid.uuid4().hex[:12]
    t = {"id": tid, "name": name, "state": "queued", "progress": 0, "detail": "Waiting for the NAS…",
         "done": [], "failed": [], "started": time.time()}
    tasks[tid] = t

    def run():
        with _lock:
            t["state"] = "running"
            n = len(steps) or 1
            for i, (label, fn) in enumerate(steps):
                def prog(f, txt, i=i):
                    t["progress"] = round(100 * (i + max(0.0, min(1.0, f))) / n)
                    t["detail"] = txt
                try:
                    t["done"].append({"label": label, "result": fn(prog)})
                except Exception as e:
                    log(f"NAS: {label}: {e}")
                    t["failed"].append({"label": label, "error": str(e)[:300]})
            t["progress"] = 100
            t["state"] = "done" if not t["failed"] else ("failed" if not t["done"] else "partly")
            t["detail"] = (f"{len(t['done'])} done" + (f", {len(t['failed'])} couldn't be" if t["failed"] else ""))
        for old in [k for k, v in tasks.items() if time.time() - v["started"] > 86400]:
            tasks.pop(old, None)

    threading.Thread(target=run, daemon=True, name=f"nas-{tid}").start()
    return tid


def task(tid):
    t = tasks.get(tid)
    return dict(t) if t else None


# ───────────────────────────── finished audiobooks on the NAS ─────────────────────────────
# Every finished studio audiobook is also copied to "StoryMaker Audiobooks/<book id>/" on the NAS, with an
# info.json, so StoryMaker on the server can put it in your podcast feed even while this Mac is asleep. The book
# itself stays in the studio; deleting the book takes its copy off the NAS too.

BOOKS = "StoryMaker Audiobooks"


def _books_root(cfg):
    return base(cfg, mount=False).parent / BOOKS


def _publish_from(cfg, d, pid, root):
    out = d / "out"
    src = next((out / n for n in ("story.mp3", "story.m4b") if (out / n).exists()), None)
    if not src or not (d / "project.json").exists():
        return False
    p = json.loads((d / "project.json").read_text())
    dst = root / pid
    info_f = dst / "info.json"
    st = src.stat()
    try:
        old = json.loads(info_f.read_text())
        if old.get("src_mtime") == st.st_mtime and old.get("size") == st.st_size and (dst / old.get("file", "")).exists():
            return False                                     # already there and up to date
    except (OSError, ValueError):
        pass
    dst.mkdir(parents=True, exist_ok=True)
    name = "audiobook" + src.suffix
    part = dst / (name + ".partial")
    shutil.copyfile(src, part)
    if part.stat().st_size != st.st_size:
        part.unlink(missing_ok=True)
        raise OSError(f"the copy of {p.get('title', pid)} came out the wrong size")
    part.replace(dst / name)
    cover = out / "cover.jpg"
    if cover.exists():
        shutil.copyfile(cover, dst / "cover.jpg")
    src_info = p.get("source") or {}
    info = {"id": pid, "title": p.get("title") or pid, "author": p.get("author") or "", "level": p.get("level") or "family",
            "owner": str(p.get("owner") or ""), "created": p.get("created") or "",
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            "file": name, "size": st.st_size, "src_mtime": st.st_mtime, "cover": cover.exists(),
            "source": {"type": src_info.get("type", ""), "id": src_info.get("id", "")},
            "sent_to_storymaker": bool(p.get("sent_to_storymaker"))}
    tmp = dst / "info.json.tmp"
    tmp.write_text(json.dumps(info, indent=1))
    tmp.replace(info_f)
    return True


def publish_book(cfg, pid):
    """Copy one finished book's audiobook to the NAS (quietly does nothing if the NAS isn't connected)."""
    import engine
    try:
        root = _books_root(cfg)
    except Exception:
        return False
    d = engine.studio_dir(cfg) / "projects" / pid
    if not d.is_dir():
        d = base(cfg, mount=False) / "studio" / pid              # an archived book
    return _publish_from(cfg, d, pid, root)


def publish_all(cfg):
    """Bring the NAS copies up to date: new or remade audiobooks copied, deleted books' copies removed.
    Returns how many were copied (None if the NAS isn't connected)."""
    import engine
    try:
        arch = base(cfg, mount=False)
        root = _books_root(cfg)
    except Exception:
        return None
    root.mkdir(parents=True, exist_ok=True)
    homes = {}
    for folder in (arch / "studio", engine.studio_dir(cfg) / "projects"):     # the studio's own copy wins
        try:
            for d in folder.iterdir():
                if d.is_dir() and (d / "project.json").exists():
                    homes[d.name] = d
        except OSError:
            return None                                      # couldn't look: don't remove anything this time
    copied = 0
    for pid, d in homes.items():
        try:
            copied += bool(_publish_from(cfg, d, pid, root))
        except Exception as e:
            print(f"[studio] couldn't put {pid}'s audiobook on the NAS: {e}", file=sys.stderr, flush=True)
    for d in root.iterdir():
        if d.is_dir() and d.name not in homes and (d / "info.json").exists():
            shutil.rmtree(d, ignore_errors=True)             # the book was deleted
    return copied
