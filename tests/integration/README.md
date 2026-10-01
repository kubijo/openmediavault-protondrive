# Isolated tests and remaining release checks

Run the automated tests without installing the plugin on the NAS:

```sh
just validate
just integration-debian
just integration-vm
```

`integration-debian` runs a digest-pinned Debian 12 container. It checks the real bundled CLI, all Python 3.11 runtime
unit tests, archive metadata, private D-Bus/keyring startup and restart, and Lintian. The source mount is read-only; no
Docker socket is mounted into the guest. Its container is removed even on test failure.

`integration-vm` boots a hash-pinned Debian 12 cloud image in QEMU, installs OMV 7 from its signed apt repository, and
installs the built plugin. It checks actual RPC defaults, Salt deployment twice, disabled scheduling, generated units,
workbench compilation, Docker recovery after SIGTERM and SIGKILL, and a full backup/restore through the installed runner
and daemon with a filesystem-backed fake Proton CLI. The recovery tests substitute a fixture workload inside the shipped
systemd unit; they exercise the real Recovery implementation and ExecStopPost, without a Proton account. One container
starts running and another stopped; only the former may be restarted.

The VM uses an ephemeral disk overlay, NAT, an ephemeral SSH key and a localhost-only SSH port. No host directory,
Docker socket, credentials or NAS disk is mounted. The VM is removed on failure too; logs remain in `.tmp/vm-tests/`.
KVM with the host CPU is used when available, with QEMU’s `max` CPU under software emulation as fallback. The initial
run downloads the image/toolchain and installs OMV dependencies; allow disk space and about 30 minutes. Debian/OMV apt
packages are resolved at run time, so these integration checks need network access and are separate from hermetic
`nix flake check`.

Tooling runs on Python 3.14 through uv/uv2nix. Guest tests use Debian's Python 3.11. The two filesystem tests skipped by
the Nix sandbox (setgid and xattrs/ACLs) run in the Debian guests. Unit tests simulate Proton protocol and retention;
that is not evidence of a successful live Proton round trip.

## Remaining authenticated/manual release gates

Run these on a disposable Debian 12 / OMV 7 amd64 VM. Use test containers and a dedicated Proton folder. These are
manual release gates; local unit tests do not claim to have exercised them.

01. Install the built package. Verify `dpkg --verify`, plugin navigation, both seeded sets, and disabled scheduling.
    Apply twice and confirm idempotence, service ownership, and a writable private keyring.
02. Exercise sign-in with 2FA, cancellation, timeout, sign-out, offline startup, and invalid/expired session handling.
    Reboot and upgrade the package; check that credentials persist. Check that no auth link or key appears in journals.
03. Capture CLI 0.8.0 JSON for nested list/info, upload, trash and delete. Confirm `name`, `uid`, `type`, and
    `activeRevision.value.claimedSize` match the adapter. Commands must terminate with stdin closed. Add sanitized real
    output to fixtures before release; never include sign-in URLs or credentials.
04. Back up fixture data with a numeric owner different from the VM administrator, setgid, ACLs, xattrs, symlinks and
    excluded directories. Download through Proton and complete the checksum, extraction and database restore exercise.
05. During container archiving, send SIGTERM and SIGKILL to the runner in separate tests. Confirm all recorded
    containers resume, while previously stopped containers remain stopped. Repeat with a VM reboot and with a failed
    Docker start. The recovery record must remain until the final container is running.
06. Inject tar failure, low/full staging space, missing source, upload interruption and network failure. Confirm no
    incomplete archive is published, pending complete archives survive, and a failed backup never removes the last
    confirmed copy. Resume and verify pending uploads are retried before containers are stopped again.
07. Start a scheduled and a manual job simultaneously. Verify only one run proceeds. Close the execution dialog's
    browser tab and confirm the job continues; exercise explicit Cancel backup and verify restoration completes.
08. Produce more than the configured retention counts. Verify only the oldest completed pairs are deleted. Add duplicate
    names, a same-named folder, a same-named trash item, incomplete pairs, and unrelated files. Ambiguity must prevent
    destructive cleanup. No test may call `empty-trash`.
09. Change the schedule and destination, remove a set, disable scheduling, upgrade, remove, and purge. Verify old
    archives remain available. Inspect systemd units with `systemd-analyze verify` and validate the package with
    Lintian.
10. Confirm the NAS being offline at 03:00 does not cause a catch-up backup at boot. Container recovery must still run
    before any later backup if an interrupted stop was recorded.
