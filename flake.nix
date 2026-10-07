{
  description = "OpenMediaVault plugin for backups to Proton Drive";

  inputs = {
    nix-tools.url = "github:kubijo/nix-tools/v0.9.0";
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
      qaPython = pythonSet.mkVirtualEnv "protondrive-tooling" workspace.deps.all;
      maintenance = import ./infra/nix/maintenance.nix {
        inherit pkgs nix-tools;
        python = qaPython;
        src = self;
      };
      qa = import ./infra/nix/qa.nix {
        inherit pkgs;
        python = qaPython;
        src = self;
      };
      vmGuestTools = import ./infra/nix/vm-guest-tools.nix {
        inherit pkgs;
        src = self;
      };
      vmApp = mode: {
        type = "app";
        program = pkgs.lib.getExe (
          import ./infra/nix/test-vm.nix {
            inherit pkgs mode;
            python = qaPython;
            src = self;
            package = deb;
            guestBundle = vmGuestTools;
          }
        );
      };
      shellFiles = [
        "*.sh"
        "omv-protondrive-auth"
        "*.postinst"
        "*.prerm"
        "*.postrm"
      ];
      cli = import ./infra/nix/proton-cli.nix { inherit pkgs; };
      apiRuntime = import ./infra/nix/api-runtime.nix {
        inherit pkgs;
        src = self;
      };
      webApp = import ./infra/nix/web-app.nix {
        inherit pkgs;
        src = self;
      };
      apiGeneration = import ./infra/nix/api-generation.nix {
        inherit
          pkgs
          webApp
          qaPython
          uv2nix
          pyproject-nix
          pyproject-build-systems
          ;
        src = self;
      };
      deb = import ./infra/nix/package.nix {
        inherit
          pkgs
          cli
          apiRuntime
          webApp
          ;
        python = qaPython;
        src = self;
      };
      testDebian = pkgs.writeShellApplication {
        name = "protondrive-test-debian";
        runtimeInputs = [
          qaPython
          pkgs.docker-client
        ];
        text = ''
          export PYTHONPATH="${self}/tools"
          exec python ${self}/tests/integration/debian_smoke.py \
            --guest-bundle ${vmGuestTools}/omv-protondrive-vm-tools.tar \
            --package ${deb}/openmediavault-protondrive_7.0.0_amd64.deb "$@"
        '';
      };
      webProbe = import ./infra/nix/web-probe.nix {
        inherit pkgs;
        src = self;
        vmCommand = (vmApp "command").program;
      };
      project = nix-tools.lib.configure {
        inherit system;
        toolPkgs = pkgs;
        nodejs = pkgs.nodejs_latest;
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
        inherit (maintenance) outdated;
        format = {
          css = true;
          scss = true;
          protobuf = true;
          nunjucks.includes = [ "*.html.njk" ];
          typescript = {
            includes = [
              "*.ts"
              "*.tsx"
            ];
            organizeImports = true;
          };
          python = {
            configFile = ./pyproject.toml;
            includes = [
              "*.py"
              "src/bin/omv-protondrive"
            ];
          };
          shell.includes = [
            "*.sh"
            "src/bin/omv-protondrive-auth"
            "debian/*.postinst"
            "debian/*.prerm"
            "debian/*.postrm"
          ];
          shell.extraOptions = [ "--case-indent" ];
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
              "**/.gitignore"
              "flake.lock"
              "uv.lock"
              "**/uv.lock"
              "buf.lock"
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
          exclude = [
            "LICENSE"
            "src/web/app/pnpm-lock.yaml"
            "src/api/protondrive_api/v1/**"
            "src/api/buf/**"
            "src/web/app/src/generated/**"
            "src/web/app/src/*.scss.d.ts"
          ];
        };
        lint = {
          nunjucks.includes = [ "*.html.njk" ];
          typescript.includes = [
            "*.ts"
            "*.tsx"
          ];
          protobuf.package = apiGeneration.buf;
          exclude = [
            "src/api/protondrive_api/v1/**"
            "src/api/buf/**"
            "src/web/app/src/generated/**"
            "src/web/app/src/*.scss.d.ts"
          ];
          nix = true;
          # actionlint 1.7.12 predates this GA runner label; keep other label errors fatal.
          workflows.extraOptions = [
            "-ignore"
            ''^label "ubuntu-26\.04" is unknown\.''
          ];
          python = {
            configFile = ./pyproject.toml;
            includes = [
              "*.py"
              "omv-protondrive"
            ];
          };
          deptry.projects = {
            tooling.sourceRoots = [
              "src"
              "tools"
              "tools/tests"
              "tests/unit"
              "tests/integration"
            ];
            api.root = "src/api";
          };
          basedpyright.projects.python = {
            configFile = "pyproject.toml";
            python = qaPython;
            reporter = "rich";
            outdated = {
              package = pkgs.basedpyright;
              repo = "detachhead/basedpyright";
            };
          };
          shell.includes = shellFiles;
          php.extraOptions = [ "--semantics" ];
          debian = true;
          extraCheckers.php-syntax = {
            command = "${pkgs.php}/bin/php";
            options = [ "-l" ];
            outdated = {
              package = pkgs.php;
              repo = "php/php-src";
              tags = true;
              tagPattern = ''php-(?P<version>[0-9]+(?:\.[0-9]+)+)'';
            };
            includes = [
              "*.inc"
              "*.php"
            ];
          };
          extraCheckers.css = {
            command = "${pkgs.biome}/bin/biome";
            outdated = {
              package = pkgs.biome;
              provider = "npm";
              project = "@biomejs/biome";
            };
            options = [
              "lint"
              "--error-on-warnings"
            ];
            includes = [ "*.css" ];
          };
          salt.exclude = [ "tools/tests/fixtures/templates/invalid/**" ];
          links = true;
          whitespace = {
            configFile = ./.editorconfig;
            includes = [ "*" ];
            exclude = [ "LICENSE" ];
          };
          extraProjectCheckers = {
            vm-branding = {
              command = pkgs.lib.getExe qa.checkBranding;
              outdated.skip = "Versioned with this repository";
            };
            templates = {
              command = "${qaPython}/bin/python";
              options = [ "tools/check_templates.py" ];
              outdated.skip = "Versioned with this repository";
            };
            just = {
              command = "${pkgs.just}/bin/just";
              options = [ "--summary" ];
              outdated = {
                package = pkgs.just;
                repo = "casey/just";
              };
            };
            guest-recipes = {
              command = "${pkgs.just}/bin/just";
              outdated = {
                package = pkgs.just;
                repo = "casey/just";
              };
              options = [
                "--justfile"
                "tests/integration/guest.just"
                "--summary"
              ];
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
            name = "Web probe TypeScript";
            run = "test -f ${webProbe.checked}/lib/web-probe/src/main.ts";
          }
          {
            name = "Owned web application";
            run = "test -f ${webApp}/index.html";
          }
          {
            name = "Generated API bindings";
            run = "test -e ${apiGeneration.check}";
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
        api-runtime = apiRuntime;
        web-app = webApp;
        api-generation = apiGeneration.check;
        api-schema-dependencies = apiGeneration.dependencies;
      };
      checks.${system} = project.checks // {
        tests = qa.hermeticTests;
        web-probe-types = webProbe.checked;
        web-app = webApp;
        api-bindings = apiGeneration.check;
        vm-guest-tools = vmGuestTools;
        debian-test-launcher = pkgs.runCommand "check-debian-test-launcher" { } ''
          ${pkgs.lib.getExe testDebian} --help > "$out"
        '';
      };
      apps.${system} = project.apps // {
        generate-api = {
          type = "app";
          program = pkgs.lib.getExe apiGeneration.generate;
        };
        audit = {
          type = "app";
          program = pkgs.lib.getExe maintenance.audit;
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
          program = pkgs.lib.getExe testDebian;
        };
        test-vm = vmApp "test";
        test-regression = {
          type = "app";
          program = pkgs.lib.getExe (
            import ./infra/nix/test-regression.nix {
              inherit pkgs;
              python = qaPython;
              src = self;
              vmUp = (vmApp "up").program;
              vmControl = (vmApp "control").program;
              vmCommand = (vmApp "command").program;
              webProbe = pkgs.lib.getExe webProbe.probe;
              testVm = (vmApp "test").program;
            }
          );
        };
        vm = vmApp "control";
        vm-command = vmApp "command";
        vm-up = vmApp "up";
        vm-install = vmApp "install";
        web-probe = {
          type = "app";
          program = pkgs.lib.getExe webProbe.probe;
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
            pkgs.pnpm
          ];
      };
    };
}
