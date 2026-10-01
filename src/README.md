# Plugin source

- `protondrive/`: Python runtime, compatible with Debian 12's Python 3.11.
- `bin/`: command launchers and the private D-Bus/keyring session helper.
- `omv/`: PHP RPC modules, configuration models, and workbench pages.
- `salt/`: deployment states and service/configuration templates.

[Package assembly](../infra/nix/package.nix) maps these sources to `/usr/sbin`, `/usr/share/openmediavault*`, and
`/srv/salt/omv/deploy/protondrive`. `/srv` is OMV's installed Salt tree, not a separate service in this repository. The
Proton CLI is fetched separately by Nix into `/usr/lib/openmediavault-protondrive`.
