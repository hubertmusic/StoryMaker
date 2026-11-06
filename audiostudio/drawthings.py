"""
Keeps Draw Things' API server on.

Draw Things forgets its Settings → API Server switch every time the app closes (a restart, a crash, a Mac update,
quitting it). This turns it back on the way a person would: it opens Draw Things in the background, goes to
Settings → All, scrolls down to "API Server" and flips the switch, then checks the server answers.

It uses the Mac's accessibility controls, so the studio needs permission once:
System Settings → Privacy & Security → Accessibility → turn on "Python" (or Terminal, if you start the studio by
hand in a Terminal window). Without it, nothing is clicked and the studio just says what's missing.
"""

import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

APP_NAME = "Draw Things"
BUNDLE = "com.liuliu.draw-things"

_lock = threading.Lock()                 # one attempt at a time (the watchdog and StoryMaker can both ask)
state = {"up": None, "detail": "", "checked": 0.0, "last_try": 0.0, "failed_at": 0.0, "asked_permission": False}


def log(msg):
    try:
        import engine
        engine.log(msg)
    except Exception:                    # (tests, or used on its own)
        print(msg, flush=True)


# ───────────────────────────── where the server is ─────────────────────────────

def server_address(cfg):
    """(host, port) of Draw Things' server from LOCAL_IMAGE_URL, or None when it isn't on this Mac."""
    url = str(cfg.get("LOCAL_IMAGE_URL") or "").strip()
    if not url:
        return None
    u = urlparse(url if "://" in url else "http://" + url)
    host = (u.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        return None                      # Draw Things on another computer: nothing to switch on here
    return "127.0.0.1", u.port or 7860


def server_up(cfg, timeout=2.0):
    addr = server_address(cfg)
    if not addr:
        return False
    try:
        with socket.create_connection(addr, timeout=timeout):
            return True
    except OSError:
        return False


def app_pid():
    try:
        out = subprocess.run(["pgrep", "-x", APP_NAME], capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None
    except (OSError, ValueError):
        return None


def open_app():
    """Open Draw Things without bringing it to the front. Returns its process id, or None."""
    if not app_pid():
        if subprocess.run(["open", "-g", "-b", BUNDLE], capture_output=True).returncode != 0 and \
           subprocess.run(["open", "-g", "-a", APP_NAME], capture_output=True).returncode != 0:
            return None
    for _ in range(60):
        pid = app_pid()
        if pid:
            return pid
        time.sleep(1)
    return None


# ───────────────────────────── reading the screen (accessibility) ─────────────────────────────
# Each control becomes a small dict: role, subrole, title, value, x, y, w, h, and "ref" (the real control).
# The finding logic below only looks at these dicts, so it can be tested without a Mac.

def _ax():
    import ApplicationServices as AS                       # pyobjc-framework-ApplicationServices (Mac only)
    return AS


def accessibility_allowed(prompt=False):
    try:
        AS = _ax()
    except ImportError:
        return False
    try:
        if prompt:
            return bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True}))
        return bool(AS.AXIsProcessTrusted())
    except Exception:
        return False


def _attr(el, name):
    AS = _ax()
    try:
        err, val = AS.AXUIElementCopyAttributeValue(el, name, None)
    except Exception:
        return None
    return val if err == 0 else None


def _point(val, kind):
    AS = _ax()
    try:
        ok, p = AS.AXValueGetValue(val, AS.kAXValueCGPointType if kind == "point" else AS.kAXValueCGSizeType, None)
        if ok:
            return (p.x, p.y) if kind == "point" else (p.width, p.height)
    except Exception:
        pass
    return (0.0, 0.0)


def _text(v):
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


def snapshot(el, limit=1500):
    """Every control inside a window, top to bottom, as dicts."""
    out, stack = [], [el]
    while stack and len(out) < limit:
        e = stack.pop()
        x, y = _point(_attr(e, "AXPosition"), "point")
        w, h = _point(_attr(e, "AXSize"), "size")
        out.append({"role": _text(_attr(e, "AXRole")), "subrole": _text(_attr(e, "AXSubrole")),
                    "title": _text(_attr(e, "AXTitle")) or _text(_attr(e, "AXDescription")),
                    "value": _text(_attr(e, "AXValue")), "x": x, "y": y, "w": w, "h": h, "ref": e})
        kids = _attr(e, "AXChildren") or []
        stack.extend(reversed(list(kids)))
    return out


def _label(n):
    return (n.get("title") or n.get("value") or "").strip()


def find_named(nodes, role, name):
    name = name.lower()
    for n in nodes:
        if n["role"] == role and _label(n).lower() == name:
            return n
    return None


