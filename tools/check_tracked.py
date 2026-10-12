"""Prevent a successful flake gate from silently omitting untracked files."""

import subprocess
import sys

from console import error

files = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True)
if files:
    error(f'Nix would omit these untracked files:\n{files}')
    sys.exit(1)
