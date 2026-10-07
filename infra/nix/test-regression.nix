{
  pkgs,
  python,
  src,
  vmUp,
  vmControl,
  vmCommand,
  webProbe,
  testVm,
}:
pkgs.writeShellApplication {
  name = "protondrive-test-regression";
  runtimeInputs = [ python ];
  text = ''
    export PYTHONPATH="${src}/tools"
    exec python ${src}/tools/vm_flow.py \
      --vm-up ${vmUp} \
      --vm-control ${vmControl} \
      --vm-command ${vmCommand} \
      --web-probe ${webProbe} \
      --test-vm ${testVm} "$@"
  '';
}
