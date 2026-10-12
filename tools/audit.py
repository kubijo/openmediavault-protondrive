"""Run Nix-defined audit commands and report every result, including tool failures."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from cli_options import parse_options
from console import child_environment, install_traceback
from qa_report import Result, report
from tool_data import decode_value, integer, items, mapping, string


@dataclass
class Options:
    plan: Path
    root: Path = Path('.')
    json: bool = False
    no_color: bool = False
    in_clanker: bool = False


class AuditStep(TypedDict):
    name: str
    command: list[str]
    findingCodes: list[int]
    requiresHead: NotRequired[bool]
    timeout: NotRequired[int]


def parse_step(value: object) -> AuditStep:
    data = mapping(value)
    head = data.get('requiresHead', False)
    if not isinstance(head, bool):
        raise TypeError('requiresHead must be a boolean')
    return {
        'name': string(data['name']),
        'command': [string(arg) for arg in items(data['command'])],
        'findingCodes': [integer(code) for code in items(data['findingCodes'])],
        'requiresHead': head,
        'timeout': integer(data.get('timeout', 300)),
    }


def run_step(step: AuditStep, root: Path) -> Result:
    try:
        if step.get('requiresHead'):
            subprocess.run(['git', 'rev-parse', '--git-dir'], cwd=root, capture_output=True, timeout=15, check=True)
            head = subprocess.run(
                ['git', 'rev-parse', '--verify', 'HEAD'],
                cwd=root,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if head.returncode:
                return Result(
                    step['name'],
                    'skipped',
                    detail='Repository has no commits; working tree and index are still scanned.',
                )
        process = subprocess.run(
            step['command'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=step.get('timeout', 300),
            check=False,
            env=child_environment(),
        )
        state = (
            'passed'
            if process.returncode == 0
            else ('failed' if process.returncode in step['findingCodes'] else 'error')
        )
        return Result(step['name'], state, detail=(process.stdout + process.stderr).strip())
    except (OSError, subprocess.SubprocessError) as exc:
        return Result(step['name'], 'error', detail=f'{type(exc).__name__}: {exc}')


def main(options: Options) -> int:
    install_traceback(no_color=options.no_color, in_clanker=options.in_clanker or options.json)
    try:
        steps = [parse_step(value) for value in items(decode_value(options.plan.read_text()))]
        results = [run_step(step, options.root.resolve()) for step in steps]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        results = [Result('audit configuration', 'error', detail=str(exc))]
    return report(results, json_output=options.json, no_color=options.no_color, in_clanker=options.in_clanker)


if __name__ == '__main__':
    raise SystemExit(main(parse_options(Options)))
