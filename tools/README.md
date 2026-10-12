# Repository tooling

- `console.py`: shared agent detection, Rich consoles and tracebacks, colour policy, and subprocess environment.
- `qa_report.py`: result records, report layout, final states, and exit codes.
- `process_output.py`: streamed subprocess logs and optional Rich stage indicators for VM tests.
- `vm_cache.py`: locked, atomic publication of immutable provisioned VM bases.
- `vm_runtime.py`: shared VM boot, provisioning, and disposable integration orchestration.
- `interactive_vm.py` and `vm_control.py`: persistent interactive VM lifecycle and local QEMU monitor control.
- `web-probe/`: TypeScript Chromium assertions and screenshots against a running OMV web URL. Its pnpm dependencies and
  type check are built by the root flake.
- `vm_flow.py`: disposable, unattended web-change, snapshot-restore, reset, and backup/restore exercise.
- `outdated.py`: consumer policy adapters for the nix-tools outdated app; `just dev::outdated` runs that built-in app.
- `audit.py`: runs the audit commands defined in `infra/nix/maintenance.nix`, exposed as `just dev::audit`.
- `check_templates.py` and `check_tracked.py`: template/schema validation and Git-flake source coverage.
- `build_workbench.py`: package-time assembly of the [overview templates](../src/web/README.md) into native OMV YAML.

The Python scripts run in the uv/uv2nix tooling environment. The web probe uses Node.js and pnpm through the root flake.
The NAS runtime has separate Debian compatibility requirements. See [maintenance commands](../docs/maintenance.md) for
usage and output behaviour.

Crash reports hide locals; clanker/no-colour/dumb-terminal modes use plain tracebacks. Guest bootstrap uses plain
tracebacks until Rich is installed.

`tests/` covers reports, audits, console behaviour, and template validation. Its `fixtures/templates/` directory holds
readable valid and intentionally invalid templates. All receive whitespace checks; invalid fixtures are excluded from
Salt lint and must fail rendering or output validation in the tests.

`just/` contains `app`, `dev`, `test` and `vm` recipes with a shared prelude. Run `just --list --list-submodules` to
list them; invoke with `just dev::validate`, for example.
