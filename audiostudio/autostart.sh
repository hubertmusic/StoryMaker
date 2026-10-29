#!/bin/bash
# Start Audio Studio by itself whenever you log in to the Mac (and restart it if it ever stops).
#   ./autostart.sh         turn it on
#   ./autostart.sh off     turn it off
cd "$(dirname "$0")"
LABEL=com.storymaker.audiostudio
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && sleep 2     # let the old one finish stopping
if [ "$1" = "off" ]; then rm -f "$PLIST"; echo "Audio Studio won't start by itself any more."; exit 0; fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/StoryMaker/studio"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$PWD/start.sh</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/StoryMaker/studio/studio.log</string>
  <key>StandardErrorPath</key><string>$HOME/StoryMaker/studio/studio.log</string>
</dict></plist>
PL
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
  sleep 3
  launchctl bootstrap "gui/$(id -u)" "$PLIST" || { echo "Couldn't start it. Run ./autostart.sh again in a moment."; exit 1; }
fi
echo "Audio Studio is running and will start by itself from now on."
echo "Its log: ~/StoryMaker/studio/studio.log"
