"""Shared Rich console and subprocess output policy for repository tooling."""

import os
import re
import sys
import traceback

from rich.console import Console
from rich.text import Text
from rich.traceback import install as install_rich_traceback

AGENT_ENVS = ('CLAUDECODE', 'CURSOR_AGENT', 'GEMINI_CLI', 'CODEX_THREAD_ID', 'OPENCODE', 'IN_CLANKER', 'in-clanker')


def agent_mode(in_clanker=False):
    return in_clanker or any(name in os.environ for name in AGENT_ENVS)


def color_disabled(no_color=False):
    return no_color or 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb'


def force_color():
    return os.environ.get('FORCE_COLOR') in ('1', '2', '3', 'true')


def plain_output(no_color=False, in_clanker=False):
    if in_clanker or os.environ.get('FORCE_COLOR') == '0':
        return True
    return agent_mode() and not (force_color() and not color_disabled(no_color))


def new_console(*, stderr=False, no_color=False, in_clanker=False):
    plain = plain_output(no_color, in_clanker)
    disabled = plain or color_disabled(no_color)
    terminal = False if plain else (True if force_color() and not disabled else None)
    return Console(
        stderr=stderr,
        force_terminal=terminal,
        no_color=disabled,
        color_system=None if disabled else 'auto',
        highlight=False,
        markup=False,
    )


def live_output(console, *, no_color=False, in_clanker=False):
    return (
        not agent_mode(in_clanker)
        and not color_disabled(no_color)
        and os.environ.get('FORCE_COLOR') != '0'
        and console.is_terminal
        and console.file.isatty()
    )


def install_traceback(*, no_color=False, in_clanker=False):
    """Install readable crash reports without dumping credential-bearing locals."""
    previous = sys.excepthook
    if plain_output(no_color, in_clanker) or color_disabled(no_color):
        # Python's default hook can force ANSI independently of our CLI flags.
        sys.excepthook = traceback.print_exception
    else:
        install_rich_traceback(console=new_console(stderr=True), show_locals=False, width=100)
    return previous


def print_exception(*, no_color=False, in_clanker=False):
    """Render a caught exception using the same policy as unhandled crashes."""
    if plain_output(no_color, in_clanker) or color_disabled(no_color):
        traceback.print_exc()
    else:
        new_console(stderr=True).print_exception(show_locals=False, width=100)


def success(message):
    new_console().print(Text.assemble(('PASS: ', 'bold green'), (message, '')))


def error(message):
    new_console(stderr=True).print(Text.assemble(('ERROR: ', 'bold red'), (message, '')))


def style_diagnostic(text):
    """Normalize tool palettes; reserve colour for meaningful status labels."""
    text = Text(text.plain)
    line = text.plain.lstrip()
    offset = len(text.plain) - len(line)
    for prefix, style in (
        ('RUN ', 'dim'),
        ('PASS:', 'bold green'),
        ('FAILED:', 'bold red'),
        ('ERROR:', 'bold red'),
        ('WARNING:', 'yellow'),
        ('[ERROR', 'bold red'),
        ('[WARNING', 'yellow'),
    ):
        if line.startswith(prefix):
            text.stylize(style, offset, offset + len(prefix))
            return text

    # Salt's highstate output uses colour for whole blocks. Highlight its status
    # values instead, so success, failure and changes have one consistent meaning.
    if line.startswith(('ID:', 'Summary for ')):
        text.stylize('bold cyan')
    elif line.startswith(('Started:', 'Duration:', 'Total states run:', 'Total run time:')):
        text.stylize('dim')
    elif result := re.match(r'\s*(Result|Succeeded|Failed):\s*(\S+)', text.plain):
        label, value = result.groups()
        if label == 'Result':
            style = {'True': 'green', 'False': 'bold red', 'None': 'yellow'}.get(value, '')
        elif value.isdecimal():
            style = 'dim' if int(value) == 0 else ('bold red' if label == 'Failed' else 'green')
        else:
            style = ''
        text.stylize(style, result.start(2), result.end(2))
        text.highlight_regex(r'\bchanged=[1-9]\d*', 'yellow')
    return text


def child_environment(*, terminal=False):
    # Captured subprocess diagnostics must remain readable in logs and JSON reports.
    environment = {
        **os.environ,
        'NO_COLOR': '1',
        'FORCE_COLOR': '0',
        'CLICOLOR_FORCE': '0',
        'TERM': 'dumb',
        'UV_COLOR': 'never',
        'PYTHON_COLORS': '0',
        'PY_COLORS': '0',
        'GIT_TERMINAL_PROMPT': '0',
        'GH_PROMPT_DISABLED': '1',
    }
    if terminal:
        environment.pop('NO_COLOR', None)
        environment.update(
            TERM='xterm-256color',
            FORCE_COLOR='1',
            CLICOLOR_FORCE='1',
            UV_COLOR='always',
            PYTHON_COLORS='1',
            PY_COLORS='1',
        )
    return environment