def find_api_switch(nodes):
    """The on/off switch under the "API Server" heading: the first switch below the heading, in the same column.
    Returns (switch, heading) or (None, heading-or-None)."""
    head = find_named(nodes, "AXStaticText", "API Server")
    if not head:
        return None, None
    best = None
    for n in nodes:
        if n["role"] != "AXCheckBox":
            continue
        if not (head["y"] < n["y"] < head["y"] + 400):          # below the heading, inside its box
            continue
        if n["x"] + n["w"] < head["x"] or n["x"] > head["x"] + 600:
            continue
        if best is None or n["y"] < best["y"]:
            best = n
    return best, head


def is_on(switch):
    return _text(switch.get("value")).strip().lower() in ("1", "true", "on")


def scroll_point(nodes):
    """Somewhere in the middle of the settings list (below the Basic / Advanced / All buttons)."""
    all_btn = find_named(nodes, "AXButton", "All")
    head = find_named(nodes, "AXStaticText", "All Settings")
    anchor = all_btn or head
    if not anchor:
        return None
    return (anchor["x"] - 100 if all_btn else anchor["x"] + 60, anchor["y"] + 320)


# ───────────────────────────── acting ─────────────────────────────

def _press(node):
    AS = _ax()
    try:
        return AS.AXUIElementPerformAction(node["ref"], "AXPress") == 0
    except Exception:
        return False


def _window_number(pid):
    try:
        import Quartz as Q
        best, area = None, 0
        for w in Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []:
            if int(w.get("kCGWindowOwnerPID", -1)) != pid or int(w.get("kCGWindowLayer", 1)) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            a = float(b.get("Width", 0)) * float(b.get("Height", 0))
            if a > area:
                best, area = int(w.get("kCGWindowNumber")), a
        return best
    except Exception:
        return None


def _scroll(pid, point, front=False):
    """Scroll the settings list down a few lines. Sent straight to Draw Things, so the person's mouse and the
    window in front don't change; with front=True (the last resort) it goes through the normal mouse path."""
    try:
        import Quartz as Q
        ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, -8)
        Q.CGEventSetLocation(ev, point)
        win = _window_number(pid)
        if win:
            Q.CGEventSetIntegerValueField(ev, 91, win)      # window under the pointer
            Q.CGEventSetIntegerValueField(ev, 92, win)      # …that can handle this event
        if front:
            back = Q.CGEventGetLocation(Q.CGEventCreate(None))
            Q.CGWarpMouseCursorPosition(point)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.05)
            Q.CGWarpMouseCursorPosition(back)
        else:
            Q.CGEventPostToPid(pid, ev)
        return True
    except Exception:
        return False


def _window(pid):
    AS = _ax()
    app = AS.AXUIElementCreateApplication(pid)
    for _ in range(40):
        wins = list(_attr(app, "AXWindows") or [])
        if wins:
            w = _attr(app, "AXMainWindow") or wins[0]
            if _attr(w, "AXMinimized"):
                try:
                    AS.AXUIElementSetAttributeValue(w, "AXMinimized", False)
                    time.sleep(1)
                except Exception:
                    pass
            return w
        time.sleep(1)
    return None


def flip_switch(pid):
    """Go to Settings → All → API Server in Draw Things and turn the switch on. Returns (ok, what happened)."""
    win = _window(pid)
    if win is None:
        return False, "Draw Things is open but has no window to work in"
    nodes = snapshot(win)
    sw, _ = find_api_switch(nodes)
    if sw is None:
        btn = find_named(nodes, "AXButton", "Settings")             # the sidebar's Settings tab
        if btn:
            _press(btn)
            time.sleep(1.2)
            nodes = snapshot(win)
        btn = find_named(nodes, "AXButton", "All")                  # Basic / Advanced / All
        if btn:
            _press(btn)
            time.sleep(1.2)
            nodes = snapshot(win)
        sw, _ = find_api_switch(nodes)
    tries, front = 0, False
    while sw is None and tries < 45:
        point = scroll_point(nodes)
        if point is None:
            return False, "couldn't find Draw Things' settings list (has the app's layout changed?)"
        before = [(_label(n), round(n["y"])) for n in nodes if n["role"] == "AXStaticText"][:12]
        _scroll(pid, point, front=front)
        time.sleep(0.5)
        nodes = snapshot(win)
        sw, _ = find_api_switch(nodes)
        after = [(_label(n), round(n["y"])) for n in nodes if n["role"] == "AXStaticText"][:12]
        if after == before and not front and tries >= 2:
            # Draw Things ignored the quiet scroll: bring it forward for a moment and scroll like a mouse would
            front = True
            subprocess.run(["open", "-b", BUNDLE], capture_output=True)
            time.sleep(1)
        tries += 1
    if sw is None:
        return False, "couldn't find the API Server switch in Draw Things' settings"
    if is_on(sw):
        return True, "the API Server switch was already on"
    if not _press(sw):
        return False, "couldn't press the API Server switch"
    time.sleep(1)
    nodes = snapshot(win)
    sw2, _ = find_api_switch(nodes)
    if sw2 is not None and not is_on(sw2):
        return False, "pressed the API Server switch but it didn't stay on"
    return True, "turned on Draw Things' API Server"


