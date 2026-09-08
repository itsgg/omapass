#!/bin/bash
# Sends a real Tab (or Shift+Tab) to the note editor and reports where focus
# went. Usage: tabkey.sh [Tab|ISO_Left_Tab]
#
# reportTabOrder walks Qt's static focus graph. That cannot see a widget which
# accepts the Tab key as text input instead of passing it on, which is the
# difference between a field Tab reaches and a field Tab cannot leave. Only a
# real keypress settles it.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit"
LOG="$WORK/shots/tab-key-note.log"

OMAPASS_AUDIT_HOLD=6 "$HERE/shoot.sh" tab-key-note >/dev/null 2>&1 &
shoot_pid=$!

# Never send synthetic input before confirming the target surface is mapped,
# and never before the harness says it has put focus where the test needs it:
# sending Tab early moved focus INTO the note and made the run look like a
# pass while testing nothing.
ready=0
for _ in $(seq 1 100); do
  if hyprctl layers 2>/dev/null | grep -q "omapass-audit" \
     && grep -q "TAB-KEY before:" "$LOG" 2>/dev/null; then ready=1; break; fi
  sleep 0.1
done
if [ "$ready" != 1 ]; then
  echo "harness never reported focus on the note; sending no input"
  kill "$shoot_pid" 2>/dev/null
  sed 's/\x1b\[[0-9;]*m//g' "$LOG" 2>/dev/null | grep "TAB-KEY"
  exit 1
fi

# Focus is on the note. Tab must take it off; the marker then shows where the
# keys went.
wtype ${WT_MOD:+-M shift} -k "${1:-Tab}"
sleep 0.3
wtype "MARK"
wait "$shoot_pid" 2>/dev/null
sed 's/\x1b\[[0-9;]*m//g' "$LOG" | grep "TAB-KEY"
