#!/bin/bash
# Render one OmaPass state in a throwaway bar and screenshot it.
#
# The workspace is built outside the plugin folder on purpose: Omarchy's
# validator refuses a symlink anywhere inside a plugin, and this needs to link
# the shell's own Commons/Ui modules next to the widget to load it.
#
# The rendered copy differs from the shipped widget in one way only: its helper
# is stubbed to `true`, so the live vault cannot replace the fixtures the
# moment the popup opens. See README.md in this directory.
set -u

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PLUGIN="$(cd "$HERE/../.." && pwd)"
SHELL_DIR="${OMARCHY_SHELL_DIR:-/usr/share/omarchy/shell}"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit"
SHOTS="$WORK/shots"

state="${1:?usage: shoot.sh <state>}"
mkdir -p "$WORK" "$SHOTS"

ln -sfn "$SHELL_DIR/Commons" "$WORK/Commons"
ln -sfn "$SHELL_DIR/Ui" "$WORK/Ui"
ln -sfn "$PLUGIN/components" "$WORK/components"
ln -sfn "$PLUGIN/Model.js" "$WORK/Model.js"
ln -sfn "$PLUGIN/omapass-agent.py" "$WORK/omapass-agent.py"
cp "$HERE/harness.qml" "$WORK/harness.qml"

sed -e 's|^\( *\)return \["/usr/bin/python3", "-I", root.helperPath, "request", "-"\]|\1return ["/usr/bin/true"]|' \
    "$PLUGIN/BarWidget.qml" > "$WORK/OmaPassWidget.qml"

OMAPASS_AUDIT_BACKDROP="${OMAPASS_AUDIT_BACKDROP:-}" \
OMAPASS_AUDIT_STATE="$state" \
  quickshell -p "$WORK/harness.qml" >"$SHOTS/$state.log" 2>&1 &
qs_pid=$!
trap 'kill "$qs_pid" 2>/dev/null; wait "$qs_pid" 2>/dev/null' EXIT
# Wait for the harness surface to actually map. Without this, a quickshell
# that failed to launch means grim photographs the desktop instead, and the
# result looks like a screenshot of the widget to everything downstream.
# The popup's own layer, and this instance's: the fake bar maps first, and a
# capture once came back as the desktop with only that bar waited for. The
# popup is a full-screen overlay that closes on any click, so on a desktop in
# use it can also be gone again by the time of the capture below.
popup_mapped() {
  grep -qE "namespace: omarchy-keyboard-panel, pid: ${qs_pid}([^0-9]|\$)" <<<"$1"
}
mapped=0
for _ in $(seq 1 80); do
  layers="$(hyprctl layers 2>/dev/null)"
  if grep -q "namespace: omapass-audit," <<<"$layers" && popup_mapped "$layers"; then
    mapped=1; break
  fi
  sleep 0.1
done
if [ "$mapped" != 1 ]; then
  echo "harness popup never mapped for '$state'; see $SHOTS/$state.log" >&2
  kill "$qs_pid" 2>/dev/null
  wait "$qs_pid" 2>/dev/null
  exit 1
fi

# The tab-key state needs longer: it has to be driven after it renders.
sleep "${OMAPASS_AUDIT_HOLD:-3.0}"

# Checked again here, not only before the wait. The surface that maps first
# is the fake bar, and quickshell can still die while the popup is being
# built: the layers go with it, and grim would then photograph whatever the
# desktop happens to be showing and hand it back as a picture of the widget.
if ! kill -0 "$qs_pid" 2>/dev/null || ! popup_mapped "$(hyprctl layers 2>/dev/null)"; then
  echo "harness died before '$state' could be captured; see $SHOTS/$state.log" >&2
  kill "$qs_pid" 2>/dev/null
  wait "$qs_pid" 2>/dev/null
  exit 1
fi

grim "$SHOTS/$state.png" 2>/dev/null
kill "$qs_pid" 2>/dev/null
wait "$qs_pid" 2>/dev/null
echo "$SHOTS/$state.png"
