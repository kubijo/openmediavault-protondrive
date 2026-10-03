"""Run Nix-defined audit commands and report every result, including tool failures."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import tyro
from console import child_environment, install_traceback
from qa_report import Result, report


@dataclass
class Options:
    plan: Path
    root: Path = Path('.')
    json: bool = False
    no_color: bool = False
    in_clanker: bool = False


def run_step(step, root):
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


def main(options: Options):
    install_traceback(no_color=options.no_color, in_clanker=options.in_clanker or options.json)
    try:
        steps = json.loads(options.plan.read_text())
        results = [run_step(step, options.root.resolve()) for step in steps]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        results = [Result('audit configuration', 'error', detail=str(exc))]
    return report(results, json_output=options.json, no_color=options.no_color, in_clanker=options.in_clanker)


if __name__ == '__main__':
    raise SystemExit(main(tyro.cli(Options)))
