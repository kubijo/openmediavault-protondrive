# Repository tooling

- `console.py`: shared agent detection, Rich consoles and tracebacks, colour policy, and subprocess environment.
- `qa_report.py`: result records, report layout, final states, and exit codes.
- `process_output.py`: streamed subprocess logs and optional Rich stage indicators for VM tests.
- `vm_cache.py`: locked, atomic publication of immutable provisioned VM bases.
- `vm_runtime.py`: shared VM boot, provisioning, and disposable integration orchestration.
- `interactive_vm.py` and `vm_control.py`: persistent interactive VM lifecycle and local QEMU monitor control.
- `web_probe.py`: Chromium assertions and screenshots against a running OMV web URL.
- `vm_flow.py`: disposable, unattended web-change, snapshot-restore, reset, and backup/restore exercise.
- `outdated.py`: consumer policy adapters for the nix-tools outdated app; `just dev::outdated` runs that built-in app.
- `audit.py`: runs the audit commands defined in `infra/nix/maintenance.nix`, exposed as `just dev::audit`.
- `check_templates.py` and `check_tracked.py`: template/schema validation and Git-flake source coverage.
- `build_workbench.py`: package-time assembly of the [overview templates](../src/web/README.md) into native OMV YAML.

These scripts run in the uv/uv2nix tooling environment. The NAS runtime has separate Debian compatibility requirements.
See [maintenance commands](../docs/maintenance.md) for usage and output behaviour.

Crash reports hide local variables and use plain tracebacks in clanker, no-colour, or dumb-terminal mode. The
interactive guest installer shares this policy and installs Debian's `python3-rich`; bootstrap failures before Rich is
available use Python's normal traceback.

`tests/` covers reports, audits, console behaviour, and template validation. Its `fixtures/templates/` directory holds
readable valid and intentionally invalid templates. All receive whitespace checks; invalid fixtures are excluded from
Salt lint and must fail rendering or output validation in the tests.

`just/` contains the `app`, `dev`, `test`, and `vm` modules. Each imports `prelude.just` for shared settings and the
repository working directory. Run `just` to list modules, `just dev` to list development recipes, or
`just --list --list-submodules` to list everything. Invoke recipes with `just dev::validate`, for example.
