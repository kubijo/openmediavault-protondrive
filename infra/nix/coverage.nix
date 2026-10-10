let
  runtimeTests = {
    includes = [
      "src/protondrive/*.py"
      "src/web/templates/*.njk"
      "tests/unit/*.py"
      "tests/fixtures/fake_proton_auth.py"
      "tests/integration/vm.py"
      "tests/integration/provision_guest.py"
      "tests/integration/interactive_guest.py"
      "tests/integration/interactive-vm.css"
      "tools/*.py"
      "tools/tests/*.py"
      "tools/tests/fixtures/**"
    ];
    # The tracked-source gate runs separately and has no unit tests.
    exclude = [ "tools/check_tracked.py" ];
    kind = "test";
    description = "Runtime and tooling unit suites; guest-only tests are not claimed here";
  };
in
{
  required = [
    "format"
    "lint"
  ];
  checkerKinds.php-syntax = "syntax";
  checkerKinds.css = "semantic";
  projectChecks = {
    "lint:basedpyright-python" = {
      includes = [
        "*.py"
        "src/bin/omv-protondrive"
      ];
      kind = "semantic";
      description = "Strict basedpyright checks Python source and tests, including executable fixtures";
    };
    "lint:deptry-tooling" = {
      includes = [
        "src/**/*.py"
        "tools/**/*.py"
        "pyproject.toml"
      ];
      exclude = [
        "src/api/**"
        "**/tests/**"
      ];
      kind = "semantic";
      description = "Tooling dependency declarations and imports";
    };
    "lint:deptry-api" = {
      includes = [
        "src/api/**/*.py"
        "src/api/pyproject.toml"
      ];
      exclude = [ "src/api/tests/**" ];
      kind = "semantic";
      description = "API dependency declarations and imports, including generated bindings";
    };
    "lint:vm-branding" = {
      includes = [
        "tools/tests/fixtures/nginx.conf"
        "src/nginx/*.conf"
      ];
      kind = "syntax";
      description = "Nginx validates plugin stylesheet and VM branding includes with the test server configuration";
    };
    "lint:templates" = {
      includes = [
        "src/salt/**/*.sls"
        "src/salt/**/*.j2"
        "src/salt/**/*.jinja"
        "src/monit/*.sls"
        "src/omv/datamodels/*.json"
        "src/omv/workbench/**/*.yaml"
        "src/web/templates/*.njk"
      ];
      kind = "semantic";
      description = "Strict rendering, Salt/JSON and unit INI syntax, timer assertions, models, workbench assembly and template syntax";
    };
    "lint:just" = {
      includes = [
        "justfile"
        "tools/just/*.just"
      ];
      kind = "syntax";
      description = "Parse recipes, modules, and the shared prelude with just";
    };
    "lint:guest-recipes" = {
      includes = [ "tests/integration/guest.just" ];
      kind = "syntax";
      description = "Parse the standalone guest recipes with just";
    };
    "validate:Tracked source" = {
      includes = [ "*" ];
      kind = "unspecified";
      description = "Reject untracked files omitted by Git flakes; no semantic lint claim";
    };
    "validate:Runtime tests" = runtimeTests;
    "validate:Hermetic runtime tests" = runtimeTests;
    "validate:Hermetic formatting" = {
      includes = [
        "*.json"
        "*.toml"
      ];
      kind = "syntax";
      description = "Biome and Taplo parse JSON/TOML while checking formatting; no schema claim";
    };
    "validate:Hermetic linting" = {
      includes = [ ];
      kind = "unspecified";
      description = "Aggregate gate; file scope comes from the individual lint checkers";
    };
    "validate:Web probe TypeScript" = {
      includes = [ "tools/web-probe/**/*.ts" ];
      kind = "semantic";
      description = "Pinned TypeScript compiler checks the browser probe with strict types";
    };
    "validate:Owned web application" = {
      includes = [
        "src/web/app/**/*.ts"
        "src/web/app/**/*.tsx"
        "src/web/app/**/*.scss"
      ];
      exclude = [ "src/web/app/src/generated/**" ];
      kind = "semantic";
      description = "Strict TypeScript, Stylelint, Rstest and the React Compiler production build";
    };
    "validate:Generated API bindings" = {
      includes = [
        "proto/**"
        "buf*.yaml"
        "buf.lock"
        "src/api/protondrive_api/v1/**"
        "src/api/buf/**"
        "src/web/app/src/generated/**"
      ];
      kind = "semantic";
      description = "Buf schema lint and byte-for-byte regeneration with locked local generators";
    };
    "validate:Debian package" = {
      includes = [
        "debian/control"
        "debian/changelog"
        "config/sources.json"
      ];
      kind = "syntax";
      description = "dpkg parses control/changelog; Nix parses pinned download metadata during package assembly";
    };
  };
  exceptions = [
    {
      includes = [ "tools/tests/fixtures/templates/invalid/**" ];
      stages = [ "lint" ];
      reason = "Intentionally invalid templates exercised by rejection tests; whitespace remains checked";
    }
    {
      includes = [ "LICENSE" ];
      reason = "Verbatim upstream GPL license; excluded from rewriting and lint";
    }
    {
      includes = [
        "flake.lock"
        "uv.lock"
        "**/uv.lock"
        "buf.lock"
      ];
      stages = [ "lint" ];
      reason = "Generated locks consumed by Nix and uv2nix; whitespace remains checked, dependency audits run separately";
    }
    {
      includes = [
        "tools/web-probe/pnpm-lock.yaml"
        "src/web/app/pnpm-lock.yaml"
      ];
      stages = [
        "format"
        "lint"
      ];
      reason = "pnpm 12 generates a multi-document lockfile; a frozen install validates it during the web-probe type check";
    }
    {
      includes = [
        "src/api/protondrive_api/v1/**"
        "src/api/buf/**"
        "src/web/app/src/generated/**"
        "src/web/app/src/*.scss.d.ts"
      ];
      stages = [
        "format"
        "lint"
      ];
      reason = "Generated output; pinned generator drift checks and consumers' strict type checks validate it";
    }
    {
      includes = [
        ".editorconfig"
        ".gitignore"
      ];
      stages = [ "lint" ];
      reason = "Policies consumed by EditorConfig and Git; whitespace checks are not semantic lint";
    }
    {
      includes = [
        "debian/*.lintian-overrides"
        "debian/*.triggers"
        "debian/source/format"
      ];
      stages = [ "lint" ];
      reason = "Not supported by debputy lint; package policy is checked by Lintian in the disposable integration suites";
    }
    {
      includes = [
        "tests/fixtures/recovery.service.conf"
        "tests/integration/ui-cancel.conf"
        "tests/integration/owned-restore.conf"
      ];
      stages = [ "lint" ];
      reason = "Systemd loads this override in guest recovery tests; no standalone host unit validation";
    }
  ];
}
