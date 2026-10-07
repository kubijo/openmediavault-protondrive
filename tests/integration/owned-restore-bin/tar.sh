#!/bin/sh
# Hold extraction only while an owned development restore fixture is armed.
set -eu
root=/var/lib/protondrive-owned-ui-restore
if [ -f /var/lib/protondrive-interactive-vm ] && [ -f "$root/active" ]; then
    IFS= read -r token <"$root/active"
    test -f "$root/$token/owner.json"
    touch "$root/$token/held"
    /usr/bin/sleep 300
    exit 1
fi
exec /usr/bin/tar "$@"
