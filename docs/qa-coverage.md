# QA coverage

`just dev::validate` checks formatting, lint, tracked source, host and hermetic tests, and package assembly.
`nix run .#coverage -- --check` audits tracked-file selection and declared scope; it does not execute checks.
[CI](../.github/workflows/ci.yml) also runs the [audit](maintenance.md) and Debian integration on pushes and PRs; OMV VM
tests run weekly or manually. Local commands and CI use the same published nix-tools pin.

| Source                    | Formatter       | Checks                                                  |
| ------------------------- | --------------- | ------------------------------------------------------- |
| Python                    | Ruff            | Ruff; runtime and tooling tests; Debian Python 3.11     |
| Nix                       | nixfmt          | statix, deadnix; flake checks and package build         |
| PHP `.inc`                | Mago            | Mago semantics, PHP syntax; OMV RPC in VM               |
| Shell, maintainer scripts | shfmt           | shellcheck; guest installation and session lifecycle    |
| Workbench YAML            | yamlfmt         | yamllint; form loading routes; references; compilation  |
| Datamodel, editor JSON    | Biome           | Parsing; config tests; OMV database and RPC             |
| Salt, Jinja               | Whitespace only | salt-lint; strict rendering; deployment and units in VM |
| Systemd test override     | Whitespace only | VM recovery tests                                       |
| Debian control, copyright | debputy         | debputy lint; package checks                            |
| Other Debian metadata     | Whitespace only | dpkg, Lintian; guest installation                       |
| TOML, uv lock             | Taplo           | uv2nix environment build; lock consistency audit        |
| Markdown                  | mdformat        | Offline link checks                                     |
| `.editorconfig`           | Whitespace only | editorconfig-checker                                    |
| CI workflow               | yamlfmt         | yamllint, actionlint, shellcheck                        |

Just modules, recipes, and the shared prelude are formatted by just and checked by its parser.

Whitespace-only formatting uses an explicit EditorConfig policy. Salt/Jinja have no semantic formatter; salt-lint rule
205 is exempted only for Jinja extensions. Rendered JSON, YAML and unit INI syntax are checked locally; systemd
validates the installed units in the VM. See the [coverage policy](../infra/nix/coverage.nix) for explicit exceptions.

[Guest tests](../tests/integration/README.md) cover filesystem metadata, private keyring sessions, OMV deployment,
backup/restore and container recovery. They run the root/metadata tests skipped in the Nix sandbox. Proton is faked for
backup tests; live authentication, network behaviour and browser interaction remain manual release checks. Guest images
and the CLI are pinned; Debian/OMV apt dependencies are resolved at run time.

Upstream `LICENSE` text is preserved; Nix manages `flake.lock`. Generated outputs and caches are excluded. Lintian
overrides cover OMV's required `/srv/salt` path and the unchanged vendor executable.
