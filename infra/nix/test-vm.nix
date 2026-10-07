{
  pkgs,
  python,
  src,
  package,
  guestBundle,
  mode ? "test",
}:
let
  inherit (builtins) fromJSON readFile;

  source = (fromJSON (readFile ../../config/sources.json)).debian-vm;
  image = pkgs.fetchurl {
    inherit (source) url sha512;
  };

  script =
    if mode == "test" then
      "tests/integration/vm.py"
    else if mode == "command" then
      "tools/vm_command.py"
    else
      "tools/interactive_vm.py";

  arguments =
    pkgs.lib.optional (mode == "up" || mode == "install") mode
    ++ pkgs.lib.optionals (mode == "test" || mode == "up" || mode == "install") [
      "--image"
      "${image}"
      "--package"
      "${package}/openmediavault-protondrive_7.0.0_amd64.deb"
    ]
    ++ pkgs.lib.optionals (mode != "command") [
      "--guest-bundle"
      "${guestBundle}/omv-protondrive-vm-tools.tar"
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
