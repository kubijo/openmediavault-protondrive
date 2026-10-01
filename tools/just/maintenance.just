# Report available updates without changing pins or lockfiles.
outdated *args:
    nix run {{ nix_args }} .#outdated -- "$@"

# Run security and dependency hygiene audits defined in Nix.
audit *args:
    nix run {{ nix_args }} .#audit -- "$@"
