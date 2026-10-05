# Isolated tests and remaining release checks

Run the automated tests without installing the plugin on the NAS:

```sh
just dev::validate
just test::debian
just test::vm
```

`test::debian` runs a digest-pinned Debian 12 container. It checks the real bundled CLI, all Python 3.11 runtime unit
tests, archive metadata, private D-Bus/keyring startup and restart, and Lintian. The source mount is read-only; no
Docker socket is mounted into the guest. Its container is removed even on test failure.

`test::vm` caches a provisioned Debian 12 / OMV 7 base under `.tmp/vm-cache/`. Every run boots a fresh QEMU overlay and
installs the current plugin. It checks actual RPC defaults, Salt deployment twice, disabled scheduling, generated units,
workbench compilation, Docker recovery after SIGTERM and SIGKILL, and a full backup/restore through the installed runner
and daemon with a filesystem-backed fake Proton CLI. The recovery tests substitute a fixture workload inside the shipped
systemd unit; they exercise the real Recovery implementation and ExecStopPost, without a Proton account. One container
starts running and another stopped; only the former may be restarted.

The VM uses an ephemeral disk overlay, NAT, an ephemeral SSH key and a localhost-only SSH port. No host directory,
Docker socket, credentials or NAS disk is mounted. The test overlay is removed on failure too; the reusable base and
logs in `.tmp/vm-tests/` remain. Stage timings and guest output stream into terminal scrollback; human terminals also
show a transient spinner. Guest scripts use an output PTY in a human terminal: tool palettes are normalized,
carriage-return progress replaces its transient row, and stdin remains disconnected. Saved transcripts contain plain
text. Agent, no-colour and dumb-terminal modes use plain capture without a PTY or animations. The guest transcript is
retained as `tests.log` and boot output as `serial.log`. Use `just test::vm --in-clanker` for explicit plain output. KVM
with the host CPU is used when available, with QEMU’s `max` CPU under software emulation as fallback. The initial run
downloads the image/toolchain and installs OMV dependencies; allow disk space and about 30 minutes. Debian/OMV apt
packages are resolved when provisioning, so these integration checks need network access and are separate from hermetic
`nix flake check`.

The base cache key covers Debian image contents, provisioning and harness code, QEMU version, and the built package's
dependency metadata. Plugin code and test fixtures do not invalidate the base. A base contains no plugin or test data;
it is published only after sealing, clean shutdown, and a disk check. SSH keys and cloud-init identity are regenerated
per guest. Concurrent builders share a lock; refresh publishes a new immutable generation without replacing backing
files used by running overlays.

```sh
just test::vm                # Build the base once, then reuse it with a fresh test overlay.
just test::vm --refresh-base # Refresh apt packages and replace the selected base generation.
just test::vm --no-cache     # Exercise provisioning from the original Debian image; leave the cache untouched.
```

Upstream apt updates require `--refresh-base`; they cannot automatically invalidate a local cache. CI uses `--no-cache`
to retain clean-install coverage. Old generations are retained; remove `.tmp/vm-cache/` only when no test VM is running.
Use separate `--reports` directories for simultaneous runs. Cache messages explicitly report HIT, MISS, REFRESH or
BYPASS.

Tooling runs on Python 3.14 through uv/uv2nix. Guest tests use Debian's Python 3.11. The two filesystem tests skipped by
the Nix sandbox (setgid and xattrs/ACLs) run in the Debian guests. Unit tests simulate Proton protocol and retention;
that is not evidence of a successful live Proton round trip.

## Interactive VM

```sh
just vm::up      # Start the daemon (or reuse it) and open a root SSH shell.
just vm::ssh     # Reconnect to the running VM.
just vm::exec -- systemctl is-active omv-protondrive.service
just vm::rpc status
just vm::rpc prepare --set-uuid <backup-set-uuid>
just vm::status  # Show state and connection details.
just vm::install # Install the current plugin while retaining settings and login state.
just vm::down    # Shut down explicitly; exiting SSH leaves the VM running.
just vm::probe   # Assert overview/settings/backup sets in Chromium and save screenshots.
```

The initial setup reuses the plugin-free base, installs the real CLI/plugin, and prepares small fixture directories with
scheduling disabled and the fixed development root, with a unique instance directory underneath it. Open
`http://127.0.0.1:8080/` and log in with `admin` / `admin`. Existing VMs retain their password; the initial value is
recorded in `.tmp/interactive-vm/instance/admin-password`. Sign into your dedicated Proton account through the plugin.
Proton credentials and authentication links are not printed by the harness.

`vm::exec` passes arguments to SSH safely and disconnects stdin; use `vm::ssh` for an interactive shell. `vm::rpc`
serializes the request to JSON, keeps the socket connected until the complete response arrives, and exits nonzero for
transport or service errors. Its `prepare` operation checks a named backup set and may create its remote folder.

