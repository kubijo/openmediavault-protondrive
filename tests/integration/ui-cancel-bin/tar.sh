#!/bin/sh
# Test-only archive hold: the real runner has already stopped its containers.
set -eu
test -f /var/lib/protondrive-interactive-vm
test -f /var/lib/protondrive-ui-flow/config.json
touch /var/lib/protondrive-ui-flow/archiving
exec /usr/bin/sleep 300
