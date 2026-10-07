# OpenMediaVault Proton Drive backups

An OMV 7 plugin that archives NAS paths with filesystem metadata and uploads them to Proton Drive. Targets Debian 12
amd64 with OMV 7.7.9 or newer in the 7.x series. Bundles Proton Drive CLI 0.8.0; the NAS does not need Nix.

## Build and validate

Use the toolchain pinned by this repository for local builds and validation:

```sh
nix develop
just dev::validate
just app::build
```

The package is `result/openmediavault-protondrive_7.0.0_amd64.deb`. `just dev::validate` runs the Nix-defined
formatting, lint, tests and package build, and rejects untracked source files. Use `just dev::format` to apply
formatting.

Tooling uses Python 3.14 via uv/uv2nix; the installed runtime supports Debian's Python 3.11. Run `uv sync` for editor
dependencies in `.venv`. Nix assembles the package using the hash-pinned CLI source in `infra/nix/proton-cli.nix`.

## Configure

1. Complete the [isolated tests and release checks](tests/integration/README.md) before installing on the NAS.
2. Install with `apt install ./openmediavault-protondrive_7.0.0_amd64.deb`.
3. Open **Services → Proton Drive**, review Settings and Backup sets, save, and Apply.
4. Select **Sign in** and complete authentication through the Proton browser link.
5. Run a manual backup and [test restoring it](docs/restore.md), then enable scheduled backups.

Scheduling starts disabled; the default time is 03:00 in the NAS timezone, with no catch-up at boot.

The **Open backup console** link opens the [owned web application](docs/owned-ui.md); its restore workflows are still
under development.

| Set     | Paths                                   | Excluded relative paths                         | Containers                   |
| ------- | --------------------------------------- | ----------------------------------------------- | ---------------------------- |
| system  | `/etc`, `/usr/local`, `/var/spool/cron` | None                                            | Keep running                 |
| appData | `/data/appData`                         | `cache`, `logs`, `immich/cache`, `immich/redis` | Stop those currently running |

Staging defaults to `/data/.omv-protondrive`; each set retains two confirmed local backups and seven remote backups.
Unuploaded archives are preserved. Containers restart before upload; cancellation and abnormal termination trigger
recovery. **Cancel backup** stops the job; closing the browser leaves it running.

Remote retention moves expired backups to Proton's trash after identity checks. Avoid concurrent edits to managed remote
folders. See [backup behavior and recovery](docs/operations.md) for storage layout, retention safeguards, service
lifecycle and troubleshooting commands.

## Testing and reference

Source lives in [`src/`](src/README.md), repository commands in [`tools/`](tools/README.md), and Nix builds/QA in
[`infra/`](infra/README.md). See [`config/`](config/README.md) for pins and [`tests/`](tests/README.md) for test suites.

GitHub Actions runs QA and Debian integration on pushes and pull requests; OMV VM tests run weekly or manually.

`just test::debian` checks the CLI, runtime and keyring in a disposable container. `just test::vm` exercises OMV,
container recovery and backup/restore in an isolated QEMU guest. Initial Proton sign-in and broader failure scenarios
remain separate [release checks](tests/integration/README.md).

For hands-on testing, `just vm::up` starts a persistent local OMV VM in the background and opens its SSH shell. Exiting
SSH leaves the VM running. Use `just vm::ssh` to reconnect and `just vm::down` to shut it down without losing the login
session. The guest login banner lists its own `just` commands. See the
[interactive VM instructions](tests/integration/README.md#interactive-vm). `just vm::probe` checks the running VM in
Chromium and saves desktop/mobile screenshots and videos. `just vm::live` exercises real UI backup, download/restore,
and controlled cancellation using the signed-in development account. `just vm::flow` runs a separate VM through web
setting changes, disk restore, reset, and the backup/restore suite without touching the hands-on VM.

- [QA coverage](docs/qa-coverage.md)
- [Version reporting and security audits](docs/maintenance.md): `just dev::outdated` and `just dev::audit`.
- Upstream references:
  [OMV OneDrive plugin](https://github.com/openmediavault/openmediavault/tree/fa3125f5f18bc740d11caad3ffe8776c55cdf210/deb/openmediavault-onedrive)
  and
  [HA Proton Drive add-on](https://github.com/ashishdevasia/ha-proton-drive-backup/tree/af03cc002f150e7030d45dfb4e895b1b0ab43320).

See [Debian copyright metadata](debian/copyright) for attribution. Public distribution requires confirming the bundled
Proton binary's redistribution terms; the plugin's GPL license does not relicense it.
