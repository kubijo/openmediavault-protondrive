{ pkgs, src }:
let
  inherit (builtins) fromJSON readFile;
  manifest = fromJSON (readFile (src + "/src/web/app/package.json"));
  appSrc = src + "/src/web/app";
  pnpmDeps = pkgs.fetchPnpmDeps {
    pname = "protondrive-web";
    version = "1";
    src = appSrc;
    inherit (pkgs) pnpm;
    fetcherVersion = 4;
    hash = "sha256-H6tx+p32HMzhY1vUouHMlgl2cMELRX2L6H4qLWfLJEk=";
  };
  buildPnpm = pkgs.writeShellScriptBin "pnpm" ''
    export pnpm_config_pm_on_fail=download
    exec ${pkgs.lib.getExe pkgs.pnpm} "$@"
  '';
in
assert manifest.devEngines.runtime.version == pkgs.nodejs_latest.version;
pkgs.stdenvNoCC.mkDerivation {
  pname = "protondrive-web";
  version = "1";
  src = appSrc;
  outputs = [
    "out"
    "tools"
  ];
  inherit pnpmDeps;
  nativeBuildInputs = [
    pkgs.nodejs_latest
    buildPnpm
    pkgs.pnpmConfigHook
  ];
  buildPhase = ''
    runHook preBuild
    pnpm typecheck
    pnpm lint:scss
    pnpm check:scss-types
    pnpm test
    pnpm build
    runHook postBuild
  '';
  installPhase = ''
    mkdir -p "$out"
    cp -r dist/. "$out/"
    mkdir -p "$tools"
    cp -r node_modules "$tools/"
  '';
}
