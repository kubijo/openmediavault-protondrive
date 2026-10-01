"""Shared Rich console and subprocess output policy for repository tooling."""

import os

from rich.console import Console
from rich.text import Text

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


def success(message):
    new_console().print(Text.assemble(('PASS: ', 'bold green'), (message, '')))


def error(message):
    new_console(stderr=True).print(Text.assemble(('ERROR: ', 'bold red'), (message, '')))


def child_environment():
    # Captured subprocess diagnostics must remain readable in logs and JSON reports.
    return {
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
