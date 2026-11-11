#!/bin/bash
# Move StoryMaker from this Mac to the server (the OptiPlex), keeping Draw Things, Ollama and Audio Studio here.
#
#   bash ~/StoryMaker/mac/move-to-server.sh
#
# Before you start: the NAS share is connected on this Mac (/Volumes/StoryMaker), and you can SSH to the server.
#
# Nothing on this Mac is deleted. Your stories are COPIED to the NAS (every file checked); when you're happy the
# server works, the last lines tell you how to free up the space here.
set -euo pipefail

APP="$HOME/StoryMaker"
STUDIO_ENV="$APP/audiostudio/studio.env"
PY="$APP/audiostudio/venv/bin/python"
ask() { local v; read -r -p "$1 " v </dev/tty; printf '%s' "$v"; }
yes() { local v; v="$(ask "$1 (y/n)")"; [ "$v" = "y" ] || [ "$v" = "Y" ]; }
envv() { grep -E "^$1=" "$2" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
setv() {   # KEY VALUE FILE
  touch "$3"; grep -v -E "^$1=" "$3" > "$3.tmp" || true; echo "$1=$2" >> "$3.tmp"; mv "$3.tmp" "$3"
}

[ "$(uname -s)" = "Darwin" ] || { echo "Run this on the Mac."; exit 1; }
[ -x "$PY" ] || { echo "Audio Studio isn't installed (it does the checked copying). Run the update first."; exit 1; }

echo "▸ The NAS"
NASDIR="$(envv ARCHIVE_DIR "$STUDIO_ENV")"; NASDIR="${NASDIR:-/Volumes/StoryMaker}"
if [ ! -d "$NASDIR" ] && [ -n "$(envv NAS_URL "$STUDIO_ENV")" ]; then
  osascript -e "mount volume \"$(envv NAS_URL "$STUDIO_ENV")\"" >/dev/null 2>&1 || true
fi
[ -d "$NASDIR" ] || { echo "  The NAS share isn't connected at $NASDIR. Connect it in Finder (Go → Connect to Server)."; exit 1; }
echo "  Using $NASDIR"

echo "▸ The server"
SERVER="$(ask "  The server's address (its Tailscale name or IP, e.g. 100.78.23.75):")"
SERVER="${SERVER#http://}"; SERVER="${SERVER#https://}"; SERVER="${SERVER%%/*}"; SERVER="${SERVER%%:*}"   # just the name
SUSER="$(ask "  Your user name on the server [$USER]:")"; SUSER="${SUSER:-$USER}"
ssh -o ConnectTimeout=10 "$SUSER@$SERVER" true || { echo "  Can't SSH to $SUSER@$SERVER. Check it's on and SSH works."; exit 1; }
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
MACIP="$( (command -v tailscale >/dev/null && tailscale ip -4) || ([ -x "$TS" ] && "$TS" ip -4) || ipconfig getifaddr en0 || true)"
MACIP="$(echo "$MACIP" | head -1)"
while :; do
  v="$(ask "  This Mac's address, as the server should reach it [$MACIP] (press Return to use it):")"
  v="${v:-$MACIP}"
  if [[ "$v" =~ ^[A-Za-z0-9.-]+$ ]]; then MACIP="$v"; break; fi
  echo "  That doesn't look like an address. Just press Return, or type something like 100.87.112.63."
done

echo "▸ Pausing StoryMaker on this Mac (so nothing changes while it's copied)"
(cd "$APP" && docker compose stop) || true

echo "▸ Copying your stories to the NAS (each one checked file by file)…"
"$PY" - "$APP/output" "$NASDIR" <<'PYEOF'
import shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "StoryMaker" / "audiostudio"))
import archive as A
src, nas = Path(sys.argv[1]), Path(sys.argv[2])
lib = nas / "library"
lib.mkdir(parents=True, exist_ok=True)
skip = {".jobs", ".storymaker.db", ".storymaker.db-wal", ".storymaker.db-shm", ".helper-token"}
items = [p for p in sorted(src.iterdir()) if p.name not in skip and not p.name.endswith((".partial", ".old"))]
total = len(items)
for n, p in enumerate(items, 1):
    dst = lib / p.name
    if p.is_dir():
        if dst.is_dir() and dict(A.files_of(dst)) == dict(A.files_of(p)):
            print(f"  {n}/{total} {p.name}: already there", flush=True)
            continue
        A.copy_checked(p, dst)
    else:
        shutil.copyfile(p, dst)
    print(f"  {n}/{total} {p.name}", flush=True)
for kind in ("sounds", "music"):                          # your sounds and music go too (the copies here stay)
    s = Path.home() / "StoryMaker" / kind
    if s.is_dir() and any(s.iterdir()):
        A.copy_checked(s, nas / kind) if not (nas / kind).exists() else shutil.copytree(s, nas / kind, dirs_exist_ok=True)
        print(f"  {kind} copied", flush=True)
(nas / ".storymaker-nas").write_text("StoryMaker library\n")
print("  All stories are on the NAS.")
PYEOF