# ───────────────────────────── what the studio calls ─────────────────────────────

PERMISSION_FIX = ("Allow it once: System Settings → Privacy & Security → Accessibility → turn on \"Python\" "
                  "(or Terminal, if you start the studio in a Terminal window).")


def ensure_server(cfg, open_if_closed=True, wait=25):
    """Make sure Draw Things is open and its API server answers. Returns (ok, what happened)."""
    if not server_address(cfg):
        return False, "LOCAL_IMAGE_URL doesn't point at Draw Things on this Mac"
    if server_up(cfg):
        return True, "Draw Things is ready"
    if sys.platform != "darwin":
        return False, "Draw Things can only be switched on from the Mac itself"
    with _lock:
        state["last_try"] = time.time()
        if server_up(cfg):
            return True, "Draw Things is ready"
        pid = app_pid()
        opened = False
        if not pid:
            if not open_if_closed:
                return False, "Draw Things is closed"
            pid = open_app()
            opened = True
            if not pid:
                return False, "Draw Things isn't installed on this Mac (or wouldn't open)"
            for _ in range(10):                       # give it a moment: it may come up with the server already on
                time.sleep(1)
                if server_up(cfg):
                    return True, "opened Draw Things"
        if not accessibility_allowed():
            if not state["asked_permission"]:
                state["asked_permission"] = True
                accessibility_allowed(prompt=True)   # the Mac shows its "allow" box once
            return False, ("Draw Things is open but its API Server switch is off, and the studio isn't allowed to "
                           "turn it on. " + PERMISSION_FIX)
        try:
            ok, what = flip_switch(pid)
        except Exception as e:                         # never let this take the studio down
            ok, what = False, f"something went wrong switching it on ({e})"
        if not ok:
            return False, what
        for _ in range(wait):
            if server_up(cfg):
                return True, ("opened Draw Things and " if opened else "") + what
            time.sleep(1)
        return False, what + ", but the server still isn't answering"


def keep_on(cfg, reopen=True):
    """One watchdog check. Quiet when all is well; after a failed attempt it waits 10 minutes before trying again,
    so it never keeps clicking around in Draw Things."""
    up = server_up(cfg)
    state["checked"] = time.time()
    if up:
        if state["up"] is False:
            log("Draw Things' API server is answering again.")
        state.update(up=True, detail="Draw Things is ready", failed_at=0.0)
        return True
    if state["failed_at"] and time.time() - state["failed_at"] < 600:
        return False
    ok, what = ensure_server(cfg, open_if_closed=reopen)
    if ok:
        log(f"Draw Things: {what}.")
        state.update(up=True, detail=what, failed_at=0.0)
    else:
        if what != state["detail"]:
            log(f"Draw Things: {what}")
        state.update(up=False, detail=what, failed_at=time.time() if what != "Draw Things is closed" else 0.0)
    return ok


def status(cfg):
    return {"here": bool(server_address(cfg)) and sys.platform == "darwin",
            "up": server_up(cfg), "open": bool(app_pid()) if sys.platform == "darwin" else False,
            "allowed": accessibility_allowed() if sys.platform == "darwin" else False,
            "detail": state["detail"], "watching": bool(state.get("watching"))}


def watch(cfg, every=60):
    """Runs for as long as the studio does."""
    state["watching"] = True
    time.sleep(20)
    while True:
        try:
            keep_on(cfg, reopen=str(cfg.get("DRAW_THINGS_REOPEN", "on")).lower() not in ("off", "no", "0", "false"))
        except Exception as e:
            log(f"Draw Things watchdog: {e}")
        time.sleep(every)


def start_watchdog(cfg):
    if sys.platform != "darwin" or not server_address(cfg):
        return False
    if str(cfg.get("DRAW_THINGS_ALWAYS_ON", "on")).lower() in ("off", "no", "0", "false"):
        return False
    threading.Thread(target=watch, args=(cfg,), daemon=True, name="drawthings-watchdog").start()
    return True
