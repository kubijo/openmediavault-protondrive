# Runtime tests

- `unit/`: runtime behaviour, protocol handling, metadata, and recovery.
- `fixtures/`: fake Proton programs and service workloads; no real credentials.
- [`integration/`](integration/README.md): disposable Debian and OMV guests, plus manual release checks.

`just test::unit` runs unit tests and the maintenance tests in [`tools/tests/`](../tools/README.md). `just test::debian`
and `just test::vm` exercise installed packages without touching the NAS.
