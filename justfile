set default-list
set positional-arguments
set shell := ["bash", "-euo", "pipefail", "-c"]

nix_args := env("NIX_ARGS", "")

import 'tools/just/qa.just'
import 'tools/just/package.just'
import 'tools/just/maintenance.just'
import 'tools/just/integration.just'
