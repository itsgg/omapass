#!/bin/bash
# Sends a real Return to the locked card and follows the unlock handoff.
#
# While the popup is open it is a full-screen overlay holding the keyboard, so
# 1Password's authorization dialog could be neither typed into nor clicked.
# Unlocking has to close the popup, then reopen it on the locked card if the
# dialog is cancelled, or on the search field once the vault opens. The first
# step needs a real keypress; the harness plays the dialog's answers.
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit"
LOG="$WORK/shots/locked-handoff.log"

rm -f "$LOG"
OMAPASS_AUDIT_HOLD=6 "$HERE/shoot.sh" locked-handoff >/dev/null 2>&1 &
shoot_pid=$!

# Never send synthetic input before the target surface is mapped and the
# harness says the locked card has the keyboard.
ready=0
for _ in $(seq 1 100); do
  if grep -q "UNLOCK-HANDOFF result=fail" "$LOG" 2>/dev/null; then
    # The locked card did not take the keyboard. Nothing is sent: a Return
    # sent now would land in whatever window holds it.
    sed 's/\x1b\[[0-9;]*m//g' "$LOG" | grep "UNLOCK-HANDOFF"
    kill "$shoot_pid" 2>/dev/null
    exit 1
  fi
  if hyprctl layers 2>/dev/null | grep -q "omapass-audit" \
     && grep -q "UNLOCK-HANDOFF ready" "$LOG" 2>/dev/null; then ready=1; break; fi
  sleep 0.1
done
if [ "$ready" != 1 ]; then
  echo "UNLOCK-HANDOFF harness never became ready; sending no input"
  kill "$shoot_pid" 2>/dev/null
  exit 1
fi

wtype -k Return
wait "$shoot_pid" 2>/dev/null
shot_status=$?
out="$(sed 's/\x1b\[[0-9;]*m//g' "$LOG" | grep "UNLOCK-HANDOFF" | grep -v "UNLOCK-HANDOFF ready")"
printf '%s\n' "$out"
# A pass marker is not enough: the harness can log it and then lose its popup
# before the capture, which shoot.sh reports by exiting non-zero.
if [ "$shot_status" != 0 ]; then
  echo "UNLOCK-HANDOFF result=fail (the harness failed after the checks; see $LOG)"
  exit 1
fi
grep -q "UNLOCK-HANDOFF result=pass" <<<"$out" && exit 0
grep -q "UNLOCK-HANDOFF result=" <<<"$out" || echo "UNLOCK-HANDOFF result=fail (Return never closed the popup)"
exit 1
