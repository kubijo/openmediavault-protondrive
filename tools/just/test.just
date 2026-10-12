import 'prelude.just'

# Run runtime and tooling unit tests.
unit:
    nix run {{ nix_args }} .#test

# Test the CLI, runtime, and keyring in a Debian container.
debian:
    nix run {{ nix_args }} .#test-debian

# Test OMV in a fresh VM overlay over a cached base.
vm *args:
    nix run {{ nix_args }} .#test-vm -- "$@"

# Own VM setup, UI/service regressions and teardown with reusable cache layers.
regression *args:
    nix run {{ nix_args }} .#test-regression -- "$@"
