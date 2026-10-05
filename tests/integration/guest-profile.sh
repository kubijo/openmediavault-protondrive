#!/bin/sh
# Show guest development commands only in interactive test VM login shells.
if [ -t 1 ] && [ -f /var/lib/protondrive-interactive-vm ]; then
    if [ -n "${BASH_VERSION:-}" ]; then
        eval "$(JUST_COMPLETE=bash just)"
    fi
    python3 /usr/local/lib/omv-protondrive-vm/guest_commands.py banner
fi
