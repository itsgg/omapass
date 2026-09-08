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

States: `locked`, `locked-long`, `locked-none`, `list`, `long`, `many`,
`empty`, `no-results`, `toast`, `details`, `details-totp`, `details-totp60`,
`details-legacy`, `details-card`, `details-notes`, `details-many`,
`details-error`, `details-loading`, and the `demo-*` states used to render the
screenshots in the README.
