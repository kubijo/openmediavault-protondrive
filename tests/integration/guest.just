set default-list
set positional-arguments
set shell := ["/bin/sh", "/usr/local/lib/omv-protondrive-vm/guest-recipe.sh", "-c"]

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

# Preserve a corrupt vendor event lock while no Proton operation is running.
repair-cli-lock:
    omv-protondrive repair-cli-lock

# Verify corrupt-lock diagnosis and repair using the existing development account.
check-cli-lock:
    env PYTHONPATH=/usr/share/openmediavault-protondrive python3 /usr/local/lib/omv-protondrive-vm/owned_cli_lock_guest.py

# Show OMV modules waiting for configuration apply.
pending:
    python3 /usr/local/lib/omv-protondrive-vm/guest_commands.py pending

# Apply plugin-related changes, refusing unrelated pending changes.
apply:
    python3 /usr/local/lib/omv-protondrive-vm/guest_commands.py apply

# Rebuild OMV's web interface definitions.
workbench:
    omv-mkworkbench all
