# OpenMediaVault Proton Drive backups

An OMV 7 plugin that archives NAS paths with filesystem metadata and uploads them to Proton Drive. Targets Debian 12
amd64 with OMV 7.7.9 or newer in the 7.x series. Bundles Proton Drive CLI 0.8.0; the NAS does not need Nix.

## Build and validate

The lockfile pins the published nix-tools release. Clear any `NIX_ARGS` override left from local tooling trials.

```sh
unset NIX_ARGS
nix develop
just validate
just build
```

The package is `result/openmediavault-protondrive_7.0.0_amd64.deb`. `just validate` runs the Nix-defined formatting,
lint, tests and package build, and rejects untracked source files. Use `just format` to apply formatting.

Tooling uses Python 3.14 via uv/uv2nix; the installed runtime supports Debian's Python 3.11. Run `uv sync` for editor
dependencies in `.venv`. Nix assembles the package using the hash-pinned CLI source in `infra/nix/proton-cli.nix`.

## Configure

1. Complete the [isolated tests and release checks](tests/integration/README.md) before installing on the NAS.
2. Install with `apt install ./openmediavault-protondrive_7.0.0_amd64.deb`.
3. Open **Services → Proton Drive**, review Settings and Backup sets, save, and Apply.
4. Select **Sign in** and complete authentication through the Proton browser link.
5. Run a manual backup and [test restoring it](docs/restore.md), then enable scheduled backups.

Scheduling starts disabled; the default time is 03:00 in the NAS timezone, with no catch-up at boot.

| Set     | Paths                                   | Excluded relative paths                         | Containers                   |
| ------- | --------------------------------------- | ----------------------------------------------- | ---------------------------- |
| system  | `/etc`, `/usr/local`, `/var/spool/cron` | None                                            | Keep running                 |
| appData | `/data/appData`                         | `cache`, `logs`, `immich/cache`, `immich/redis` | Stop those currently running |

Staging defaults to `/data/.omv-protondrive`; each set retains two confirmed local backups and seven remote backups.
Unuploaded archives are preserved. Containers restart before upload; cancellation and abnormal termination trigger
recovery. **Cancel backup** stops the job; closing the browser leaves it running.

Remote retention permanently deletes expired backups after identity checks. Avoid concurrent edits to managed remote
folders. See [backup behavior and recovery](docs/operations.md) for storage layout, retention safeguards, service
lifecycle and troubleshooting commands.

## Testing and reference

Source lives in [`src/`](src/README.md), repository commands in [`tools/`](tools/README.md), and Nix builds/QA in
[`infra/`](infra/README.md). See [`config/`](config/README.md) for pins and [`tests/`](tests/README.md) for test suites.

GitHub Actions runs QA and Debian integration on pushes and pull requests; OMV VM tests run weekly or manually.

`just integration-debian` checks the CLI, runtime and keyring in a disposable container. `just integration-vm` exercises
OMV, container recovery and backup/restore in an isolated QEMU guest. Live Proton authentication and round-trip tests
remain separate [release checks](tests/integration/README.md).

- [QA coverage](docs/qa-coverage.md)
- [Version reporting and security audits](docs/maintenance.md): `just outdated` and `just audit`.
- Upstream references:
  [OMV OneDrive plugin](https://github.com/openmediavault/openmediavault/tree/fa3125f5f18bc740d11caad3ffe8776c55cdf210/deb/openmediavault-onedrive)
  and
  [HA Proton Drive add-on](https://github.com/ashishdevasia/ha-proton-drive-backup/tree/af03cc002f150e7030d45dfb4e895b1b0ab43320).

See [Debian copyright metadata](debian/copyright) for attribution. Public distribution requires confirming the bundled
Proton binary's redistribution terms; the plugin's GPL license does not relicense it.
