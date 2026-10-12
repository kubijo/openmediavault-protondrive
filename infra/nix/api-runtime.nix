{ pkgs, src }:
let
  inherit (builtins) fromJSON readFile;
  wheels = map (
    wheel:
    pkgs.fetchurl {
      inherit (wheel) name url;
      sha256 = pkgs.lib.removePrefix "sha256:" wheel.hash;
    }
  ) (fromJSON (readFile (src + "/src/api/runtime-wheels.json")));
in
pkgs.runCommand "protondrive-api-debian-runtime"
  {
    nativeBuildInputs = [ pkgs.unzip ];
  }
  ''
    mkdir -p "$out"
    ${pkgs.lib.concatMapStringsSep "\n" (wheel: ''unzip -q -o ${wheel} -d "$out"'') wheels}
    # These are unmodified manylinux/pure Python wheels for Debian's interpreter.
    # Never copy a Nix virtualenv: its patched extensions refer to the Nix store.
    if find "$out" -type d -name '*.data' | read -r _; then
      echo 'Wheel requires an explicit .data installation mapping' >&2
      exit 1
    fi
  ''
