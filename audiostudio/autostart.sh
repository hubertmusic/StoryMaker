#!/bin/bash
# Start Audio Studio by itself whenever you log in to the Mac (and restart it if it ever stops).
#   ./autostart.sh         turn it on
#   ./autostart.sh off     turn it off
cd "$(dirname "$0")"
LABEL=com.storymaker.audiostudio
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SVC="gui/$(id -u)/$LABEL"
if launchctl print "$SVC" >/dev/null 2>&1; then
  launchctl bootout "$SVC" 2>/dev/null || true
  for i in $(seq 1 20); do launchctl print "$SVC" >/dev/null 2>&1 || break; sleep 1; done   # let it finish stopping
fi
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
  <key>ThrottleInterval</key><integer>30</integer>
  <key>EnvironmentVariables</key><dict><key>STUDIO_AT_LOGIN</key><string>1</string></dict>
  <key>StandardOutPath</key><string>$HOME/StoryMaker/studio/studio.log</string>
  <key>StandardErrorPath</key><string>$HOME/StoryMaker/studio/studio.log</string>
</dict></plist>
PL
started=""
for i in 1 2 3 4 5; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then started=1; break; fi
  # still registered from before (the Mac hadn't finished stopping it): restart that one instead
  if launchctl print "$SVC" >/dev/null 2>&1 && launchctl kickstart -k "$SVC" 2>/dev/null; then started=1; break; fi
  sleep 3
done
[ -n "$started" ] || { echo "Couldn't start it. Run this in a moment:  bash ~/StoryMaker/audiostudio/autostart.sh"; exit 1; }
echo "Audio Studio will start by itself from now on (a few minutes after the Mac starts, so logging in stays quick)."
echo "Its log: ~/StoryMaker/studio/studio.log"
