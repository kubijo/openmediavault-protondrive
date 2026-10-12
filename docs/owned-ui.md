# Owned web application

`/protondrive/` uses OMV sessions, configuration and Save/Apply. It provides account and backup controls, settings,
backup sets, optional OMV Compose sources, operation URLs, remote browsing and selected-file extraction.

Acceptance on 2026-10-07 passed disposable VM lifecycle/cache reuse and authenticated restore, metadata, collision
rejection, cancellation, controller crash, browser reload, separate-process recovery and corrupt CLI lock repair.
Foreign browsing passed only with the disposable fixture; the real account has no second development instance. The
2026-10-10 regression verified unavailable, sign-in-required, busy and invalid-archive failures against the installed
controller, and matched an internal error reference to its journal entry.

## Remaining work

- [ ] Verify authenticated foreign-instance browsing under the development root; requires a second instance.
- [ ] Support database-native exports or atomic snapshots for mutable databases. Live files and OMV Compose rsync
  captures are marked unverified; archive checksums only establish byte integrity.
- [ ] Restore selected files and directories over existing paths with conflict preview and preserved originals. Roll
  back failed publication, cancellation and reboot during publication. Never replace system files on the running host.
- [ ] Check CPU compatibility before upgrading Proton CLI: the 0.9.0 download index omits our `linux-x64-baseline`
  build.

## Extraction and recovery

In Remote backups, download and inspect an archive, select files, then preview extraction into a new directory. Listings
alone do not verify checksums. Existing destinations, device nodes and escaping links are refused; required parents and
hard-link targets appear in the preview. Foreign storage is neither claimed nor subject to retention.

Archiving finishes before upload. Local and remote checksums prove the archive bytes match; they do not prove that files
changed during archiving form a consistent database state.

For an OMV Compose source, select an existing backup-only job with its schedule disabled and name the stacks to include.
The plugin invokes that job once, seals its selected output in private staging, then archives those stack folders and
the shared environment file if present. OMV Compose owns container state. Its current rsync path can miss a copy
failure, so the manifest and UI mark capture unverified even when archive and upload checks pass. Restore the files into
a new directory, then use OMV Compose to restore application data; application deployment is outside this plugin.

Extraction preserves numeric ownership, permissions, ACLs, xattrs and links. Publication is atomic after staging is
synced. Cancellation removes owned staging before publication; once publication starts, wait for its result. Restart
recovery checks directory identities and ownership markers, retries unresolved cleanup, and retains uncertain artifacts
for manual recovery.

Inspection allows 100,000 entries, 16 MiB of indexed metadata and eight cached archives, subject to the free-space
reserve. **Release local copy** removes the cache, not remote backups.

For a corrupt vendor event lock, `omv-protondrive repair-cli-lock` preserves private evidence before removal and refuses
repair during managed CLI operations. Account credentials remain intact.

## Architecture

Each enabled set produces one local archive and uploads it to every enabled destination. Receipts are per archive and
destination; failed targets are retried without reuploading confirmed copies. Local retention counts an archive only
after all currently enabled targets confirm it. Backend authentication, status, browsing and archive inspection use a
destination ID.

- `src/web/app`: React Compiler, Rsbuild, Mantine, Sass modules, React Router Data Mode, Connect-Query and TanStack
  Form.
- `proto/protondrive_api/v1/control.proto`: shared contract with Protovalidate validation.
- nginx: OMV session authentication, overwritten identity headers and same-origin RPC header checks. Polling does not
  renew inactive sessions.
- Connect/Uvicorn runs as `protondrive-api`; a bounded, peer-checked Protobuf Unix socket connects it to the root
  controller.
- SQLAlchemy/Alembic SQLite journal: request UUIDs prevent duplicate admission; event cursors support reconnect.
  Abandoned jobs become interrupted on restart rather than replaying side effects.
- Debian bundles hash-pinned CPython 3.13 wheels and declares native dependencies.

Use `nix run .#generate-api` to regenerate bindings, `just dev::validate` to check the build, and
`just vm::probe --help` for browser assertions and artifacts.
