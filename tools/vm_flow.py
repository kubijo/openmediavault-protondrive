"""Run a disposable VM's web change, disk restore, reset, and backup/restore checks."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from process_signals import termination_signals
from tool_data import decode, string
from vm_runtime import free_port


class StepRecord(TypedDict):
    name: str
    command: tuple[str, ...]
    exit: int
    log: str


class FlowReport(TypedDict):
    url: str
    state: str
    steps: list[StepRecord]
    remote_folders: NotRequired[dict[str, str]]
    instance_uuids: NotRequired[dict[str, str]]
    ok: NotRequired[bool]
    error: NotRequired[str]


def main() -> int:
    root = Path.cwd()
    if not (root / 'justfile').is_file():
        raise SystemExit('Run from the repository root')
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    base = (root / '.tmp/autonomous-flow').resolve()
    base.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f'{stamp}-', dir=base))
    state = directory / 'vm'
    ssh_port = free_port()
    http_port = free_port()
    while http_port == ssh_port:
        http_port = free_port()
    url = f'http://127.0.0.1:{http_port}'
    report: FlowReport = {'url': url, 'state': str(state), 'steps': []}

    def run(name: str, *command: str, timeout: float = 3600) -> Path:
        print(f'{name} …', flush=True)
        log = directory / f'{name}.log'
        with (
            log.open('w') as output,
            subprocess.Popen(
                command, cwd=root, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            ) as process,
        ):
            try:
                code = process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                if isinstance(error, KeyboardInterrupt):
                    raise
                code = 124
        report['steps'].append({'name': name, 'command': command, 'exit': code, 'log': str(log)})
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        if code:
            raise RuntimeError(f'{name} failed with exit {code}; see {log}')
        print(f'{name}: PASS', flush=True)
        return log

    def vm(name: str, action: str, *args: str) -> None:
        run(name, 'just', f'vm::{action}', '--state-dir', str(state), *args)

    def probe(name: str, *args: str) -> str:
        log = run(
            name,
            'just',
            'vm::probe',
            '--state-dir',
            str(state),
            '--url',
            url,
            '--output',
            str(directory / 'screenshots' / name),
            '--json',
            '--width',
            '420',
            *args,
            timeout=180,
        )
        for line in reversed(log.read_text().splitlines()):
            try:
                result = decode(line)
            except (ValueError, TypeError):
                continue
            if 'ok' in result:
                if result['ok'] and isinstance(result.get('remote_folder'), str):
                    return string(result['remote_folder'])
                break
        raise RuntimeError(f'{name} had no valid browser result; see {log}')

    def instance_uuid() -> str:
        value = decode((state / 'instance/instance.json').read_text())['proton_instance_uuid']
        if not isinstance(value, str) or not value:
            raise RuntimeError('The VM did not record its Proton backup instance UUID')
        return value

    try:
        vm('init', 'up', '--no-shell', '--ssh-port', str(ssh_port), '--http-port', str(http_port))
        initial_remote = probe('initial-web', '--expect-hour', '3')
        initial_instance = instance_uuid()
        vm('stop-before-snapshot', 'down')
        vm('snapshot', 'snapshot', '--snapshot-name', 'baseline')
        vm('resume-for-change', 'up', '--no-shell')
        probe('change-web', '--expect-hour', '3', '--change-hour', '4')
        changed_remote = probe('changed-web', '--expect-hour', '4')
        if changed_remote != initial_remote or instance_uuid() != initial_instance:
            raise RuntimeError('Changing the backup hour unexpectedly changed the remote identity')
        vm('stop-before-restore', 'down')
        vm('restore', 'restore', '--snapshot-name', 'baseline')
        vm('resume-after-restore', 'up', '--no-shell')
        restored_remote = probe('restored-web', '--expect-hour', '3')
        if restored_remote != initial_remote or instance_uuid() != initial_instance:
            raise RuntimeError('Restored VM did not recover its original remote identity')
        vm('stop-before-reset', 'down')
        vm('reset', 'reset', '--yes')
        vm('fresh-init', 'up', '--no-shell', '--ssh-port', str(ssh_port), '--http-port', str(http_port))
        reset_remote = probe('reset-web', '--expect-hour', '3')
        reset_instance = instance_uuid()
        if reset_remote != initial_remote or reset_instance == initial_instance:
            raise RuntimeError('Reset VM did not use the shared development root with a new instance UUID')
        report['remote_folders'] = {'initial': initial_remote, 'reset': reset_remote}
        report['instance_uuids'] = {'initial': initial_instance, 'reset': reset_instance}
        vm('stop-after-reset', 'down')
        run('backup-restore', 'just', 'test::vm', '--reports', str(directory / 'backup-restore'), '--keep-failed')
        vm('delete-disposable-disk', 'reset', '--yes')
    except (OSError, RuntimeError, KeyboardInterrupt) as error:
        report['ok'] = False
        report['error'] = str(error)
        (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(error, file=sys.stderr)
        try:
            vm('stop-after-failure', 'down')
        except (OSError, RuntimeError, KeyboardInterrupt) as stop_error:
            print(f'Could not stop failed VM: {stop_error}', file=sys.stderr)
        print(f'Artifacts and failed state: {directory}', file=sys.stderr)
        return 1
    report['ok'] = True
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Flow passed; screenshots and logs: {directory}')
    return 0


if __name__ == '__main__':
    with termination_signals():
        raise SystemExit(main())
