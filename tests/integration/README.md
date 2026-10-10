# Isolated tests and release checks

Use `just test::regression` for unattended VM setup, assertions and teardown. It uses real Debian/OMV services and a
filesystem-backed Proton fixture; no account is required. See each command's `--help` for options.

| Command                 | Scope                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------- |
| `just dev::validate`    | Formatting, lint, types, unit tests and builds                                     |
| `just test::debian`     | Debian 12 container: CLI, Python 3.11, metadata, keyring and Lintian               |
| `just test::vm`         | Fresh OMV overlay: installation, RPC, Salt, backup/restore and container recovery  |
| `just test::regression` | VM service tests plus browser, restore faults, snapshot/restore/reset and teardown |

VMs mount no host directories, Docker socket, credentials or NAS disks; the Debian container mounts source read-only.
Disposable disks/containers are removed on failure too, unless retention is requested. Python 3.11 guests cover setgid
and ACL/xattr tests skipped in the Nix sandbox. QEMU uses KVM when available, software emulation otherwise. Cold
provisioning needs network access, disk space and roughly 30 minutes; apt dependencies are not snapshot-pinned. These
checks are separate from hermetic validation.

## Local regression lifecycle

The suite asserts UI backup and selected-file restore, metadata, destination-collision refusal, cache release,
cancellation, controller crash, separate-process recovery, foreign browsing and corrupt-lock repair. It also checks
1440/420/320px layouts, RPC authentication, settings/set edits and Apply, snapshot/restore/reset identities, and service
installation, notifications and container recovery in a separate clean overlay. Failure checks isolate a Proton outage,
signed-out CLI, concurrent browse and corrupt archive, then match an internal error reference to the controller journal.

| Layer            | Invalidated by                             | Isolation                       |
| ---------------- | ------------------------------------------ | ------------------------------- |
| Nix artifacts    | Build inputs                               | Immutable store                 |
| OMV base         | Image, setup, harness, deps, QEMU, refresh | No plugin                       |
| Installed plugin | Base, package, guest tools, builder        | No sessions or jobs             |
| Run fixtures     | Every run                                  | Private overlay and fake remote |

Builders serialize publication and check disks after clean shutdown. Clones receive fresh machine, SSH and backup
identities. `--refresh-base` picks up apt updates and invalidates the installed descendant; old generations remain valid
for existing overlays. `test::vm --no-cache` provisions from Debian without updating the cache. Remove `.tmp/vm-cache/`
only when no VM uses it. Test results are never cached.

Regression artifacts live in `.tmp/regression/run-*/`: `report.json` records commands, exits and logs; `browser/` holds
screenshots and videos. Standalone VM logs live in `.tmp/vm-tests/`; use separate report directories for concurrent
runs. Teardown tries graceful then forced shutdown. `--keep-failed` retains stopped disks. Cleanup failure fails the run
and preserves both errors. SIGKILL/host failure requires manual `vm::down` / `vm::reset` using the report's state
directory.

These assertions cover the installed stack, not Proton's live protocol. See [acceptance status](../../docs/owned-ui.md)
and the release gates below.

## Interactive VM

`just vm::up` starts or resumes a persistent OMV VM and opens SSH. `just vm::ssh` reconnects; `just vm::down` stops it.
Exiting SSH or cancelling the launcher leaves the VM running. Failed initialization retains the disk; `vm::up` retries.
Use `vm::install` to install source changes while preserving configuration and credentials.

Default access: SSH on localhost:2222, OMV on <http://127.0.0.1:8080/>, web login `admin` / `admin`. Fresh VMs disable
scheduling, seed fixtures and use `/my-files/open-media-vault-proton-backup-development/<instance UUID>` remotely.
Existing passwords and OMV preferences are preserved. The TEST VM banner shows the default login, not a changed
password.

