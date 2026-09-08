"""OmaPass helper: the backend behind the Omarchy 1Password widget.

    config     tunables and the vocabulary matched against
    paths      the private runtime directory and how files there are opened
    clipboard  putting a secret on the clipboard and taking it off again
    fields     resolving "the password" to an actual field of an item
    service    vault state, caching, and the actions the widget calls
    daemon     the Unix socket and the process behind it
    cli        argument parsing and the stdin request path
"""

__all__ = ["config", "paths", "clipboard", "fields", "service", "daemon", "cli"]
