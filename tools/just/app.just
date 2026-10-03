import 'prelude.just'

# Build the plugin Debian package.
build:
    nix build {{ nix_args }} .#openmediavault-protondrive -L
