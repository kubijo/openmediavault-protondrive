set default-list
set positional-arguments
set shell := ["bash", "-euo", "pipefail", "-c"]
set working-directory := "../.."

nix_args := env("NIX_ARGS", "")
