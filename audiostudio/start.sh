#!/bin/bash
# Start Audio Studio (StoryMaker must be running too).
cd "$(dirname "$0")"
FULL="$(/opt/homebrew/bin/brew --prefix ffmpeg-full 2>/dev/null || brew --prefix ffmpeg-full 2>/dev/null)"
[ -n "$FULL" ] && [ -x "$FULL/bin/ffmpeg" ] && export PATH="$FULL/bin:$PATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTORCH_ENABLE_MPS_FALLBACK=1        # anything the graphics chip can't do runs on the processor
if ! ./venv/bin/python -c "import sys" >/dev/null 2>&1; then
  echo "The studio's Python is missing. In Terminal run:  cd ~/StoryMaker/audiostudio && bash install.sh"
  sleep 60                                  # don't restart over and over while it's missing
  exit 1
fi
if [ "$STUDIO_AT_LOGIN" = "1" ]; then
  # started by the Mac at log-in: wait until the Mac has finished starting up, so the studio never slows down
  # signing in (it can pick up a big recording job as soon as it starts)
  BOOT=$(sysctl -n kern.boottime 2>/dev/null | sed -E 's/^[{] sec = ([0-9]+).*/\1/')
  NOW=$(date +%s)
  if [ -n "$BOOT" ] && [ $((NOW - BOOT)) -lt 900 ]; then
    echo "Waiting 4 minutes for the Mac to finish starting up…"
    sleep 240
  fi
fi
exec nice -n 5 ./venv/bin/python server.py
