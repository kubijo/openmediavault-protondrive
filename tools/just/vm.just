import 'prelude.just'

# Start the VM in the background and enter its SSH shell.
up *args:
    nix run {{ nix_args }} .#vm-up -- "$@"

# Open an interactive root shell in the running VM.
ssh *args:
    nix run {{ nix_args }} .#vm -- ssh "$@"

# Run an argv-safe command in the running VM without opening a shell.
exec *args:
    nix run {{ nix_args }} .#vm-command -- exec -- "$@"

# Query the Proton service socket with structured JSON (status, probe, or prepare).
rpc *args:
    nix run {{ nix_args }} .#vm-command -- rpc "$@"

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

# Run an authenticated UI backup, restore check, and cancellation with video evidence.
live *args:
    nix run {{ nix_args }} .#web-probe -- --live "$@"

# Interrupt a real fixture upload and verify safe retry and restore through the web UI.
retry *args:
    nix run {{ nix_args }} .#web-probe -- --live --upload-retry "$@"

# Verify real runner SIGKILL, failed container restart, and abrupt VM reboot recovery.
crash *args:
    nix run {{ nix_args }} .#web-probe -- --live --crash-recovery "$@"

# Alias for the disposable local regression suite.
flow *args:
    nix run {{ nix_args }} .#test-regression -- "$@"
