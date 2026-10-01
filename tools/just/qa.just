# Format the tree in place.
format *args:
    nix run {{ nix_args }} .#format -- "$@"

lint *args:
    nix run {{ nix_args }} .#lint -- "$@"

check:
    nix flake check {{ nix_args }} -L --keep-going

# Audit tracked-file selection and declared check scope.
coverage *args:
    nix run {{ nix_args }} .#coverage -- "$@"

test:
    nix run {{ nix_args }} .#test

# Nix flakes omit untracked source; fail before reporting a clean validation.
tracked:
    nix run {{ nix_args }} .#tracked

validate:
    nix run {{ nix_args }} .#validate
