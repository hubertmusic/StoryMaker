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
exec ./venv/bin/python server.py
