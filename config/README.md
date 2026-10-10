# Repository configuration

`sources.json` owns the pinned Proton CLI and Debian test images, their upstream update metadata, and compatibility
reasons. Nix consumes the download URLs and hashes; the read-only version report consumes the update providers. Changing
a pin is a separate, reviewed operation. Python does not download or assemble the shipped CLI.

`gitleaks.toml` extends the default secret rules and excludes generated environments, caches, and build outputs.
