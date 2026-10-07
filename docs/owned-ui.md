# Owned web application

The application is served at `/protondrive/` on the OMV web origin. OMV remains responsible for administrator login,
configuration storage, and Save/Apply. The original workbench remains available while recovery workflows are
implemented.

The current application supports account and backup controls, settings, backup-set editing, explicit container and
Compose-project scopes, durable operation URLs, read-only remote browsing, and selected-file extraction. Listing an
archive/manifest pair does not verify its checksum; downloading and inspecting it does. Foreign instances are never
claimed or subjected to retention by browsing or restoration.

This is an incremental development checkpoint. The recovered development VM currently reports an unknown account
(`Proton filesystem info failed (exit 1)`) and remote browsing fails with a generic controller error. Their cause has
not been established. Passing build, unit and basic browser checks does not establish that authenticated restoration
works end to end. Existing archives and the original manual restore procedure are unchanged.

## Remaining work

This is the migration's remaining-work list; implemented foundations are described below, not queued again here.

- [ ] Diagnose and fix the live account-status and remote-browsing failures. Verify a signed-in account and browsing
  both this instance and foreign instances under the configured development root, without claiming foreign storage.
- [ ] Complete authenticated selected-file restoration through the owned UI: inspect a real remote archive, preview,
  extract, verify contents and metadata, reject destination collisions, release the cache and remove owned fixtures.
  Exercise cancellation, controller restart, browser reconnect and recovery of an interrupted probe; retain screenshots,
  video and assertions. Preserve evidence when cleanup or recovery cannot be confirmed.
- [ ] Make account, transport and remote-operation failures actionable. Distinguish sign-in required, temporary outage,
  busy service and invalid archive; preserve useful redacted diagnostics with a correlation to the visible operation.
- [ ] Implement Compose application reconstruction. Explicitly capture the selected Compose definition and selected
  environment/secret files, pin image digests and pull images during preflight. Support bind paths; explicitly reject
  named/anonymous volumes. Do not silently capture Docker environment values or bundle container images.
- [ ] Implement replacement and separate-deployment restore with path/port mapping, conflict preview and preservation of
  original directories. Verify Docker health or configured HTTP/TCP checks (five-minute default); temporarily start
  originally stopped applications for verification, then stop them again. Roll back failed verification, cancellation
  after cutover and reboot during cutover/verification. Retain rollback data for a configurable 24 hours by default, and
  clean it up only after verified success. Keep system restores away from the running host.
- [ ] Close dependency-update reporting coverage for `src/web/app` and the separate API development dependencies. The
  upstream outdated API currently accepts one root per native provider; the existing report covers the probe's pnpm root
  and the root UV inventory. Use the upstream extension mechanism and verify every dependency root is reported.
- [ ] Verify the completed workflows against existing configuration and archives before retiring the legacy view.
  Standalone containers must retain scoped quiescing and file restoration without requiring Compose reconstruction.

## Selected-file extraction

Download and inspect an archive from Remote backups, select entries, and preview extraction into a new directory.
Existing destinations are refused. The preview includes required parent directories and hard-link targets; device nodes
and escaping links are explicit errors. System archives can be extracted elsewhere, never over the running host.

Extraction preserves numeric ownership, permissions, ACLs, extended attributes and links. A private staging directory is
synchronized before atomic publication. Cancellation before publication removes only owned staging; once publication
starts, wait for its result. After a restart, the controller reconciles the journal against directory identities and
ownership markers. Uncertain artifacts are retained with an error for manual recovery. Cleanup errors remain visible;
unresolved extraction journals are retried on restart even after a terminal job error. Verified publication is reported
as successful, and resolved journals are retired with the operation result.

Inspection is limited to 100,000 entries and 16 MiB of indexed metadata. Up to eight archives can be cached, subject to
the configured free-space reserve. Release local copies from the UI when finished; this does not delete remote backups.

## Boundaries

- React Compiler, Rsbuild, Mantine, Sass modules, React Router Data Mode, Connect-Query and TanStack Form live in
  `src/web/app`. Its package manifest declares Node and pnpm; the root flake builds and checks it.
- `proto/protondrive_api/v1/control.proto` defines the contract. Both clients validate requests with Protovalidate.
- nginx authenticates each request through the OMV session. Client-supplied identity headers are overwritten. RPCs
  require a same-origin custom header; no cross-origin permission is granted. Polling does not renew inactive sessions.
- The Connect/Uvicorn process runs as `protondrive-api` behind a Unix socket. Its only privileged interface is a bounded
  Protobuf socket to the root controller, which verifies peer credentials and dispatches fixed operations.
- The controller owns the SQLAlchemy/Alembic SQLite job journal. Request UUIDs prevent duplicate admission; event
  cursors support replay after reconnect. A restart marks abandoned jobs interrupted instead of replaying their side
  effects.
- Debian bundles hash-pinned wheels selected for CPython 3.11 on Debian 12 amd64. Nix virtual environments are not
  copied into the package. Native libraries are declared in the Debian dependencies.

Existing stop-all settings retain their meaning. Explicit scopes stop only selected running containers and recover only
those that were running. Missing selections fail before archive creation. Compose project selection currently scopes
quiescing; it does not yet imply that a backup contains everything needed to reconstruct an application. Automatic
application reconstruction will require an explicitly selected Compose definition. Standalone containers remain
supported for backup quiescing and file restoration; Docker environment values will not be silently captured.

## Development checks

Use the root `generate-api` Nix app to regenerate bindings. Validation compares generated output with the tracked files,
checks the wheel inventory against the API lock, verifies Sass declarations, runs strict Python/TypeScript checks and
unit tests, and builds the production web application and Debian package. Schema dependencies are supplied from a
hash-pinned cache, so these checks need no live registry access.

The browser probe reads the saved VM login itself; see `just vm::probe --help` for owned-UI checks and the reversible
settings/set-edit scenario. It saves screenshots and videos in the selected ignored artifact directory.

During this migration, append `--override-input nix-tools path:/home/kubijo/dev/kubijo/nix-tools --no-write-lock-file`
to every Nix invocation. The committed nix-tools pin and `flake.lock` remain unchanged.
