{
  pkgs,
  python,
  src,
  package,
  mode ? "test",
}:
let
  inherit (builtins) fromJSON readFile;
  source = (fromJSON (readFile ../../config/sources.json)).debian-vm;
  image = pkgs.fetchurl {
    inherit (source) url sha512;
  };
  script = if mode == "test" then "tests/integration/vm.py" else "tools/interactive_vm.py";
  arguments =
    pkgs.lib.optional (mode != "test" && mode != "control") mode
    ++ pkgs.lib.optionals (mode != "control") [
      "--image"
      "${image}"
      "--package"
      "${package}/openmediavault-protondrive_7.0.0_amd64.deb"
    ]
    ++ pkgs.lib.optionals (mode != "test") [
      "--guest-just"
      "${pkgs.pkgsStatic.just}/bin/just"
    ];
in
pkgs.writeShellApplication {
  name = if mode == "test" then "protondrive-test-vm" else "protondrive-vm-${mode}";
  runtimeInputs = [
    python
    pkgs.qemu_kvm
    pkgs.cdrkit
    pkgs.openssh
    pkgs.dpkg
    pkgs.just
  ];
  text = ''
    export PYTHONPATH="${src}/tools"
    exec python ${src}/${script} ${pkgs.lib.escapeShellArgs arguments} "$@"
  '';
}