`.tmp/interactive-vm/` contains the private disk, SSH key, web login and logs; deleting it loses the session. Existing
VMs retain their base across cache refreshes. Stop the VM before changing ports. Commands accept `--state-dir` for
another VM; see `just vm --list` and command `--help` for SSH, RPC, ports, snapshots and recovery options.

- `vm::down --force` cuts power and may lose unsaved writes.
- `vm::reset --yes` deletes a stopped VM's disk/session, never remote Proton files.
- Snapshot/restore requires a stopped, initialized VM. It restores guest state only; keep forwarded ports unchanged.
- Unrelated pending OMV changes block installation: apply or revert them first. Failed deployment retains rollback data.
- Lost provisioning connections leave a pending-job marker; retries wait for confirmed cleanup.

Run `just` in the guest for its commands. Helpers ship as one versioned bundle and survive service restarts; host
`vm::up`, `vm::ssh` and `vm::install` activate updates. The guest needs no Nix.

`just vm::probe` reads the saved login and records browser assertions, screenshots, video and `report.json`, including
failures. See `just vm::probe --help`. `vm::flow` aliases `test::regression`.

## Authenticated flows

Use the dedicated development account and VM, with scheduling disabled and fixture sources only.

- `just vm::live`: UI backup, download/restore, cancellation after containers stop, and recovery of only previously
  running containers. Asserts unchanged remote data/last success and removal of partial archives.
- `just vm::retry`: bounded upload firewall fault scoped to the Proton service user; verifies archive preservation,
  retry before container stops, and download/restore.
- `just vm::crash`: SIGKILL and abrupt reboot with blocked container restart; verifies pending archives and
  failed/successful recovery through the UI.

Flows own their fixtures and serialize through a VM-wide lock. Handled failure/cancellation restores configuration and
removes owned overrides, rules and containers. Forced termination leaves evidence; the next run offers recovery or
abort. Automation must use `--recover`; failed recovery retains evidence.

After a backup, guest helpers also check ownership safeguards:

```sh
PYTHONPATH=/usr/share/openmediavault-protondrive python3 /usr/local/lib/omv-protondrive-vm/live_proton_guest.py
python3 /usr/local/lib/omv-protondrive-vm/live_proton_collision.py
PYTHONPATH=/usr/share/openmediavault-protondrive python3 /usr/local/lib/omv-protondrive-vm/live_proton_unmarked.py
```

These respectively verify both archives, reject a substituted owner ID, and reject claiming a nonempty unmarked folder.
The latter trashes only its disposable folder inside the development root.

## Remaining authenticated/manual release gates

Use a disposable Debian 12 / OMV 7 amd64 VM, test containers and the dedicated Proton root.

01. Verify package integrity, navigation, seeded sets, disabled scheduling, repeated Apply, service ownership and
    keyring.
02. Test 2FA, sign-in cancellation/timeout, sign-out, offline startup and expired sessions. Verify credentials survive
    reboot/upgrade and auth links/keys never enter journals.
03. Add sanitized CLI 0.8.0 list/info/upload/trash fixtures covering `name`, `uid`, `type` and
    `activeRevision.value.claimedSize`; verify commands terminate with stdin closed.
04. Restore data through Proton with numeric ownership, setgid, ACLs, xattrs, symlinks and exclusions; verify checksums
    and database restoration.
05. Run live/crash flows; only previously running containers may resume. Failed recovery must retain its record.
06. Run retry flow; also inject tar failure, low/full staging and missing sources. Preserve pending complete archives
    and the last confirmed backup; retry uploads before stopping containers again.
07. Race scheduled/manual jobs; only one proceeds. Closing the browser must leave the job running; explicit Cancel must
    finish container recovery.
08. Exceed retention counts; trash only oldest complete pairs. Duplicate names, folders, incomplete pairs and unrelated
    files must prevent unsafe cleanup. Never empty account-wide trash.
09. Change schedule/destination, remove sets, disable, upgrade, remove and purge; retain old archives. Check systemd
    units and Lintian.
10. Confirm no boot catch-up after missing 03:00; recover interrupted container stops before any later backup.
