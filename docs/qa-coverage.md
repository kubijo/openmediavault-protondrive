# QA coverage

`just dev::validate` checks formatting, lint, tracked source, host and hermetic tests, and package assembly.
`nix run .#coverage -- --check` audits tracked-file selection and declared scope; it does not execute checks.
[CI](../.github/workflows/ci.yml) also runs the [audit](maintenance.md) and Debian integration on pushes and PRs; OMV VM
tests run weekly or manually. Local commands and CI use the same published nix-tools pin.

| Source                    | Formatter       | Checks                                                                                        |
| ------------------------- | --------------- | --------------------------------------------------------------------------------------------- |
| Python                    | Ruff            | Ruff; strict basedpyright for all Python files; runtime and tooling tests; Debian Python 3.11 |
| Nix                       | nixfmt          | statix, deadnix; flake checks and package build                                               |
| PHP `.inc`                | Mago            | Mago semantics, PHP syntax; OMV RPC in VM                                                     |
| Shell, maintainer scripts | shfmt           | shellcheck; guest installation and session lifecycle                                          |
| Workbench YAML            | yamlfmt         | yamllint; form loading routes; references; compilation                                        |
| Datamodel, editor JSON    | Biome           | Parsing; config tests; OMV database and RPC                                                   |
| Salt, Jinja               | Whitespace only | salt-lint; strict rendering; deployment and units in VM                                       |
| Systemd test override     | Whitespace only | VM recovery tests                                                                             |
| Debian control, copyright | debputy         | debputy lint; package checks                                                                  |
| Other Debian metadata     | Whitespace only | dpkg, Lintian; guest installation                                                             |
| TOML, uv lock             | Taplo           | uv2nix environment build; lock consistency audit                                              |
| TypeScript web probe      | Biome           | Strict type check; frozen pnpm install; VM browser assertions and screenshots                 |
| Markdown                  | mdformat        | Offline link checks                                                                           |
| `.editorconfig`           | Whitespace only | editorconfig-checker                                                                          |
| CI workflow               | yamlfmt         | yamllint, actionlint, shellcheck                                                              |

Just modules, recipes, and the shared prelude are formatted by just and checked by its parser. The basedpyright gate
checks the complete Python source and test tree, including executable fixtures, with Any and Unknown rejected. Both the
checker and Ruff target Python 3.11 for Debian compatibility; Zed reads the same project configuration. Native deptry
checks validate tooling and API dependency declarations separately, including generated API imports.

Whitespace-only formatting uses an explicit EditorConfig policy. Salt/Jinja have no semantic formatter; salt-lint rule
205 is exempted only for Jinja extensions. Rendered JSON, YAML and unit INI syntax are checked locally; systemd
validates the installed units in the VM. See the [coverage policy](../infra/nix/coverage.nix) for explicit exceptions.

[Guest tests](../tests/integration/README.md) cover metadata, keyring, deployment, backup/restore and container
recovery, including tests skipped in the Nix sandbox. Disposable tests fake Proton; authenticated flows and remaining
release gates are listed there. Images and CLI are pinned; apt dependencies resolve during provisioning.

Upstream `LICENSE` text is preserved; Nix manages `flake.lock`. Generated outputs and caches are excluded. Lintian
overrides cover OMV's required `/srv/salt` path and the unchanged vendor executable.