echo "▸ Sending your settings, accounts and unfinished stories to the server"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
"$PY" - "$APP/output/.storymaker.db" "$STAGE/storymaker.db" <<'PYEOF'
import sqlite3, sys
from pathlib import Path
if Path(sys.argv[1]).exists():                            # a clean, consistent copy of the database
    src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
    src.backup(dst); dst.close(); src.close()
PYEOF
[ -d "$APP/output/.jobs" ] && tar -cf "$STAGE/jobs.tar" -C "$APP/output/.jobs" . || true
cp "$APP/storymaker.env" "$STAGE/storymaker.env"
ZIP="$(ls -t "$HOME"/Downloads/StoryMaker*.zip 2>/dev/null | head -1 || true)"
[ -n "$ZIP" ] || { echo "  Put StoryMaker.zip in Downloads first (the server installs from it)."; exit 1; }
cp "$ZIP" "$STAGE/StoryMaker.zip"
cp "$APP/server/setup-server.sh" "$STAGE/setup-server.sh"
ssh "$SUSER@$SERVER" 'mkdir -p ~/storymaker-move && chmod 700 ~/storymaker-move'
scp -q "$STAGE"/* "$SUSER@$SERVER:storymaker-move/"
ssh "$SUSER@$SERVER" 'chmod 600 ~/storymaker-move/storymaker.env'

# already set up (for example by running setup-server.sh on the server yourself)? then don't do it again
READY="$(ssh "$SUSER@$SERVER" 'test -f ~/StoryMaker/storymaker.env && curl -s -o /dev/null -w "%{http_code}" -m 5 http://localhost:8095/login.html' 2>/dev/null || true)"
if [ "$READY" = "200" ]; then
  echo "▸ StoryMaker is already running on the server: skipping its setup."
  ssh "$SUSER@$SERVER" 'rm -f ~/storymaker-move/storymaker.env' || true
else
echo "▸ Setting up the server (it will ask a few things: the NAS's address and login, Ollama)…"
echo "  First it asks for $SUSER's password ON THE SERVER ([sudo] Password). Type it and press Return: it won't"
echo "  show as you type, and it gives up after a few minutes if nothing is typed."
ssh -t "$SUSER@$SERVER" "bash ~/storymaker-move/setup-server.sh '$MACIP'" || {
  echo "  The server setup didn't finish. Fix what it said, then run this again (copying skips what's done)."
  echo "  Or set the server up from its own terminal:  bash ~/storymaker-move/setup-server.sh $MACIP"
  echo "  and then run this again here: it sees the server is ready and just finishes this Mac's part."
  echo "  Starting StoryMaker on this Mac again for now."
  (cd "$APP" && docker compose start) >/dev/null 2>&1 || true
  exit 1; }
fi

echo "▸ Letting the server use this Mac's Ollama"
PLIST="$HOME/Library/LaunchAgents/com.storymaker.ollama-network.plist"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.storymaker.ollama-network</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
    <string>launchctl setenv OLLAMA_HOST 0.0.0.0; sleep 20; if pgrep -x ollama >/dev/null || pgrep -f Ollama.app >/dev/null; then osascript -e 'quit app "Ollama"'; sleep 4; open -g -a Ollama; fi</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLISTEOF
launchctl bootout "gui/$(id -u)/com.storymaker.ollama-network" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl setenv OLLAMA_HOST 0.0.0.0
osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true; sleep 3; open -g -a Ollama || true
for i in $(seq 1 15); do curl -s -m 3 "http://$MACIP:11434/api/tags" >/dev/null && break; sleep 2; done
curl -s -m 3 "http://$MACIP:11434/api/tags" >/dev/null && echo "  Ollama answers on the network." \
  || echo "  Ollama isn't answering on $MACIP yet. Open the Ollama app, then check: curl http://$MACIP:11434/api/tags"

if grep -qE '^LOCAL_IMAGE_URL=.+' "$APP/storymaker.env"; then
  echo "▸ Letting the server use Draw Things"
  echo "  In Draw Things: Settings → All → API Server → IP: choose the one that isn't 'localhost only' (0.0.0.0)."
  while ! curl -s -m 3 -o /dev/null "http://$MACIP:7860/"; do
    v="$(ask "  Press Return to check again, or type s to skip for now:")"; [ "$v" = "s" ] && break
  done
  curl -s -m 3 -o /dev/null "http://$MACIP:7860/" && echo "  Draw Things answers on the network."
fi

echo "▸ Pointing Audio Studio at the server and the NAS library"
setv STORYMAKER_URL "http://$SERVER:8095" "$STUDIO_ENV"
setv STORYMAKER_OUTPUT "$NASDIR/library" "$STUDIO_ENV"
bash "$APP/audiostudio/autostart.sh" >/dev/null

echo "▸ StoryMaker on this Mac is switched off for good (the server runs it now)"
(cd "$APP" && docker compose down) || true
echo "$SERVER" > "$APP/.storymaker-on-server"         # so future updates don't start it here again

echo
echo "All done."
echo "  StoryMaker:   http://$SERVER:8095   (update the Apple Shortcut and bookmarks to this address)"
echo "  Audio Studio: http://localhost:8096 on this Mac, as before"
echo
echo "When you've checked a few stories open and play from the server, free up the space here with:"
echo "  rm -rf \"$APP/output\""
