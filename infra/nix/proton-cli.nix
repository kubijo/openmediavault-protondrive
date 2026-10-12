{ pkgs }:
let
  inherit (builtins) fromJSON readFile;
  source = (fromJSON (readFile ../../config/sources.json)).proton-cli;
in
pkgs.fetchurl {
  name = "proton-drive-${source.version}-linux-x64-baseline";
  inherit (source) url hash;
}
