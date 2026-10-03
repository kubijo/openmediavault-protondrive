{
  pkgs,
  nix-tools,
  python,
  src,
}:
let
  inherit (builtins) toJSON;
  inherit (pkgs) lib;
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
  outdatedAdapter = app "outdated" [ pkgs.gh ] [ ];
  github = package: repo: { inherit package repo; };
  pypi = package: project: {
    inherit package project;
    provider = "pypi";
  };
in
{
  outdated = {
    enable = true;
    uv = true;
    githubActions = true;
    releases = {
      nix = (github pkgs.nix "NixOS/nix") // {
        tags = true;
      };
      host-nix = {
        versionCommand = [
          "nix"
          "--version"
        ];
        versionPattern = "nix .* (?P<version>[^ ]+)";
        repo = "NixOS/nix";
        tags = true;
      };
      uv = github pkgs.uv "astral-sh/uv";
      gitleaks = github pkgs.gitleaks "gitleaks/gitleaks";
      deptry = github pkgs.deptry "fpgmaas/deptry";

      # Selected tools have explicit sources;
      # nix-tools does not infer a package-to-upstream mapping
      # from its formatter or linter configuration.
      debputy = {
        package = (nix-tools.lib.packagesFor pkgs).debputy;
        provider = "git";
        url = "https://salsa.debian.org/debian/debputy.git";
        tagPattern = ''(?:archive/)?debian/(?P<version>[0-9]+(?:\.[0-9]+)+)'';
      };
      shellcheck = github pkgs.shellcheck "koalaman/shellcheck";
      actionlint = github pkgs.actionlint "rhysd/actionlint";
      biome = {
        package = pkgs.biome;
        provider = "npm";
        project = "@biomejs/biome";
      };
      deadnix = (github pkgs.deadnix "astro/deadnix") // {
        tags = true;
      };
      djlint = pypi pkgs.djlint "djlint";
      editorconfig-checker = github pkgs.editorconfig-checker "editorconfig-checker/editorconfig-checker";
      fd = github pkgs.fd "sharkdp/fd";
      just = github pkgs.just "casey/just";
      lychee = {
        package = pkgs.lychee;
        provider = "crates";
        project = "lychee";
      };
      mago = github pkgs.mago "carthage-software/mago";
      mdformat = pypi pkgs.python3Packages.mdformat "mdformat";
      mdformat-frontmatter = pypi pkgs.python3Packages.mdformat-frontmatter "mdformat-frontmatter";
      mdformat-gfm = pypi pkgs.python3Packages.mdformat-gfm "mdformat-gfm";
      mdformat-simple-breaks = pypi pkgs.python3Packages.mdformat-simple-breaks "mdformat-simple-breaks";
      nixfmt = github pkgs.nixfmt "NixOS/nixfmt";
      python3 = (github pkgs.python3 "python/cpython") // {
        tags = true;
      };
      ruff = github pkgs.ruff "astral-sh/ruff";
      salt-lint = pypi pkgs.salt-lint "salt-lint";
      shfmt = github pkgs.shfmt "mvdan/sh";
      statix = github pkgs.statix "oppiliappan/statix";
      taplo = github pkgs.taplo "tamasfe/taplo";
      treefmt = github pkgs.treefmt "numtide/treefmt";
      yamlfmt = github pkgs.yamlfmt "google/yamlfmt";
      yamllint = pypi pkgs.yamllint "yamllint";
    };
    adapters.application = {
      package = outdatedAdapter;
      timeout = 120;
    };
  };
  audit = app "audit" [ pkgs.uv pkgs.gitleaks pkgs.deptry ] [ "--plan" (toString auditPlan) ];
}
