{
  pkgs,
  src,
}:
let
  playwright = pkgs.python3Packages.playwright;
  python = pkgs.python3.withPackages (_: [ playwright ]);
in
assert pkgs.lib.assertMsg (playwright.version == pkgs.playwright-driver.version) ''
  Python Playwright ${playwright.version} does not match the Nix browser driver ${pkgs.playwright-driver.version}.
'';
pkgs.writeShellApplication {
  name = "protondrive-web-probe";
  runtimeInputs = [ python ];
  text = ''
    export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
    export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
    exec python ${src}/tools/web_probe.py "$@"
  '';
}
