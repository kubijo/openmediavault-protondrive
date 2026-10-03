set default-list
set positional-arguments
set shell := ["/bin/sh", "/root/guest-recipe.sh", "-c"]

# Show account and backup status.
status:
    omv-protondrive status

# Show the plugin and web server service state.
services:
    systemctl --no-pager --full status omv-protondrive nginx

# Follow plugin logs; pass extra journalctl options as needed.
logs *args:
    journalctl --unit=omv-protondrive --follow "$@"

# Restart the Proton Drive service.
restart:
    systemctl restart omv-protondrive

# Show OMV modules waiting for configuration apply.
pending:
    python3 /root/guest_commands.py pending

# Apply plugin-related changes, refusing unrelated pending changes.
apply:
    python3 /root/guest_commands.py apply

# Rebuild OMV's web interface definitions.
workbench:
    omv-mkworkbench all
