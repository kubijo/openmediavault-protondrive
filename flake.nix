{
  description = "OpenMediaVault plugin for backups to Proton Drive";

  inputs = {
    nix-tools.url = "github:kubijo/nix-tools";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        nixpkgs.follows = "nixpkgs";
      };
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
        nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs =
    {
      self,
      nix-tools,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (builtins) filter;
      system = "x86_64-linux";
      basePkgs = nixpkgs.legacyPackages.${system};
      pkgs = basePkgs // {
        nix = basePkgs.nixVersions.latest;
        php = basePkgs.php85;
        python3 = basePkgs.python314;
        python3Packages = basePkgs.python314.pkgs;
      };
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      pythonSet =
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python3; }).overrideScope
          (
            pkgs.lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
            ]
          );
      qaPython = pythonSet.mkVirtualEnv "protondrive-tooling" workspace.deps.default;
      maintenance = import ./infra/nix/maintenance.nix {
        inherit pkgs;
        python = qaPython;
        src = self;
        nixToolsRevision = nix-tools.rev or null;
      };
      qa = import ./infra/nix/qa.nix {
        inherit pkgs;
        python = qaPython;
        src = self;
      };
      shellFiles = [
        "*.sh"
        "omv-protondrive-auth"
        "*.postinst"
        "*.prerm"
        "*.postrm"
      ];
      cli = import ./infra/nix/proton-cli.nix { inherit pkgs; };
      deb = import ./infra/nix/package.nix {
        inherit pkgs cli;
        src = self;
      };
      project = nix-tools.lib.configure {
        inherit system;
        toolPkgs = pkgs;
        src = self;
        exclude = [
          ".tmp/**"
          "result/**"
          "**/__pycache__/**"
          "*.pyc"
        ];
        # Preserve the former whole-project whitespace check on shared default exclusions.
        unexclude = [
          ".editorconfig"
          ".gitignore"
          "*.lock"
        ];
        coverage = import ./infra/nix/coverage.nix;
        format = {
          python.includes = [
            "*.py"
            "src/bin/omv-protondrive"
          ];
          shell.includes = [
            "*.sh"
            "src/bin/omv-protondrive-auth"
            "debian/*.postinst"
            "debian/*.prerm"
            "debian/*.postrm"
          ];
          php = true;
          debian = true;
          # Generic template whitespace is normalized without rewriting Jinja/YAML semantics.
          whitespace = {
            configFile = ./.editorconfig;
            includes = [
              "*.sls"
              "*.j2"
              "*.jinja"
              "*.conf"
              "debian/*"
              "debian/source/format"
              ".editorconfig"
              ".gitignore"
              "flake.lock"
              "uv.lock"
            ];
            exclude = [
              "debian/control"
              "debian/copyright"
              "debian/tests/control"
              "debian/watch"
              "debian/*.preinst"
              "debian/*.postinst"
              "debian/*.prerm"
              "debian/*.postrm"
              "debian/*.md"
            ];
          };
          exclude = [ "LICENSE" ];
        };
        lint = {
          nix = true;
          # actionlint 1.7.12 predates this GA runner label; keep other label errors fatal.
          workflows.extraOptions = [
            "-ignore"
            ''^label "ubuntu-26\.04" is unknown\.''
          ];
          python.includes = [
            "*.py"
            "omv-protondrive"
          ];
          shell.includes = shellFiles;
          php.extraOptions = [ "--semantics" ];
          debian = true;
          extraCheckers.php-syntax = {
            command = "${pkgs.php}/bin/php";
            options = [ "-l" ];
            includes = [
              "*.inc"
              "*.php"
            ];
          };
          salt.exclude = [ "tools/tests/fixtures/templates/invalid/**" ];
          links = true;
          whitespace = {
            configFile = ./.editorconfig;
            includes = [ "*" ];
            exclude = [ "LICENSE" ];
          };
          extraProjectCheckers = {
            templates = {
              command = "${qaPython}/bin/python";
              options = [ "tools/check_templates.py" ];
            };
            just = {
              command = "${pkgs.just}/bin/just";
              options = [ "--summary" ];
            };
          };
        };
        validate.steps = [
          {
            name = "Tracked source";
            run = pkgs.lib.getExe qa.tracked;
          }
          {
            name = "Runtime tests";
            run = pkgs.lib.getExe qa.test;
          }
          {
            name = "Hermetic formatting";
            run = "test -e ${project.checks.formatting}";
          }
          {
            name = "Hermetic linting";
            run = "test -e ${project.checks.linting}";
          }
          {
            name = "Hermetic runtime tests";
            run = "test -e ${qa.hermeticTests}";
          }
          {
            name = "Debian package";
            run = "test -f ${deb}/openmediavault-protondrive_7.0.0_amd64.deb";
          }
        ];
      };
    in
    {
      formatter.${system} = project.formatter;
      packages.${system} = {
        default = deb;
        openmediavault-protondrive = deb;
        proton-drive-source = cli;
      };
      checks.${system} = project.checks // {
        tests = qa.hermeticTests;
      };
      apps.${system} = project.apps // {
        audit = {
          type = "app";
          program = pkgs.lib.getExe maintenance.audit;
        };
        outdated = {
          type = "app";
          program = pkgs.lib.getExe maintenance.outdated;
        };
        test = {
          type = "app";
          program = pkgs.lib.getExe qa.test;
        };
        tracked = {
          type = "app";
          program = pkgs.lib.getExe qa.tracked;
        };
        test-debian = {
          type = "app";
          program = pkgs.lib.getExe (
            pkgs.writeShellApplication {
              name = "protondrive-test-debian";
              runtimeInputs = [
                qaPython
                pkgs.docker-client
              ];
              text = ''
                exec python ${self}/tests/integration/debian_smoke.py \
                  --package ${deb}/openmediavault-protondrive_7.0.0_amd64.deb "$@"
              '';
            }
          );
        };
        test-vm = {
          type = "app";
          program = "${
            import ./infra/nix/test-vm.nix {
              inherit pkgs;
              python = qaPython;
              src = self;
              package = deb;
            }
          }/bin/protondrive-test-vm";
        };
      };
      devShells.${system}.default = pkgs.mkShellNoCC {
        UV_NO_SYNC = "1";
        UV_PYTHON = "${pkgs.python3}/bin/python3";
        UV_PYTHON_DOWNLOADS = "never";
        # Entering the editing shell must not first build the validation gate.
        packages =
          (filter (package: pkgs.lib.getExe package != project.apps.validate.program) project.packages)
          ++ [
            pkgs.just
            pkgs.uv
            qaPython
            pkgs.gnutar
            pkgs.zstd
            pkgs.acl
            pkgs.dpkg
            pkgs.php
          ];
      };
    };
}
