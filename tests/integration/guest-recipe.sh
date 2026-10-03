#!/bin/sh

# Leave space before the next prompt while preserving the command's exit status.
set -eu

trap 'printf "\n"' 0
/bin/sh -eu "$@"
