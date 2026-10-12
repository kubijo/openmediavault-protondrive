# Build and QA infrastructure

`nix/` defines package assembly, pinned downloads, tests, maintenance commands, and the disposable VM launcher. The root
[flake](../flake.nix) exposes these alongside nix-tools formatters and linters.

The consumer pins `nixos-unstable` and passes its selected package set through nix-tools' `toolPkgs` override. QA,
uv2nix, maintenance commands and the dev shell share that set, selecting current Nix, PHP 8.5 and Python 3.14. Update it
with `nix flake update nixpkgs`; nix-tools keeps its own independent pin.

Debian metadata stays in [`debian/`](../debian/README.md), where debputy and Debian tools expect it. GitHub requires
workflows in [`.github/workflows/`](../.github/workflows/ci.yml). All QA orchestration lives in Nix; just recipes call
it.