QEMU runs as a daemon, independently of SSH and the launching terminal. Exiting the shell, disconnecting, or sending
Ctrl-C/SIGHUP/SIGTERM to the host command does not shut it down. A failed initial install also leaves the VM available
for diagnosis; `vm::up` retries initialization if it has not completed. Re-running `vm::up` on a ready VM opens another
shell without reinstalling the plugin. Stop it before changing forwarded ports.

`just vm::up --no-shell` waits for the daemon, and `just vm::probe` checks the browser UI using the VM's saved login and
saves screenshots; run `just vm::probe --help` for options.

The guest has a standalone `/root/justfile` and a pinned static `just` executable; it does not need Nix. Its login
banner lists `status`, `services`, `logs`, `restart`, `pending`, `apply`, and `workbench`. Run `just` to see the list
again. `apply` uses the same pending-change guard as installation. Guest commands diagnose/control the installed plugin;
build and install source changes from the host with `just vm::install`. The VM-only helpers are built into one archive
and activated through `/usr/local/lib/omv-protondrive-vm` on `vm::up`, `vm::ssh`, and `vm::install`; each update
switches to a fresh versioned directory, so removed helpers do not remain in the active bundle. Service restarts do not
remove them.

The guest justfile contains command wrappers only. Pending-change and apply logic lives in `guest_commands.py`, which
reuses the installer's validation and apply functions.

After signing the dedicated VM into Proton and running a backup, run these built helpers inside the VM:

```sh
PYTHONPATH=/usr/share/openmediavault-protondrive python3 /usr/local/lib/omv-protondrive-vm/live_proton_guest.py
python3 /usr/local/lib/omv-protondrive-vm/live_proton_collision.py
PYTHONPATH=/usr/share/openmediavault-protondrive python3 /usr/local/lib/omv-protondrive-vm/live_proton_unmarked.py
```

The first downloads completed archive/manifest pairs through the private Proton session and scratch-restores both
fixture sets. The second briefly substitutes a foreign local owner ID, asserts refusal before archiving, restores the
original ID and service, then requires a successful backup. The third creates a disposable nonempty instance folder
inside the development root, asserts that the missing owner marker prevents claiming it without altering its contents,
then moves that disposable folder to Proton trash. Use only with the dedicated test account and VM.

Root logins suppress Debian's stock MOTD and last-login text with `.hushlogin`. VM details and the guest recipe list
appear in boxes, with coloured paths. Host and guest recipes keep the native `just --list` colours and single-spaced
rows, with a blank line after each box. Run `just` in the guest to show its recipe list again.

Bash logins enable `just` completion automatically: use Tab to complete guest recipes and command options. Existing VMs
receive the shell setup on the next `just vm::ssh` or `just vm::up`; no reinstall is needed.

An amber **TEST VM** banner shows the default login on every web page; a dark test theme replaces the disk photograph.
Fresh VMs also default the admin's desktop and mobile workbench preferences to dark mode. Change this in OMV's user
menu; plugin reinstalls preserve the selection. The harness installs this through an Nginx snippet and separate CSS,
without changing OMV's packaged HTML/JavaScript. `just vm::install` also refreshes this branding on an existing VM. The
banner describes the default credentials; changing the web password does not change the banner or reset your password.

Installation prepares Monit, then applies Monit, nginx, PHP-FPM and Proton Drive together through OMV's configuration
API. Unrelated pending changes block installation: apply or revert them first, because OMV clears all rollback revisions
after an apply. Failed deployment retains the rollback revisions. The installer waits for Monit's nginx/PHP-FPM
registrations and retries an apply at most twice if its sole failure is a missing Monit service during reload.

Provisioning runs in a supervised guest service. Cancellation stops its process group and confirms completion; a lost
connection leaves a pending-job marker that blocks retries until cleanup succeeds. This cancels the provisioning job,
not the persistent VM. Disposable `test::vm` runs still clean up their own QEMU process.

The private state directory `.tmp/interactive-vm/` retains the disk, SSH key, web password and guest logs. Its local
base reference survives refresh or deletion of `.tmp/vm-cache/`. Existing interactive VMs do not automatically adopt a
new base or plugin build; use `vm::install` for the plugin or reset to recreate the whole guest. Deleting the
interactive state directory loses its login session. No interactive image is added to the shared base cache.

Only localhost ports are exposed: SSH on 2222 and OMV HTTP on 8080. Override them with
`vm::up --ssh-port 2223 --http-port 8081`. All VM commands accept `--state-dir` for a separate instance. The control
commands can run without rebuilding the plugin. If graceful shutdown fails, `just vm::down --force` cuts power and may
lose unsaved writes.

Graceful shutdown asks systemd over SSH; OMV ignores ACPI power-button events. Initial web deployment waits for Monit's
delayed control interface. After a failed setup, use `just vm::up` to retry on the retained disk, or `just vm::down` to
shut it down.

