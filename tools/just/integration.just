# Disposable Debian container: real CLI ABI, runtime and private keyring.
integration-debian:
    nix run {{ nix_args }} .#test-debian

# Disposable Debian 12 + OMV 7 QEMU guest; never targets the NAS.
integration-vm:
    nix run {{ nix_args }} .#test-vm
