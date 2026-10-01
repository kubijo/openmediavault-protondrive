# Backup behavior and recovery

## Storage and retention

Exclusions are literal paths relative to every source root, including descendants. Backup-set names are immutable. Media
and Docker's data root are not included by default. Source directories must exist when a run starts.

Staging defaults to `/data/.omv-protondrive`. Each set retains two confirmed local archives and seven remote archives.
Unuploaded local archives are preserved. Counts apply independently to each set. The staging directory must not overlap
a source; it is temporary storage on the NAS filesystem, not an independent backup.

Remote folders are `<configured folder>/<instance UUID>/<set UUID>`. An archive and its `.manifest.json` completion
marker form a backup. Manifests contain SHA-256, plaintext size, timestamp, and instance/set identity. Unknown and
incomplete remote files are not automatically deleted. Changing destinations leaves earlier backups where they are.

Expired confirmed remote backups are trashed and then permanently deleted after UID checks. The plugin never empties the
account's trash. An ambiguous trash entry is retained and cleanup reports a failure. Avoid concurrent changes to managed
backup folders and same-named trash entries: the upstream CLI resolves deletion by name, so identity checks cannot
eliminate a concurrent external modification between the check and deletion.

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

Pending uploads are retried before a new archive cycle. A failed retry postpones new archiving. Before stopping
containers, the runner checks estimated uncompressed source size plus a configurable free-space reserve; it also checks
free space while tar runs. Source growth and unrelated filesystem writes can still cause a run to fail.
