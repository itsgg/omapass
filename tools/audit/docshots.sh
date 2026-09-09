#!/bin/bash
# Regenerates every screenshot the README shows, into docs/.
#
# These drifted once: the details header gained an edit and an archive
# button, the list header gained a create button, and the images kept
# showing the version before all three because refreshing them was a manual
# render and crop. It is one command now, so "the screenshots are current"
# is something you can check rather than remember.
#
# Needs a running Hyprland session, grim, and ImageMagick.
set -eu
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DOCS="$(cd "$HERE/../.." && pwd)/docs"
SHOTS="${XDG_CACHE_HOME:-$HOME/.cache}/omapass-audit/shots"

for tool in grim magick; do
  command -v "$tool" >/dev/null || { echo "$tool is not installed" >&2; exit 1; }
done

# state:file:height. The locked card is shorter, so it is cropped to itself
# rather than padded out to the height of the others.
for entry in \
  demo-list:preview:707 \
  demo-search:search:707 \
  demo-details:details:707 \
  demo-card:card:707 \
  demo-locked:locked:412 \
  demo-create:demo-create:707 \
  demo-edit:demo-edit:707 \
  demo-archive:demo-archive:707
do
  state="${entry%%:*}"; rest="${entry#*:}"
  name="${rest%%:*}"; height="${rest#*:}"
  raw="$SHOTS/$state.png"

  # shoot.sh discards grim's errors and exits 0 either way, and the cache
  # persists between runs, so without removing the old file first a failed
  # render would be cropped and published as though it were current. That is
  # the exact failure this script exists to prevent.
  rm -f "$raw"
  OMAPASS_AUDIT_BACKDROP=1 "$HERE/shoot.sh" "$state" >/dev/null
  [ -s "$raw" ] || { echo "$state: nothing was captured" >&2; exit 1; }

  # Cropped aside and checked before it is allowed into docs/: writing first
  # and validating after leaves a bad image in the tree when the check fails.
  tmp="$(mktemp --suffix=.png)"
  trap 'rm -f "$tmp"' EXIT
  magick "$raw" -crop "657x${height}+0+30" +repage "$tmp"

  # A crash before the widget painted leaves a flat backdrop, which crops and
  # writes without complaint. A real shot has thousands of colours.
  colours="$(magick identify -format '%k' "$tmp")"
  [ "$colours" -ge 50 ] || {
    echo "$state: captured $colours colours, so the widget did not render" >&2
    exit 1
  }
  mv "$tmp" "$DOCS/$name.png"
  printf '  %s -> docs/%s.png (%s colours)\n' "$state" "$name" "$colours"
done
echo "docs screenshots regenerated"
