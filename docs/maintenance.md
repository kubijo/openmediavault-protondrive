# Dependency reporting and audits

Run `just dev::outdated` or `just dev::audit`; each accepts `--help` and `--json`. Both use the committed nix-tools pin
and leave dependencies, locks and installed environments unchanged. Reports perform live lookups outside validation.

## Version report

nix-tools reads Nix inputs, GitHub Actions and four dependency projects: UV `tooling` (`.`) and `api` (`src/api`), pnpm
`probe` (`tools/web-probe`) and `app` (`src/web/app`). `infra/nix/maintenance.nix` declares release sources;
`tools/outdated.py` supplies Proton downloads, Debian images, runner labels and the Debian 12 / OMV 7 / Python 3.11
compatibility constraint.

GitHub providers use `GH_TOKEN` or `GITHUB_TOKEN`; reuse CLI authentication with `export GH_TOKEN="$(gh auth token)"`.
Keep credentials in the environment or native registry configuration, never Nix options.

The pinned UV omits `latest_version` for both current packages and some failed lookups. These rows remain `unknown`;
they cannot safely be reported as current. `latest` means available upstream, not tested; `compatible` requires a
provider-verified candidate. JSON uses schema 2 with a `project` field; custom adapters still emit schema 1.

Exit codes: **0** current, **1** updates, **2** incomplete/error. Errors take precedence while preserving successful
rows.

## Audit

`infra/nix/maintenance.nix` runs every check, even after failures:

- `uv lock --check --offline`: tooling and API manifest/lock consistency.
- `uv audit --locked`: OSV Python advisories; excludes embedded vendor libraries and Debian packages.
- `gitleaks`: available Git history, working tree and index, with redaction. CI fetches full history.

Exit codes: **0** passed, **1** findings, **2** tool failure.

Native nix-tools deptry checks run in lint/validation for tooling and API projects, including generated bindings.
Uvicorn's unused-dependency exemption covers its systemd module entry point.

Audit and VM output use `tools/console.py`; outdated uses nix-tools. `--in-clanker` selects plain audit/VM output.
`NO_COLOR`, `TERM=dumb` and `--no-color` disable ANSI; otherwise `FORCE_COLOR=1` can override automatic agent detection.
Audit JSON retains complete diagnostics; agent text limits noisy failures to the final 20 lines.
