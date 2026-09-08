#!/bin/bash
# Renders every state the harness knows about.
set -u
A="$(dirname "$(readlink -f "$0")")"
for state in locked locked-long locked-none list long many empty no-results toast \
             details details-totp details-totp60 details-legacy details-card \
             details-notes details-many details-error details-loading \
             details-edit details-edit-note details-delete \
             create create-empty create-card create-note; do
  "$A/shoot.sh" "$state" >/dev/null
  printf '  %s\n' "$state"
done
echo "shots in ${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit/shots"
