# Dependency reporting and audits

Use the published nix-tools v0.7.2 pin from `flake.lock`:

```sh
just dev::outdated
just dev::outdated --json
just dev::audit
just dev::audit --in-clanker
```

Just forwards arguments to Nix apps. Neither report updates pins, lockfiles, dependencies or installed environments.
Review findings before choosing upgrades. Network and registry failures are errors, never an "up-to-date" result.

## Version report

The `outdated` app comes entirely from `nix-tools.lib.configure`. Its built-in providers read Nix inputs, `uv.lock`,
`tools/web-probe/pnpm-lock.yaml` and GitHub Actions. `infra/nix/maintenance.nix` declares explicit release sources for
standalone tools and discovers the host Nix version at report time. Custom checkers declare their own release metadata
or a reason to skip. nix-tools does not infer upstream projects from package names or include a formatter/linter package
catalogue. This repository has no Cargo, npm, Yarn or Composer inventories, so those providers remain disabled. Like the
previous checker, UV reads locked Python packages, not a developer's `.venv`.

The debputy release entry gets its current version from `(nix-tools.lib.packagesFor pkgs).debputy`, the same locally
packaged derivation used by the configured Debian formatter and checker. Its upstream Git source remains explicit.

`tools/outdated.py` is only a JSON adapter for consumer policy: Proton's platform-specific downloads, the selected
Debian cloud/container images, stable Ubuntu x64 runner labels, and the Debian 12 / OMV 7 / Python 3.11 compatibility
constraint. There is no consumer aggregate outdated command, renderer or exit-status policy. The adapter emits a valid
document with exit 0; nix-tools interprets its findings, including error rows, alongside built-in results.

GitHub providers read `GH_TOKEN` or `GITHUB_TOKEN`. To reuse GitHub CLI authentication, export it explicitly before
running:

```sh
export GH_TOKEN="$(gh auth token)"
```

Keep credentials in runtime environment or native registry configuration, never in Nix options. UV uses its declared
registries and native credential handling. The pinned UV omits `latest_version` both for current packages and for some
failed registry requests. nix-tools reports those rows as `unknown` with the locked current version and exits 2; it
cannot safely claim that either condition is current. Native providers run against disposable copies; source, staging,
manifests, locks and installed environments remain untouched. Client metadata caches may change.

Nix input results describe `flake.lock`; explicit release rows describe the configured standalone packages. Each
invocation performs live lookups. Nix may cache the executable build, but never the online report; outdated is
intentionally outside validate and flake checks.

JSON uses nix-tools schema version 1: `schemaVersion`, `state`, `counts` and `results`. Rows contain `provider`, `name`,
`source`, `state`, `current`, `compatible`, `latest` and `detail`. This replaces the old `components`/`domain` schema.
States are `up-to-date`, `outdated`, `ahead`, `pinned`, `skipped`, `unknown`, `blocked` and `error`. NAS compatibility
is an explained `pinned` row. Unsupported sources stay visible as `unknown`; unreadable release versions are explained
`skipped` rows. `latest` is upstream availability, not a tested upgrade; `compatible` is populated only when the
provider establishes a requirement-compatible candidate. Terminal text displays unknown rows in gray; JSON and
`--no-color` output remain plain.

Exit status is **0** when all inventories resolved without updates, **1** for outdated findings, and **2** for
incomplete or failed checks. Errors take precedence over updates and preserve successful rows from other providers. The
built-in CLI supports `--json`, `--root` and `--no-color`; the previous consumer-only `--in-clanker` option is gone.

## Security and dependency audit

`infra/nix/maintenance.nix` defines the tools, commands and finding exit codes:

- `uv lock --check --offline`: manifest/lock consistency.
- `uv audit --locked`: Python vulnerability and adverse-project advisories from OSV.
- `deptry`: unused and undeclared Python dependencies.
- `gitleaks`: all available Git history, the working tree, and staged changes, with secret redaction.

Every step runs even if an earlier step fails. The final state is `PASSED`, `FAILED` (exit **1**, findings), or `ERROR`
(exit **2**, a tool could not complete). An unborn repository explicitly skips history scanning; it still scans the
working tree and index. CI fetches full history before auditing. Rust-specific tools are not applicable to this project.
The Python advisory scan does not claim coverage of embedded vendor libraries or the NAS's Debian packages.

## Console policy

The audit and VM tools use `tools/console.py`; the outdated command uses nix-tools' own output. Human audit reports use
Rich tables with alternating rows, capped at 120 columns, and a final summary. Agent output is plain and puts the final
state last. Detection checks `CLAUDECODE`, `CURSOR_AGENT`, `GEMINI_CLI`, `CODEX_THREAD_ID`, `OPENCODE`, `IN_CLANKER`,
and `in-clanker`, including empty values.

`FORCE_COLOR=1` enables Rich with colour even through a pipe or automatic agent detection. `FORCE_COLOR=0` or explicit
`--in-clanker` selects plain output. `NO_COLOR` (including empty), `TERM=dumb`, and `--no-color` always disable ANSI.
Audit `--json` includes component domains and complete diagnostics. Agent text shows only the final 20 diagnostic lines
for a noisy failure; JSON preserves the complete captured diagnostics. Arbitrary messages are rendered literally, not
interpreted as Rich markup. Captured tools have colour disabled through their environment and tool-specific flags.

Offline regression tests run through `just test::unit` and Nix validation. They cover audit colour precedence, agent
detection, literal markup, application-source lookup failures, adapter results, and an actual Gitleaks scan that rejects
and redacts a generated test token.
