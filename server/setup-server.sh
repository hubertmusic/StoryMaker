#!/bin/bash
# Set up StoryMaker on the server (Linux, e.g. the OptiPlex). Normally run for you by the Mac's
# mac/move-to-server.sh, over SSH. It can also be run by hand on the server:
#
#   bash ~/storymaker-move/setup-server.sh  <Mac address>
#
# What it does (it asks before anything big):
#   1. Docker and the NAS tools, if they're missing
#   2. Connects the NAS share at /mnt/storymaker (and every time the server starts)
#   3. Puts StoryMaker in ~/StoryMaker, with your settings, database and unfinished stories from the Mac
#   4. Points StoryMaker at the Mac for Draw Things, Ollama and Audio Studio
#   5. Optionally installs Ollama here as the backup writer for when the Mac is off
#   6. Starts StoryMaker on port 8095
set -euo pipefail

MAC="${1:-}"
MOVE="$HOME/storymaker-move"            # what the Mac sent: the zip, settings, database, unfinished stories
APP="$HOME/StoryMaker"
NAS_MOUNT="/mnt/storymaker"
ask() { local v; read -r -p "$1 " v </dev/tty; printf '%s' "$v"; }
asks() { local v; read -r -s -p "$1 " v </dev/tty; echo >/dev/tty; printf '%s' "$v"; }
yes() { local v; v="$(ask "$1 (y/n)")"; [ "$v" = "y" ] || [ "$v" = "Y" ]; }

[ "$(uname -s)" = "Linux" ] || { echo "This is for the Linux server."; exit 1; }
[ -n "$MAC" ] || MAC="$(ask "The Mac's address (its Tailscale IP, e.g. 100.87.112.63):")"

echo "▸ Your password on this server, for installing tools and connecting the NAS"
until sudo -v; do
  yes "  That didn't work. Try again?" || { echo "  Setup needs your password (sudo). Run this again when ready."; exit 1; }
done
( while kill -0 $$ 2>/dev/null; do sudo -n -v 2>/dev/null; sleep 50; done ) &   # don't ask again halfway through

