import 'prelude.just'

# Format the tree in place.
format *args:
    nix run {{ nix_args }} .#format -- "$@"

# Run file and project linters.
lint *args:
    nix run {{ nix_args }} .#lint -- "$@"

# Check all Nix flake outputs.
check:
    nix flake check {{ nix_args }} -L --keep-going

# Audit tracked-file selection and declared check scope.
coverage *args:
    nix run {{ nix_args }} .#coverage -- "$@"

# Reject source files missing from the Git flake.
tracked:
    nix run {{ nix_args }} .#tracked

# Run the complete Nix-defined QA pipeline.
validate:
    nix run {{ nix_args }} .#validate

# Report available updates without changing pins or lockfiles.
outdated *args:
    nix run {{ nix_args }} .#outdated -- "$@"

# Run security and dependency hygiene audits defined in Nix.
audit *args:
    nix run {{ nix_args }} .#audit -- "$@"
