# Owned web application

`/protondrive/` uses OMV sessions, configuration and Save/Apply. It provides account and backup controls, settings,
backup sets, container/Compose scopes, operation URLs, remote browsing and selected-file extraction. The legacy
workbench remains during migration. Compose scopes currently control quiescing, not application reconstruction.

Acceptance on 2026-10-07 passed disposable VM lifecycle/cache reuse and authenticated restore, metadata, collision
rejection, cancellation, controller crash, browser reload, separate-process recovery and corrupt CLI lock repair.
Foreign browsing passed only with the disposable fixture; the real account has no second development instance. The
2026-10-10 regression verified unavailable, sign-in-required, busy and invalid-archive failures against the installed
controller, and matched an internal error reference to its journal entry.

## Remaining work

- [ ] Verify authenticated foreign-instance browsing under the development root; requires a second instance.
- [ ] Reconstruct Compose applications: capture explicitly selected definitions and environment/secret files, pin image
  digests and pull during preflight. Record which services were running before capture. Support bind paths; reject
  named/anonymous volumes. Never capture Docker environment values implicitly or bundle images.
- [ ] Restore replacements and separate deployments with path/port mapping, conflict preview and preserved originals.
  Verify Docker health or configured HTTP/TCP checks (five-minute default). Temporarily start originally stopped apps
  for verification, then stop them. Roll back failed verification, cancellation after cutover and reboot during
  cutover/verification. Retain rollback data for a configurable 24 hours; delete only after verified success. Never
  restore system files over the running host.
- [ ] Check CPU compatibility before upgrading Proton CLI: the 0.9.0 download index omits our `linux-x64-baseline`
  build.
- [ ] Verify existing configuration and archives before retiring the legacy view. Preserve standalone-container
  quiescing and file restoration independently of Compose reconstruction.

## Extraction and recovery

In Remote backups, download and inspect an archive, select files, then preview extraction into a new directory. Listings
alone do not verify checksums. Existing destinations, device nodes and escaping links are refused; required parents and
hard-link targets appear in the preview. Foreign storage is neither claimed nor subject to retention.

For a selected Compose application, the backup set names its definition, environment and secret files explicitly. Those
files and every bind source must be covered by backup sources. Preflight records service images by registry digest and
pulls them before stopping containers; named and anonymous volumes or images without a pullable digest fail preflight.
The completion manifest records the selected files, pinned images, replica counts and bind paths. Archive inspection
checks that the named files and binds exist in the verified tar and shows the snapshot. Application deployment and
rollback are tracked separately above.

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
destination ID. Existing installations map their single Proton root to the `protondrive` destination automatically.

- `src/web/app`: React Compiler, Rsbuild, Mantine, Sass modules, React Router Data Mode, Connect-Query and TanStack
  Form.
- `proto/protondrive_api/v1/control.proto`: shared contract with Protovalidate validation.
- nginx: OMV session authentication, overwritten identity headers and same-origin RPC header checks. Polling does not
  renew inactive sessions.
- Connect/Uvicorn runs as `protondrive-api`; a bounded, peer-checked Protobuf Unix socket connects it to the root
  controller.
- SQLAlchemy/Alembic SQLite journal: request UUIDs prevent duplicate admission; event cursors support reconnect.
  Abandoned jobs become interrupted on restart rather than replaying side effects.
- Debian bundles hash-pinned CPython 3.11 wheels and declares native dependencies.

Explicit scopes stop only selected running containers and recover only those previously running. Missing selections fail
before archiving; existing stop-all settings retain their meaning.

Use `nix run .#generate-api` to regenerate bindings, `just dev::validate` to check the build, and
`just vm::probe --help` for browser assertions and artifacts.
