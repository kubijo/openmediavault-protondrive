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
      ];
      stages = [ "lint" ];
      reason = "Generated locks consumed by Nix and uv2nix; whitespace remains checked, dependency audits run separately";
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
        "debian/openmediavault-protondrive.lintian-overrides"
        "debian/openmediavault-protondrive.triggers"
        "debian/source/format"
      ];
      stages = [ "lint" ];
      reason = "Not supported by debputy lint; package policy is checked by Lintian in the disposable integration suites";
    }
    {
      includes = [ "tests/fixtures/recovery.service.conf" ];
      stages = [ "lint" ];
      reason = "Systemd loads this override in guest recovery tests; no standalone host unit validation";
    }
  ];
}
