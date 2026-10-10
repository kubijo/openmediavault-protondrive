{
  pkgs,
  python,
  src,
}:
let
  checkBranding = pkgs.writeShellApplication {
    name = "check-vm-branding";
    runtimeInputs = [
      python
      pkgs.nginx
    ];
    text = ''
      export PYTHONPATH="$PWD/tools:$PWD/tools/tests"
      export PROTONDRIVE_NGINX_FASTCGI_PARAMS=${pkgs.nginx}/conf/fastcgi_params
      exec python -m unittest test_interactive_vm.InteractiveVMTests.test_branding_is_idempotent_and_preserves_packaged_html
    '';
  };
  test = pkgs.writeShellApplication {
    name = "protondrive-test";
    runtimeInputs = [
      python
      pkgs.gnutar
      pkgs.zstd
      pkgs.acl
      pkgs.git
      pkgs.gitleaks
      pkgs.qemu_kvm
      pkgs.openssh
      pkgs.cdrkit
      pkgs.nginx
      pkgs.just
      pkgs.monit
    ];
    text = ''
      export PYTHONPATH="$PWD/src:$PWD/tests/unit:$PWD/tools"
      export PYTHONDONTWRITEBYTECODE=1
      export PROTONDRIVE_NGINX_FASTCGI_PARAMS=${pkgs.nginx}/conf/fastcgi_params
      python3 -m unittest discover -s tests/unit -v
      python3 -m unittest discover -s tools/tests -v
      python3 -m pytest src/api/tests -q
    '';
  };
  tracked = pkgs.writeShellApplication {
    name = "protondrive-check-tracked";
    runtimeInputs = [ pkgs.git ];
    text = ''
      exec ${python}/bin/python ${src}/tools/check_tracked.py
    '';
  };
  hermeticTests = pkgs.runCommand "protondrive-tests" { nativeBuildInputs = [ python ]; } ''
    cp -r ${src} source
    chmod -R u+w source
    cd source
    patchShebangs tests/fixtures tools/tests/fixtures
    export PROTON_HERMETIC_TESTS=1
    ${pkgs.lib.getExe test}
    touch "$out"
  '';
in
{
  inherit
    test
    tracked
    hermeticTests
    checkBranding
    ;
}
