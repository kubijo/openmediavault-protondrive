"""VM panels using just's native recipe listing and colours."""

import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


def path(console: Console, label: str, value: str | Path) -> None:
    console.print(Text.assemble((f'{label}: ', 'dim'), (str(value), 'blue')), soft_wrap=True)


def recipes(console: Console, title: str, justfile: str | Path, *, prefix: str = '') -> None:
    result = subprocess.run(
        [
            'just',
            '--justfile',
            str(justfile),
            '--color',
            'always' if console.color_system and not console.no_color else 'never',
            '--list-heading',
            '',
            '--list-prefix',
            prefix,
            '--list',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    listing = Text.from_ansi(result.stdout.rstrip())
    width = min(console.width, max(line.cell_len for line in listing.split('\n')) + 6)
    console.print(Panel(listing, title=title, border_style='dim', padding=(1, 2), width=width))
    console.print()
