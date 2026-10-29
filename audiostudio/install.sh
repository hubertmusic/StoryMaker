#!/bin/bash
# Audio Studio: one-time setup on the Mac.   cd ~/StoryMaker/audiostudio && ./install.sh
set -e
cd "$(dirname "$0")"
echo "▸ Checking Homebrew…"
command -v brew >/dev/null || { echo "Homebrew is needed first: https://brew.sh"; exit 1; }

echo "▸ Python 3.13 and ffmpeg…"
brew list python@3.13 >/dev/null 2>&1 || yes | brew install python@3.13
brew list ffmpeg >/dev/null 2>&1 || brew list ffmpeg-full >/dev/null 2>&1 || yes | brew install ffmpeg
# the studio needs MP3 encoding and the pitch filter; the slim ffmpeg may not have them
FF=ffmpeg
[ -x "$(brew --prefix ffmpeg-full 2>/dev/null)/bin/ffmpeg" ] && FF="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
if ! "$FF" -hide_banner -encoders 2>/dev/null | grep -q libmp3lame || ! "$FF" -hide_banner -filters 2>/dev/null | grep -q rubberband; then
  echo "▸ Installing the full ffmpeg (MP3 and pitch support)…"
  yes | brew install ffmpeg-full
fi

PY="$(brew --prefix python@3.13)/bin/python3.13"
echo "▸ Making the studio's own Python…"
# a studio Python that's missing or broken (for example after a Python update) is made again
if [ -d venv ] && ! ./venv/bin/python -c "import sys" >/dev/null 2>&1; then rm -rf venv; fi
[ -d venv ] || "$PY" -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

if [ "$1" != "--no-chatterbox" ]; then
  echo "▸ Installing Chatterbox (the natural voices; a big download, a few minutes)…"
  # Chatterbox's watermark step needs pkg_resources, which newer Pythons no longer include
  if ./venv/bin/pip install --quiet "chatterbox-tts==0.1.7" "setuptools<81"; then
    echo "  Chatterbox is installed. Its voice model (a few GB) downloads the first time you record."
  else
    echo "  Chatterbox didn't install. The studio still works with Kokoro; send me the error above."
  fi
fi

[ -f studio.env ] || cp studio.env.example studio.env
chmod +x start.sh autostart.sh
echo
echo "Done. Start it with:  ./start.sh     then open http://localhost:8096"
echo "To start it by itself whenever the Mac starts:  ./autostart.sh"
