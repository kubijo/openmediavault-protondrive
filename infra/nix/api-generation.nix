{
  pkgs,
  src,
  webApp,
  qaPython,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = src + "/src/api"; };
  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python3; }).overrideScope
      (
        pkgs.lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
        ]
      );
  python = pythonSet.mkVirtualEnv "protondrive-api-generators" workspace.deps.all;
  dependencies =
    pkgs.runCommand "protondrive-buf-dependencies"
      {
        nativeBuildInputs = [
          pkgs.buf
          pkgs.cacert
        ];
        outputHashMode = "recursive";
        outputHashAlgo = "sha256";
        outputHash = "sha256-FHWO4jScAnsb5BjWZbbEXGlfWMh3MKR4f9xPwzfJc0I=";
      }
      ''
        export BUF_CACHE_DIR="$out"
        cp -r ${src}/proto proto
        cp ${src}/buf.yaml ${src}/buf.lock .
        chmod u+w buf.lock
        buf dep update
        cmp buf.lock ${src}/buf.lock
      '';
  buf = pkgs.writeShellApplication {
    name = "buf";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      buf_cache=$(mktemp -d)
      trap 'rm -rf "$buf_cache"' EXIT
      cp -r ${dependencies}/. "$buf_cache/"
      chmod -R u+w "$buf_cache"
      export BUF_CACHE_DIR="$buf_cache"
      ${pkgs.lib.getExe pkgs.buf} "$@"
    '';
  };
  generate = pkgs.writeShellApplication {
    name = "protondrive-generate-api";
    runtimeInputs = [
      python
      buf
      pkgs.nodejs_latest
    ];
    text = ''
      export PATH="${webApp.tools}/node_modules/.bin:$PATH"
      exec python ${src}/tools/generate_api.py "$@"
    '';
  };
  check = pkgs.runCommand "protondrive-generated-bindings" { nativeBuildInputs = [ pkgs.uv ]; } ''
    cp -r ${src} source
    chmod -R u+w source
    cd source
    ${pkgs.lib.getExe generate} --check
    export UV_OFFLINE=1
    export UV_CACHE_DIR="$TMPDIR/uv-cache"
    PYTHONPATH="$PWD/src" ${qaPython}/bin/python tools/api_wheels.py --check
    touch "$out"
  '';
in
{
  inherit
    generate
    check
    dependencies
    buf
    ;
}
