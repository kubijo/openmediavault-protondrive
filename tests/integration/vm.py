"""CLI entry point for disposable OMV VM integration tests."""

import tyro
from vm_runtime import Options, main

if __name__ == '__main__':
    main(tyro.cli(Options))
