#!/bin/bash
# Renders every state the harness knows about.
set -u
A="$(dirname "$(readlink -f "$0")")"
SHOTS="${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit/shots"
for state in locked locked-long locked-none list long many empty no-results toast \
             details details-totp details-totp60 details-legacy details-card \
             details-notes details-many details-error details-loading \
             details-edit details-edit-note details-delete \
             create create-empty create-card create-note; do
  "$A/shoot.sh" "$state" >/dev/null
  printf '  %s\n' "$state"
done

# Tab order is the one thing a screenshot cannot show: a control Tab skips
# looks identical to one it reaches. These states enumerate the focus chain
# instead, and print it.
echo "tab order:"
for state in tab-order-login tab-order-card tab-order-note \
             tab-order-view-list tab-order-view-details; do
  "$A/shoot.sh" "$state" >/dev/null
  sed 's/\x1b\[[0-9;]*m//g' "$SHOTS/$state.log" | grep -h "TAB-ORDER" | sed 's/^.*TAB-ORDER/  /'
done
# The focus chain is a static graph; it cannot show a field that swallows the
# Tab key. That needs a real keypress.
echo "tab key:"
"$A/tabkey.sh" Tab | sed 's/^.*TAB-KEY/  /'

echo "shots in ${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit/shots"
