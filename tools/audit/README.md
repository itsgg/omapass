# Visual audit harness

Renders the widget in a throwaway bar and screenshots one named state, so a
layout change can be checked against pixels rather than against the belief that
the bindings resolve.

It exists because a headless load check proved nothing: it happily accepted a
toast nested inside an invisible view, a locked card centred in the left half
of its own popup, and a footer running through the label beside it.

    tools/audit/shoot.sh list
    tools/audit/shoot-all.sh          # every state, into tools/audit/shots/

Needs a running Hyprland session with Omarchy's shell modules installed. The
harness runs its own layer surface and never touches the real bar.

Two deviations in the copy it renders, both deliberate:

- The helper is stubbed to `true`, so the widget cannot replace the fixtures
  with the live vault the moment the popup opens.
- Fixtures are synthetic. Nothing here reads a real 1Password item.

`tabkey.sh` sends a real Tab to the note editor and reports where focus went.
The focus chain below is a static graph and cannot see a widget that accepts
Tab as text: enabling `activeFocusOnTab` on the bare `TextEdit` made the note
reachable and simultaneously inescapable, and only a real keypress showed it.

It also enumerates keyboard focus order. A control that Tab skips looks
exactly like one it reaches in a screenshot, so `shoot-all.sh` walks the focus
chain in each view and prints it. That is how the note editor was found to be
unreachable: a bare `TextEdit` defaults `activeFocusOnTab` to false, while the
`Ui/TextField` used everywhere else does not.

The list and details views deliberately have a single focus holder, so one
stop there is the correct result, not a gap. They get there differently: the
list keeps focus on the search field and binds Tab itself, to cycle the
category chips, while the details view leaves Tab unbound and simply has
nothing else focusable for it to reach.

States: `locked`, `locked-long`, `locked-none`, `list`, `long`, `many`,
`empty`, `no-results`, `toast`, `details`, `details-totp`, `details-totp60`,
`details-legacy`, `details-card`, `details-notes`, `details-many`,
`details-error`, `details-loading`, `details-edit`, `details-edit-note`,
`details-delete`, `create`, `create-card`, `create-note`, `create-empty`,
the `demo-*` states used to render the screenshots in the README, and the
`tab-order-*` states, which print a focus chain instead of an image.
