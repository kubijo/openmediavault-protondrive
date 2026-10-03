import 'prelude.just'

# Start the VM in the background and enter its SSH shell.
up *args:
    nix run {{ nix_args }} .#vm-up -- "$@"

# Open an interactive root shell in the running VM.
ssh *args:
    nix run {{ nix_args }} .#vm -- ssh "$@"

# Shut down the VM and retain its disk and login state.
down *args:
    nix run {{ nix_args }} .#vm -- down "$@"

# Show VM state and local connection details.
status *args:
    nix run {{ nix_args }} .#vm -- status "$@"

# Install the current plugin without resetting guest settings.
install *args:
    nix run {{ nix_args }} .#vm-install -- "$@"

# Delete stopped VM state; requires --yes.
reset *args:
    nix run {{ nix_args }} .#vm -- reset "$@"

# Save the stopped VM's disk and login state as a named restore point.
snapshot *args:
    nix run {{ nix_args }} .#vm -- snapshot "$@"

# Revert a stopped VM to a named disk snapshot.
restore *args:
    nix run {{ nix_args }} .#vm -- restore "$@"

# Check the running VM's overview, settings, and backup sets in Chromium; save screenshots.
probe *args:
    nix run {{ nix_args }} .#web-probe -- "$@"

# Exercise disposable init, web changes, snapshot restore, reset, and backup restore.
flow:
    python3 tools/vm_flow.py
