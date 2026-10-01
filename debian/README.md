# Debian packaging

Control metadata, maintainer scripts, and package policy for OMV 7 / Debian 12. This directory stays at the repository
root for Debian tools and nix-tools' debputy integration.

[Package assembly](../infra/nix/package.nix) owns the source-to-installation mapping and builds the `.deb` with dpkg.
`rules` delegates to that Nix build; there is no separate debhelper install manifest.
