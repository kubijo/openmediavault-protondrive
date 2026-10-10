{
  pkgs,
  src,
  vmCommand,
}:
let
  inherit (builtins) elemAt fromJSON readFile;
  inherit (pkgs) lib pnpm;
  manifest = fromJSON (readFile ../../tools/web-probe/package.json);
  nodejs = pkgs.nodejs_latest;
  probeSrc = src + "/tools/web-probe";
  playwrightVersion = manifest.devDependencies."playwright-core";
  typescriptVersion = manifest.devDependencies.typescript;
  fonts = pkgs.makeFontsConf { fontDirectories = [ pkgs.dejavu_fonts ]; };
  pnpmVersion = elemAt (lib.splitString "@" manifest.packageManager) 1;
  pnpmDeps = pkgs.fetchPnpmDeps {
    pname = "omv-protondrive-web-probe";
    version = "1";
    src = probeSrc;
    inherit pnpm;
    fetcherVersion = 4;
    hash = "sha256-02VPMxsLfbzdKcvjW3kL/9IAHOjuA+5bHrYVuAIcAcs=";
  };
  # pnpm 12 records the package-manager pin in a second YAML document.
  # pnpmConfigHook's pm_on_fail=ignore makes pnpm reject that native lockfile.
  buildPnpm = pkgs.writeShellScriptBin "pnpm" ''
    export pnpm_config_pm_on_fail=download
    exec ${lib.getExe pnpm} "$@"
  '';
  checked = pkgs.stdenvNoCC.mkDerivation {
    pname = "omv-protondrive-web-probe";
    version = "1";
    src = probeSrc;
    inherit pnpmDeps;
    FONTCONFIG_FILE = fonts;
    nativeBuildInputs = [
      nodejs
      buildPnpm
      pkgs.pnpmConfigHook
    ];
    buildPhase = ''
      runHook preBuild
      pnpm typecheck
      pnpm test
      PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers} pnpm test:browser
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/lib/web-probe"
      cp -r src package.json node_modules "$out/lib/web-probe/"
      runHook postInstall
    '';
  };
  probe = pkgs.writeShellApplication {
    name = "protondrive-web-probe";
    runtimeInputs = [
      nodejs
      pkgs.bash
    ];
    text = ''
      export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
      export FONTCONFIG_FILE=${fonts}
      export PROTONDRIVE_VM_COMMAND=${vmCommand}
      exec node ${checked}/lib/web-probe/src/main.ts "$@"
    '';
  };
in
assert lib.assertMsg (manifest.devEngines.runtime.version == nodejs.version) ''
  web-probe package.json must match nodejs_latest ${nodejs.version}.
'';
assert lib.assertMsg (pnpmVersion == pnpm.version) ''
  web-probe package.json must match pnpm ${pnpm.version}.
'';
assert lib.assertMsg (playwrightVersion == pkgs.playwright-driver.version) ''
  web-probe playwright-core ${playwrightVersion} must match the Nix browser driver ${pkgs.playwright-driver.version}.
'';
assert lib.assertMsg (typescriptVersion == pkgs.typescript.version) ''
  web-probe TypeScript ${typescriptVersion} must match the pinned latest TypeScript ${pkgs.typescript.version}.
'';
{
  inherit checked probe;
}
