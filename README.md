# OmaPass 󰌆

**OmaPass** is a native, ultra-responsive [Omarchy](https://omarchy.org/) status bar widget and instant vault launcher for [1Password](https://1password.com/).

Built natively with Quickshell, Qt Quick/QML, and Python, OmaPass integrates directly into your Hyprland bar and desktop theme with sub-millisecond search, biometric unlock, and zero Electron bloat.

![OmaPass Preview](docs/preview.png)

## Features

- ⚡ **Instant Access (0ms latency)**: Lives directly inside `omarchy-shell` / Wayland layer-shell without heavy Electron overhead.
- 󰌆 **One-Press Credential Copying**:
  - <kbd>Enter</kbd> → Copy password to clipboard with automatic 30s wipe timer
  - <kbd>Shift</kbd> + <kbd>Enter</kbd> → Copy username
  - <kbd>Ctrl</kbd> + <kbd>Enter</kbd> → Copy One-Time Password (TOTP / 2FA)
  - <kbd>Alt</kbd> + <kbd>Enter</kbd> → Open website URL in default browser
- 󰓎 **Fast In-Memory Search**: Fuzzy search across titles, usernames, URLs, and vaults as you type.
- 󰤯 **Category Filtering**: Quick filter chips for *Logins*, *Credit Cards*, *Secure Notes*, and *Favorites*.
- 󰌏 **Biometric & System Unlock**: Leverages 1Password CLI (`op`) and system authentication (fingerprint / Polkit / PAM).
- 🎨 **Unified Omarchy Theming**: Automatically inherits your Omarchy colors, borders, font family, and blur.
- 🛡️ **Secret Isolation**: Decrypted secrets are fetched on-demand and piped directly to `wl-copy` without being logged or stored on disk.

## Requirements

- [Omarchy Linux](https://omarchy.org/)
- `1password-cli` (`op` >= 2.20)
- `wl-clipboard` (`wl-copy`)
- `notify-send` (libnotify)
- *(Optional)* `1password` desktop app running with "Integrate with 1Password CLI" enabled for biometric/fingerprint unlock.

## Installation

### Local Development / Symlink
Link the repository directly into your Omarchy plugins directory:

```bash
ln -s ~/Work/GG/omapass ~/.config/omarchy/plugins/gg.omapass
omarchy plugin enable gg.omapass
```

### Or Install from Git
```bash
omarchy plugin add https://github.com/itsgg/omapass.git --enable
```

## CLI Usage

The helper script `omapass-agent.py` can also be called directly:

```bash
# Check status
./omapass-agent.py status

# Trigger unlock
./omapass-agent.py unlock

# Sync vault metadata cache
./omapass-agent.py sync

# Search items
./omapass-agent.py list "github"

# Copy credential
./omapass-agent.py copy <item-id> password
```

## License

MIT © [Ganesh Gunasegaran](https://itsgg.com)
