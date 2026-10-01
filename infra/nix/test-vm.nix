{
  pkgs,
  python,
  src,
  package,
}:
let
  inherit (builtins) fromJSON readFile;
  source = (fromJSON (readFile ../../config/sources.json)).debian-vm;
  image = pkgs.fetchurl {
    inherit (source) url sha512;
  };
in
pkgs.writeShellApplication {
  name = "protondrive-test-vm";
  runtimeInputs = [
    python
    pkgs.qemu_kvm
    pkgs.cdrkit
    pkgs.openssh
  ];
  text = ''
    exec python ${src}/tests/integration/vm.py \
      --image ${image} \
      --package ${package}/openmediavault-protondrive_7.0.0_amd64.deb "$@"
  '';
}
