"""CLI entry point for disposable OMV VM integration tests."""

from cli_options import parse_options
from vm_runtime import Options, main

if __name__ == '__main__':
    main(parse_options(Options))
