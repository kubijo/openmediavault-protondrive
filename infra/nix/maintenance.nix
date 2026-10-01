{
  pkgs,
  python,
  src,
  nixToolsRevision,
}:
let
  inherit (builtins) toJSON;
  inherit (pkgs) lib;
  tool = package: repo: {
    inherit (package) version;
    inherit repo;
  };
  inventory = pkgs.writeText "protondrive-tool-inventory.json" (toJSON {
    nix-tools-revision = nixToolsRevision;
    tools = {
      "Nix (package set)" = tool pkgs.nix "NixOS/nix";
      "Python (tooling)" = (tool pkgs.python3 "python/cpython") // {
        tags = true;
      };
      uv = tool pkgs.uv "astral-sh/uv";
      ruff = tool pkgs.ruff "astral-sh/ruff";
      gitleaks = tool pkgs.gitleaks "gitleaks/gitleaks";
      deptry = tool pkgs.deptry "fpgmaas/deptry";
      actionlint = tool pkgs.actionlint "rhysd/actionlint";
      just = tool pkgs.just "casey/just";
      nixfmt = tool pkgs.nixfmt "NixOS/nixfmt";
      statix = tool pkgs.statix "oppiliappan/statix";
      deadnix = tool pkgs.deadnix "astro/deadnix";
      shellcheck = tool pkgs.shellcheck "koalaman/shellcheck";
      shfmt = tool pkgs.shfmt "mvdan/sh";
      "editorconfig-checker" = tool pkgs.editorconfig-checker "editorconfig-checker/editorconfig-checker";
      "salt-lint" = tool pkgs.salt-lint "warpnet/salt-lint";
      mago = tool pkgs.mago "carthage-software/mago";
      PHP = (tool pkgs.php "php/php-src") // {
        tags = true;
      };
      yamllint = tool pkgs.yamllint "adrienverge/yamllint";
    };
  });
  step = name: command: findingCodes: {
    inherit name command findingCodes;
  };
  secrets =
    mode: args:
    [
      (lib.getExe pkgs.gitleaks)
      mode
      "--config"
      "config/gitleaks.toml"
      "--redact"
      "--no-banner"
      "--no-color"
      "--exit-code"
      "10"
    ]
    ++ args
    ++ [ "." ];
  auditPlan = pkgs.writeText "protondrive-audit-plan.json" (toJSON [
    (step "Python lock consistency"
      [
        (lib.getExe pkgs.uv)
        "lock"
        "--check"
        "--offline"
        "--python"
        "${python}/bin/python"
      ]
      [ 1 ]
    )
    (step "Python vulnerability advisories (OSV)"
      [
        (lib.getExe pkgs.uv)
        "audit"
        "--locked"
        "--no-progress"
      ]
      [ 1 ]
    )
    (step "Unused and undeclared Python dependencies"
      [
        (lib.getExe pkgs.deptry)
        "."
        "--no-ansi"
      ]
      [ 1 ]
    )
    (
      (step "Git history secrets" (secrets "git" [ "--log-opts=--all" ]) [ 10 ])
      // {
        requiresHead = true;
      }
    )
    (step "Working tree secrets" (secrets "dir" [ ]) [ 10 ])
    (step "Staged secrets" (secrets "git" [
      "--pre-commit"
      "--staged"
    ]) [ 10 ])
  ]);
  app =
    name: extraInputs: args:
    pkgs.writeShellApplication {
      name = "protondrive-${name}";
      runtimeInputs = [
        python
        pkgs.git
        pkgs.cacert
      ]
      ++ extraInputs;
      text = ''
        exec ${python}/bin/python ${src}/tools/${name}.py ${lib.escapeShellArgs args} "$@"
      '';
    };
in
{
  outdated = app "outdated" [ pkgs.gh ] [ "--inventory" (toString inventory) ];
  audit = app "audit" [ pkgs.uv pkgs.gitleaks pkgs.deptry ] [ "--plan" (toString auditPlan) ];
}
