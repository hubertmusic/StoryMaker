#!/bin/bash
# StoryMaker + Audio Studio: install or update, the safe way.
#
#   bash ~/StoryMaker/update.sh                 (uses the newest StoryMaker*.zip in Downloads)
#   bash ~/StoryMaker/update.sh /path/to/StoryMaker.zip
#
# It never deletes your stories, settings, sounds, music or Audio Studio books: it only puts the new program
# files in place. Every path is a full path, so it does the same thing whichever folder you run it from.
set -euo pipefail

APP="$HOME/StoryMaker"
ZIP="${1:-}"
if [ -z "$ZIP" ]; then
  ZIP="$(ls -t "$HOME"/Downloads/StoryMaker*.zip 2>/dev/null | head -1 || true)"
fi
if [ -z "$ZIP" ] || [ ! -f "$ZIP" ]; then
  echo "Can't find StoryMaker.zip in your Downloads folder. Download it first, or give its path:"
  echo "  bash ~/StoryMaker/update.sh /path/to/StoryMaker.zip"
  exit 1
fi
echo "▸ Using $ZIP"

# unpack into a brand-new temporary folder (the only thing this script ever deletes)
TMP="$(mktemp -d "${TMPDIR:-/tmp}/storymaker-update.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
SRC="$TMP/storymaker"
[ -f "$SRC/storymaker.py" ] || { echo "That zip doesn't look like StoryMaker."; exit 1; }

echo "▸ Putting the new program files in $APP (your stories and settings stay as they are)…"
mkdir -p "$APP" "$APP/output" "$APP/sounds" "$APP/music" "$APP/studio"
rsync -a \
  --exclude 'storymaker.env' --exclude 'output' --exclude 'sounds' --exclude 'music' --exclude 'studio' \
  --exclude 'audiostudio/venv' --exclude 'audiostudio/studio.env' \
  "$SRC/" "$APP/"
chmod +x "$APP/update.sh" "$APP/audiostudio/"*.sh

# first time: make storymaker.env with your choices
if [ ! -f "$APP/storymaker.env" ]; then
  echo
  echo "▸ First-time settings. Answer each question and press Return (just Return skips it)."
  ask() { local v; read -r -p "$1 " v </dev/tty; printf '%s' "$v"; }
  asks() { local v; read -r -s -p "$1 " v </dev/tty; echo >/dev/tty; printf '%s' "$v"; }
  USERNAME="$(ask 'Your StoryMaker user name (for example motownplayer2000):')"
  PASSWORD="$(asks 'Your StoryMaker password (it will not show as you type):')"
  GROQ="$(asks 'Your Groq API key (console.groq.com → API Keys):')"
  FREESOUND="$(asks 'Your Freesound API key (freesound.org/apiv2/apply), or Return to skip:')"
  DRAW="$(ask 'Do you use Draw Things for pictures? (y/n):')"
  ADULT="dolphin3"
  MODELS="$(ollama list 2>/dev/null || true)"          # (read it all first: a pipe that stops early counts as an error here)
  FOUND="$(printf '%s\n' "$MODELS" | awk 'tolower($1) ~ /rocinante/ {print $1; exit}')"
  [ -n "$FOUND" ] && ADULT="$FOUND"
  cp "$APP/storymaker.env.example" "$APP/storymaker.env"
  {
    echo
    echo "# ── Your settings (these lines win over the ones above)"
    echo "STORY_PROVIDER=groq"
    echo "GROQ_API_KEY=$GROQ"
    echo "TTS_PROVIDER=kokoro"
    echo "OLLAMA_MODEL=dolphin3"
    echo "OLLAMA_ADULT_MODEL=$ADULT"
    echo "FREESOUND_API_KEY=$FREESOUND"
    [ -n "$USERNAME" ] && echo "APP_USER=$USERNAME"
    [ -n "$PASSWORD" ] && echo "APP_PASSWORD=$PASSWORD"
    if [ "$DRAW" = "y" ] || [ "$DRAW" = "Y" ]; then
      echo "LOCAL_IMAGE_URL=http://host.docker.internal:7860"
      echo "LOCAL_IMAGE_STEPS=4"
      echo "LOCAL_IMAGE_CFG=1.5"
    fi
  } >> "$APP/storymaker.env"
  chmod 600 "$APP/storymaker.env"
  echo "  Saved in $APP/storymaker.env (change it any time with: nano ~/StoryMaker/storymaker.env)"
fi

echo
echo "▸ Starting StoryMaker (Docker)…"
cd "$APP"
docker compose up -d --build
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w '%{http_code}' http://localhost:8095/login.html | grep -q 200; then break; fi
  sleep 2
done
if curl -s -o /dev/null -w '%{http_code}' http://localhost:8095/login.html | grep -q 200; then
  echo "  StoryMaker is running."
else
  echo "  StoryMaker didn't answer yet. Check with: docker logs storymaker --tail 30"
fi

echo
echo "▸ Audio Studio…"
cd "$APP/audiostudio"
bash "$APP/audiostudio/install.sh"
bash "$APP/audiostudio/autostart.sh"
sleep 8
tail -1 "$APP/studio/studio.log" 2>/dev/null || true

echo
echo "All done."
echo "  StoryMaker:   http://localhost:8095    (anywhere: https://<your-mac>.ts.net)"
echo "  Audio Studio: http://localhost:8096    (anywhere: https://<your-mac>.ts.net:8443)"