echo "▸ 1. Docker and the NAS tools"
if ! command -v docker >/dev/null 2>&1; then
  if yes "  Docker isn't installed. Install it now (from get.docker.com)?"; then
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. (This session uses sudo for it; after you log in again you won't need to.)"
  else
    echo "  StoryMaker needs Docker. Install it, then run this again."; exit 1
  fi
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
$DOCKER compose version >/dev/null 2>&1 || { echo "  Docker Compose is missing: sudo apt install docker-compose-plugin"; exit 1; }
command -v mount.cifs >/dev/null 2>&1 || sudo apt-get install -y cifs-utils
command -v unzip >/dev/null 2>&1 || sudo apt-get install -y unzip

echo "▸ 2. The NAS"
if ! mountpoint -q "$NAS_MOUNT"; then
  NAS="$(ask "  NAS address (e.g. 192.168.1.50):")"
  SHARE="$(ask "  Shared folder name [StoryMaker]:")"; SHARE="${SHARE:-StoryMaker}"
  NUSER="$(ask "  NAS user name:")"
  NPASS="$(asks "  NAS password (won't show):")"
  CRED=/etc/storymaker-nas.credentials
  printf 'username=%s\npassword=%s\n' "$NUSER" "$NPASS" | sudo tee "$CRED" >/dev/null
  sudo chmod 600 "$CRED"
  sudo mkdir -p "$NAS_MOUNT"
  OPTS="credentials=$CRED,uid=$(id -u),gid=$(id -g),file_mode=0664,dir_mode=0775,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=30"
  sudo sed -i "\#[[:space:]]$NAS_MOUNT[[:space:]]#d" /etc/fstab        # an old line for it, if any
  for V in 3.0 2.1; do
    echo "//$NAS/$SHARE $NAS_MOUNT cifs $OPTS,vers=$V 0 0" | sudo tee -a /etc/fstab >/dev/null
    sudo systemctl daemon-reload
    if sudo mount "$NAS_MOUNT" 2>/dev/null && mountpoint -q "$NAS_MOUNT"; then break; fi
    sudo sed -i "\#[[:space:]]$NAS_MOUNT[[:space:]]#d" /etc/fstab
  done
  mountpoint -q "$NAS_MOUNT" || { echo "  Couldn't connect the NAS (check the address, folder name, user and password)."; exit 1; }
fi
touch "$NAS_MOUNT/.write-test" && rm "$NAS_MOUNT/.write-test" || { echo "  The NAS is connected but read-only for this user."; exit 1; }
mkdir -p "$NAS_MOUNT/library" "$NAS_MOUNT/sounds" "$NAS_MOUNT/music"
date > "$NAS_MOUNT/.storymaker-nas"                    # StoryMaker checks this is there before saving a story
echo "  NAS connected at $NAS_MOUNT (it reconnects by itself when the server starts)."

echo "▸ 3. StoryMaker's files"
ZIP="$(ls -t "$MOVE"/StoryMaker*.zip "$HOME"/Downloads/StoryMaker*.zip 2>/dev/null | head -1 || true)"
[ -n "$ZIP" ] || { echo "  Can't find StoryMaker.zip in $MOVE."; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
mkdir -p "$APP" "$APP/local/jobs"
rsync -a --exclude 'storymaker.env' --exclude 'local' --exclude 'audiostudio' "$TMP/storymaker/" "$APP/"
chmod +x "$APP/update.sh" "$APP/server/"*.sh 2>/dev/null || true
if [ -f "$MOVE/storymaker.env" ] && [ ! -f "$APP/storymaker.env" ]; then
  cp "$MOVE/storymaker.env" "$APP/storymaker.env"
fi
[ -f "$APP/storymaker.env" ] || cp "$APP/storymaker.env.example" "$APP/storymaker.env"
chmod 600 "$APP/storymaker.env"
if [ -f "$MOVE/storymaker.db" ] && [ ! -f "$APP/local/storymaker.db" ]; then
  cp "$MOVE/storymaker.db" "$APP/local/storymaker.db"
  echo "  Your accounts and settings came across."
fi
if [ -f "$MOVE/jobs.tar" ]; then
  tar -xf "$MOVE/jobs.tar" -C "$APP/local/jobs" && echo "  Unfinished stories came across."
fi

echo "▸ 4. Pointing StoryMaker at the Mac ($MAC)"
setv() {   # set KEY=VALUE in storymaker.env (replacing any line for it)
  grep -v -E "^$1=" "$APP/storymaker.env" > "$APP/storymaker.env.tmp" || true
  echo "$1=$2" >> "$APP/storymaker.env.tmp"
  mv "$APP/storymaker.env.tmp" "$APP/storymaker.env"; chmod 600 "$APP/storymaker.env"
}
if grep -qE '^LOCAL_IMAGE_URL=.+' "$APP/storymaker.env"; then setv LOCAL_IMAGE_URL "http://$MAC:7860"; fi
setv OLLAMA_URL "http://$MAC:11434"
setv STUDIO_HELPER_URL "http://$MAC:8096"
grep -qE '^AUDIO_STUDIO_URL=.+' "$APP/storymaker.env" || setv AUDIO_STUDIO_URL "http://$MAC:8096"

echo "▸ 5. A backup writer on this server (for when the Mac is off)"
if command -v ollama >/dev/null 2>&1 || yes "  Install Ollama here as the backup? (about 5 GB with its model; slower than the Mac)"; then
  command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
  # listen where Docker can reach it (StoryMaker runs in Docker)
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0"\n' | sudo tee /etc/systemd/system/ollama.service.d/storymaker.conf >/dev/null
  sudo systemctl daemon-reload && sudo systemctl restart ollama
  sleep 3
  MODEL="$(grep -E '^OLLAMA_ADULT_MODEL=' "$APP/storymaker.env" | tail -1 | cut -d= -f2)"; MODEL="${MODEL:-dolphin3}"
  echo "  Getting $MODEL (a few GB)…"
  ollama pull "$MODEL" || echo "  Couldn't get $MODEL now; later: ollama pull $MODEL"
  setv OLLAMA_FALLBACK_URL "http://host.docker.internal:11434"
  setv OLLAMA_FALLBACK_MODEL "$MODEL"
fi

echo "▸ 6. Starting StoryMaker"
cd "$APP"
NAS_MOUNT="$NAS_MOUNT" $DOCKER compose -f "$APP/docker-compose.server.yml" up -d --build
for i in $(seq 1 40); do
  curl -s -o /dev/null -w '%{http_code}' http://localhost:8095/login.html | grep -q 200 && break; sleep 3
done
if curl -s -o /dev/null -w '%{http_code}' http://localhost:8095/login.html | grep -q 200; then
  echo "  StoryMaker is running on this server: http://$(hostname -I | awk '{print $1}'):8095"
else
  echo "  StoryMaker didn't answer yet. Check: $DOCKER logs storymaker --tail 40"; exit 1
fi
# the settings file with your keys was only sent here to be used: don't leave a second copy lying around
rm -f "$MOVE/storymaker.env"
echo
echo "Done. Your Mac copy of everything is untouched until you decide to free up its space."
