# Repository tooling

- `console.py`: shared agent detection, Rich consoles, colour policy, and captured-subprocess environment.
- `qa_report.py`: result records, report layout, final states, and exit codes.
- `outdated.py`: read-only upstream version checks, exposed as `just outdated`.
- `audit.py`: runs the audit commands defined in `infra/nix/maintenance.nix`, exposed as `just audit`.
- `check_templates.py` and `check_tracked.py`: template/schema validation and Git-flake source coverage.

These scripts run in the uv/uv2nix tooling environment. The NAS runtime has separate Debian compatibility requirements.
See [maintenance commands](../docs/maintenance.md) for usage and output behaviour.

`tests/` covers reports, audits, console behaviour, and template validation. Its `fixtures/templates/` directory holds
readable valid and intentionally invalid templates. All receive whitespace checks; invalid fixtures are excluded from
Salt lint and must fail rendering or output validation in the tests.

`just/` splits the root justfile into QA, package, maintenance, and integration recipes. Imports preserve the existing
command names and repository working directory; recipes only invoke Nix apps. Run `just` to list them.
