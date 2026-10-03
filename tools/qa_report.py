"""Maintenance result models, report layout, and aggregate exit status."""

import json
from collections import Counter
from dataclasses import asdict, dataclass

from console import new_console, plain_output
from rich import box
from rich.table import Table
from rich.text import Text


@dataclass
class Result:
    name: str
    state: str
    current: str = ''
    latest: str = ''
    detail: str = ''
    domain: str = 'Checks'


MAX_WIDTH = 120
STYLES = {
    'passed': 'green',
    'up-to-date': 'green',
    'outdated': 'yellow',
    'failed': 'red',
    'error': 'red',
    'blocked': 'yellow',
    'unknown': 'red',
}


def display_name(result):
    prefixes = {'Nix inputs': 'flake input: ', 'Python dependencies': 'python dependency: ', 'GitHub CI': 'action: '}
    return result.name.removeprefix(prefixes.get(result.domain, ''))


def rich_section(console, domain, components):
    table = Table(
        'Component',
        'State',
        'Current',
        'Latest',
        title=Text(domain, style='bold'),
        title_justify='left',
        box=box.SQUARE,
        row_styles=['', 'on grey15'],
    )
    for result in components:
        table.add_row(
            Text(display_name(result)),
            Text(result.state, style=STYLES.get(result.state, 'dim')),
            Text(result.current),
            Text(result.latest),
            style='dim'
            if result.state in ('passed', 'up-to-date', 'ahead', 'pinned', 'compatibility-pinned', 'skipped')
            else '',
        )
    width = min(MAX_WIDTH, console.width)
    console.print(table, width=width)
    for result in components:
        if result.detail and result.state not in ('passed', 'up-to-date'):
            detail_style = '' if result.state in ('failed', 'error', 'blocked', 'unknown') else 'dim'
            console.print()
            console.print(Text(display_name(result), style=f'bold {detail_style}'), width=width)
            console.print(Text(result.detail, style=detail_style), width=width)
    console.print()


def rich_summary(console, label, state, counts):
    console.print(Text('Summary', style='bold'))
    console.print(
        Text(f'{label}: {state} ({counts})', style=f'bold {STYLES.get(state.lower(), "")}'),
        width=min(MAX_WIDTH, console.width),
    )


def rich_report(results, label, state, counts, no_color):
    console = new_console(no_color=no_color)
    domains = {}
    for result in results:
        domains.setdefault(result.domain, []).append(result)
    for domain, components in domains.items():
        rich_section(console, domain, components)
    rich_summary(console, label, state, counts)


def plain_results(results):
    for result in results:
        versions = f' ({result.current} -> {result.latest})' if result.latest else ''
        print(f'{result.name}: {result.state}{versions}', flush=True)
        if result.state in ('passed', 'up-to-date'):
            continue
        if result.detail:
            lines = result.detail.splitlines()
            if len(lines) > 20:
                print(f'  [{len(lines) - 20} earlier diagnostic lines omitted; use --json for full output]')
                lines = lines[-20:]
            print('  ' + '\n  '.join(lines), flush=True)


def summary(results):
    counts = dict(sorted(Counter(result.state for result in results).items()))
    if not results or counts.get('error') or counts.get('unknown'):
        return 'ERROR', 2, counts
    if counts.get('blocked'):
        return 'BLOCKED', 2, counts
    if counts.get('failed'):
        return 'FAILED', 1, counts
    return 'PASSED', 0, counts


def report(results, *, json_output=False, no_color=False, in_clanker=False):
    state, code, counts = summary(results)
    label = 'AUDIT'
    count_text = ', '.join(f'{n} {s}' for s, n in counts.items())
    if json_output:
        print(json.dumps({'state': state, 'counts': counts, 'components': [asdict(r) for r in results]}, indent=2))
    elif not plain_output(no_color, in_clanker):
        rich_report(results, label, state, count_text, no_color)
    else:
        plain_results(results)
        print(f'\n{label}: {state} ({count_text})')
    return code