`just vm::reset --yes` deletes the stopped VM's local disk and session; the next `vm::up` creates a new guest. It
refuses to reset a running VM and does not delete remote Proton files. This interactive workflow is separate from
`test::vm`, which runs automated checks and deletes its test overlay.

`just vm::snapshot --snapshot-name baseline` creates an internal qcow2 restore point after the VM is stopped;
`just vm::restore --snapshot-name baseline` reverts the stopped disk, including the guest's settings and login state.
Both refuse a running or not-yet-initialized VM. They do not restore remote Proton files or host-side metadata; avoid
changing forwarded ports between snapshot and restore.

`just vm::flow` runs the full unattended local exercise in a newly allocated `.tmp/autonomous-flow/` state and on fresh
localhost ports. It boots and checks the web UI, snapshots the stopped disk, changes the backup hour through Chromium,
verifies the change, restores the snapshot and original remote identity, resets and reinitializes the VM with the same
development root and a new instance UUID, then runs the disposable guest backup/restore suite. Each step records its
command, exit code, full log and screenshots under an ignored timestamped directory. Failure retains available guest
state and logs; a passing run deletes its own disk.

Every browser probe records viewport-sized WebM video, screenshots, and `report.json` in its output directory, including
on failure. Use `just vm::probe --help` for options.

With the dedicated account already signed in, `just vm::live` starts a real backup through the web UI, leaves its task
view, checks completion, and downloads and scratch-restores both new archives. It then uses two disposable containers
and a VM-only tar hold to exercise **Cancel backup** after the real runner has stopped containers. It verifies recovery
of only the previously running container, unchanged remote contents and last-success status, and removal of partial
archives. The fixture restores runtime configuration and removes its override and containers on completion or handled
failure. Existing containers, scheduled backups, and non-development destinations or sources are refused. A VM-wide lock
prevents concurrent live flows. Ctrl+C stops the active test operation, cleans up fixtures, saves the video, and exits
130\. After a forced termination, the next run asks whether to recover the abandoned fixture or abort; automation must
explicitly pass `--recover`. Recovery failures retain their evidence for inspection.

`just vm::retry` interrupts a real fixture upload with a bounded firewall rule restricted to the VM's Proton service
user, then checks preservation, UI-triggered retry ordering, and download/restore of the retained archive. The watcher
and cleanup remove only rules owned by that run; configuration and container fixtures are restored on exit.

## Remaining authenticated/manual release gates

Run these on a disposable Debian 12 / OMV 7 amd64 VM. Use test containers and a dedicated Proton folder. These are
manual release gates; local unit tests do not claim to have exercised them.

01. Install the built package. Verify `dpkg --verify`, plugin navigation, both seeded sets, and disabled scheduling.
    Apply twice and confirm idempotence, service ownership, and a writable private keyring.
02. Exercise sign-in with 2FA, cancellation, timeout, sign-out, offline startup, and invalid/expired session handling.
    Reboot and upgrade the package; check that credentials persist. Check that no auth link or key appears in journals.
03. Capture CLI 0.8.0 JSON for nested list/info, upload and trash. Confirm `name`, `uid`, `type`, and
    `activeRevision.value.claimedSize` match the adapter. Commands must terminate with stdin closed. Add sanitized real
    output to fixtures before release; never include sign-in URLs or credentials.
04. Back up fixture data with a numeric owner different from the VM administrator, setgid, ACLs, xattrs, symlinks and
    excluded directories. Download through Proton and complete the checksum, extraction and database restore exercise.
05. During container archiving, send SIGTERM and SIGKILL to the runner in separate tests. Confirm all recorded
    containers resume, while previously stopped containers remain stopped. Repeat with a VM reboot and with a failed
    Docker start. The recovery record must remain until the final container is running.
06. Run `just vm::retry` for upload interruption and network recovery. Also inject tar failure, low/full staging space,
    and missing sources. Confirm no incomplete archive is published, pending complete archives survive, and a failed
    backup never removes the last confirmed copy. Resume and verify pending uploads are retried before containers are
    stopped again.
07. Start a scheduled and a manual job simultaneously. Verify only one run proceeds. Close the execution dialog's
    browser tab and confirm the job continues; exercise explicit Cancel backup and verify restoration completes.
08. Produce more than the configured retention counts. Verify only the oldest completed pairs are moved to trash. Add
    duplicate names, a same-named folder, incomplete pairs, and unrelated files. Ambiguity must prevent cleanup. No test
    may call `empty-trash` or perform account-wide trash operations.
09. Change the schedule and destination, remove a set, disable scheduling, upgrade, remove, and purge. Verify old
    archives remain available. Inspect systemd units with `systemd-analyze verify` and validate the package with
    Lintian.
10. Confirm the NAS being offline at 03:00 does not cause a catch-up backup at boot. Container recovery must still run
    before any later backup if an interrupted stop was recorded.
