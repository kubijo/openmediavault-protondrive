# Backup behavior and recovery

## Storage and retention

Exclusions are literal paths relative to every source root, including descendants. Backup-set names are immutable. Media
and Docker's data root are not included by default. Source directories must exist when a run starts.

Staging defaults to `/data/.omv-protondrive`. Each set retains two confirmed local archives and seven remote archives.
Unuploaded local archives are preserved. Counts apply independently to each set. The staging directory must not overlap
a source; it is temporary storage on the NAS filesystem, not an independent backup.

The root defaults to `/my-files/open-media-vault-proton-backup`; the administrator can choose a different single folder
directly under `/my-files`. The interactive test VM uses `/my-files/open-media-vault-proton-backup-development` so test
backups remain visibly separate. Remote folders are `<root>/<instance UUID>/<set UUID>`. Each instance directory has a
private owner marker. A different installation with a copied instance UUID and a different local owner identity is
refused; an existing nonempty instance directory without a marker is also refused rather than silently claimed. A full
clone containing both the same private local owner identity and the same instance UUID cannot be distinguished; do not
run both clones against the same Proton account and root. The CLI offers no atomic folder claim, so an older writer that
ignores owner markers must not write into the same instance directory concurrently with a new claim.

New claims are recorded privately in `/var/lib/openmediavault-protondrive/proton/pending-claims` before uploading the
owner marker. Interrupted or rejected claims remain blocked across service restarts. Before removing a pending record,
stop the service and inspect its destination: the remote directory must contain only the matching owner marker (or be
empty). If any other contents exist, leave the claim blocked and choose a new instance UUID; do not delete those
contents to force adoption. Successfully verified claims remove their pending record automatically.

An archive and its `.manifest.json` completion marker form a backup. Manifests contain SHA-256, plaintext size,
timestamp, and instance/set identity. Unknown and incomplete remote files are not automatically deleted. Changing
destinations leaves earlier backups where they are. Before publishing a completion marker, the service downloads the
remote archive to private temporary space and checks its SHA-256. A retry of an already completed pair does the same
check. Verification needs free temporary space for the archive plus the configured minimum reserve; without it, the
backup fails and the local archive remains for retry.

Expired confirmed remote backups are moved to trash after UID checks. The plugin never accesses or empties account-wide
trash; expired backups remain there until the administrator empties it. Ambiguous entries are retained and cleanup
reports a failure. Avoid concurrent changes to managed backup folders: the upstream CLI resolves trash operations by
name, so identity checks cannot eliminate a concurrent external modification between the check and trash operation.

## Execution and recovery

Root creates the archives; the `protondrive` system user owns the private D-Bus/keyring session and runs the Proton CLI.
The user has no Docker access. Completed staging files are root-owned and readable by that user's group.

Before stopping containers, the runner persists their full IDs. It archives all container-sensitive sets during one
stopped interval and restarts those same containers before verification and upload. Archive errors and cancellation also
trigger recovery. Systemd runs recovery after abnormal runner termination; boot recovery handles power loss. A failed
restart leaves a durable recovery record and fails the job. A second backup cannot run concurrently.

**Cancel backup** stops the supervised job and restores containers. Closing the browser or losing its connection leaves
the job running. Configuration saves and Salt deployment check that no backup is active.

The private session survives reboot and package upgrades. Authentication URLs are transient and omitted from logs.
Removal retains backups and session state; package purge removes credentials/configuration but retains archives and
recovery evidence. Package upgrades stop jobs and recover containers before replacing their code; Apply the pending
configuration afterward to restart the service and timer.

Inspect results through the Proton Drive status page and **Diagnostics → System Logs → Proton Drive**, or:

```sh
omv-protondrive status
journalctl -u omv-protondrive-backup.service
omv-protondrive recover
```

During a backup, the status page shows the active upload or verification download, its filename, and elapsed time. These
are operation timings; the Proton CLI does not expose machine-readable byte progress.

Pending uploads are retried before a new archive cycle. A failed retry postpones new archiving. Before stopping
containers, the runner checks estimated uncompressed source size plus a configurable free-space reserve; it also checks
free space while tar runs. Source growth and unrelated filesystem writes can still cause a run to fail.
