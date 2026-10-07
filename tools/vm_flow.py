"""Own disposable VM regression scenarios, evidence, deadlines, and teardown."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from cli_options import parse_options
from process_signals import TerminationRequested, termination_signals
from tool_data import decode, string
from vm_runtime import free_port


@dataclass
class Options:
    vm_up: Path
    vm_control: Path
    vm_command: Path
    web_probe: Path
    test_vm: Path
    reports: Path = Path('.tmp/regression')
    """Parent directory for per-run reports and browser artifacts."""
    cache_dir: Path = Path('.tmp/vm-cache')
    """Shared immutable OMV and installed-plugin cache layers."""
    refresh_base: bool = False
    """Refresh both VM cache layers before this run."""
    keep_failed: bool = False
    """Keep failed disks after shutdown."""
    timeout: int = 1800
    """Seconds per step; browser steps are capped at 600."""


class StepRecord(TypedDict):
    name: str
    command: tuple[str, ...]
    exit: int
    log: str


class FlowReport(TypedDict):
    url: str
    state: str
    steps: list[StepRecord]
    ok: bool
    backend: str
    error: NotRequired[str]
    cleanup_error: NotRequired[str]
    instance_uuids: NotRequired[dict[str, str]]


class Flow:
    def __init__(self, options: Options) -> None:
        self.options = options
        options.reports.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix='run-', dir=options.reports)).resolve()
        self.state = self.directory / 'vm'
        self.ssh_port = free_port()
        self.http_port = free_port()
        while self.http_port == self.ssh_port:
            self.http_port = free_port()
        self.url = f'http://127.0.0.1:{self.http_port}'
        self.report: FlowReport = {
            'url': self.url,
            'state': str(self.state),
            'steps': [],
            'ok': False,
            'backend': 'filesystem fixture (no Proton account)',
        }

    def save_report(self) -> None:
        path = self.directory / 'report.json'
        temporary = path.with_suffix('.next')
        temporary.write_text(json.dumps(self.report, indent=2) + '\n')
        temporary.replace(path)

    def run(self, name: str, *command: str, timeout: int | None = None) -> Path:
        print(f'{name} …', flush=True)
        log = self.directory / f'{name}.log'
        code = 1
        try:
            with (
                log.open('w') as output,
                subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                ) as process,
            ):
                try:
                    code = process.wait(timeout=timeout or self.options.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                    code = (
                        124
                        if isinstance(error, subprocess.TimeoutExpired)
                        else 128 + (error.signum if isinstance(error, TerminationRequested) else signal.SIGINT)
                    )
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    if isinstance(error, KeyboardInterrupt):
                        raise
        finally:
            self.report['steps'].append({'name': name, 'command': command, 'exit': code, 'log': str(log)})
            self.save_report()
        if code:
            raise RuntimeError(f'{name} failed with exit {code}; see {log}')
        print(f'{name}: PASS', flush=True)
        return log

    def vm(self, name: str, action: str, *args: str) -> None:
        if action == 'up':
            command = [str(self.options.vm_up), '--regression', '--cache-dir', str(self.options.cache_dir.resolve())]
        else:
            command = [str(self.options.vm_control), action]
        self.run(name, *command, '--state-dir', str(self.state), *args)

    def start(self, name: str) -> None:
        self.vm(
            name,
            'up',
            '--no-shell',
            '--ssh-port',
            str(self.ssh_port),
            '--http-port',
            str(self.http_port),
            *(['--refresh-base'] if name == 'init' and self.options.refresh_base else []),
        )

    def probe(self, name: str, *args: str, widths: tuple[int, ...] = (420,)) -> str:
        dimensions = [argument for width in widths for argument in ('--width', str(width))]
        log = self.run(
            name,
            str(self.options.web_probe),
            '--state-dir',
            str(self.state),
            '--url',
            self.url,
            '--output',
            str(self.directory / 'browser' / name),
            '--json',
            *dimensions,
            *args,
            timeout=min(self.options.timeout, 600),
        )
        for line in reversed(log.read_text().splitlines()):
            try:
                result = decode(line)
            except (ValueError, TypeError):
                continue
            if 'ok' in result:
                if result['ok'] is True and isinstance(result.get('remote_folder'), str):
                    return string(result['remote_folder'])
                break
        raise RuntimeError(f'{name} had no successful browser result; see {log}')

    def instance_uuid(self) -> str:
        value = decode((self.state / 'instance/instance.json').read_text())['proton_instance_uuid']
        if not isinstance(value, str) or not value:
            raise RuntimeError('The VM did not record its backup instance UUID')
        return value

    def scenarios(self) -> None:
        self.start('init')
        initial_remote = self.probe('initial-web', '--expect-hour', '3')
        initial_instance = self.instance_uuid()
        self.probe(
            'owned-backup-restore',
            '--owned-ui',
            '--owned-ui-backup',
            '--owned-ui-restore',
            '--owned-ui-resilience',
            '--owned-ui-foreign',
        )
        self.probe('owned-interrupt', '--owned-ui', '--owned-ui-restore', '--owned-ui-interrupt')
        self.probe('owned-recover', '--owned-ui', '--owned-ui-restore', '--recover')
        self.run(
            'cli-lock-recovery',
            str(self.options.vm_command),
            '--state-dir',
            str(self.state),
            'exec',
            '--',
            'just',
            'check-cli-lock',
        )
        self.probe('owned-layout', '--owned-ui', '--expect-signed-in', widths=(1440, 420, 320))
        self.probe('owned-edits', '--owned-ui', '--owned-ui-changes')
        self.run(
            'restore-metadata',
            str(self.options.vm_command),
            '--state-dir',
            str(self.state),
            'exec',
            '--',
            'python3',
            '/usr/local/lib/omv-protondrive-vm/regression_guest.py',
            'metadata',
        )
        self.vm('stop-before-snapshot', 'down')
        self.vm('snapshot', 'snapshot', '--snapshot-name', 'baseline')
        self.start('resume-for-change')
        self.probe('change-web', '--expect-hour', '3', '--change-hour', '4')
        if (
            self.probe('changed-web', '--expect-hour', '4') != initial_remote
            or self.instance_uuid() != initial_instance
        ):
            raise RuntimeError('Changing the backup hour changed the remote identity')
        self.vm('stop-before-restore', 'down')
        self.vm('restore', 'restore', '--snapshot-name', 'baseline')
        self.start('resume-after-restore')
        if (
            self.probe('restored-web', '--expect-hour', '3') != initial_remote
            or self.instance_uuid() != initial_instance
        ):
            raise RuntimeError('Restored VM did not recover its original remote identity')
        self.vm('stop-before-reset', 'down')
        self.vm('reset', 'reset', '--yes')
        self.start('fresh-init')
        reset_remote = self.probe('reset-web', '--owned-ui', '--expect-hour', '3')
        reset_instance = self.instance_uuid()
        if reset_remote != initial_remote or reset_instance == initial_instance:
            raise RuntimeError('Reset VM did not use the development root with a new instance UUID')
        self.report['instance_uuids'] = {'initial': initial_instance, 'reset': reset_instance}
        self.vm('stop-after-reset', 'down')
        # Install/removal tests must start below the installed-plugin layer.
        self.run(
            'service-regression',
            str(self.options.test_vm),
            '--reports',
            str(self.directory / 'services'),
            '--cache-dir',
            str(self.options.cache_dir.resolve()),
            '--timeout',
            str(self.options.timeout),
            *(['--keep-failed'] if self.options.keep_failed else []),
        )

    def teardown(self, failed: bool) -> None:
        try:
            self.vm('teardown-stop', 'down')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.vm('teardown-force-stop', 'down', '--force')
        if not failed or not self.options.keep_failed:
            self.vm('teardown-delete', 'reset', '--yes')

    def execute(self) -> int:
        code = 1
        try:
            self.scenarios()
            code = 0
        except KeyboardInterrupt as error:
            code = 128 + (error.signum if isinstance(error, TerminationRequested) else signal.SIGINT)
            self.report['error'] = str(error) or 'Cancelled'
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            self.report['error'] = str(error)
        finally:
            try:
                try:
                    self.teardown(failed=code != 0)
                except KeyboardInterrupt as error:
                    # Retry interrupted teardown; termination_signals ignores further signals.
                    code = 128 + (error.signum if isinstance(error, TerminationRequested) else signal.SIGINT)
                    self.report.setdefault('error', str(error) or 'Cancelled during teardown')
                    self.teardown(failed=True)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.report['cleanup_error'] = str(error)
                code = code or 1
            self.report['ok'] = code == 0
            self.save_report()
        if code:
            print(self.report.get('error', 'Regression teardown failed'), file=sys.stderr)
            if 'cleanup_error' in self.report:
                print(f'TEARDOWN FAILED: {self.report["cleanup_error"]}', file=sys.stderr)
        print(f'{"PASS" if code == 0 else "FAIL"}: regression artifacts: {self.directory}', flush=True)
        return code


def main(options: Options) -> int:
    if options.timeout <= 0:
        raise ValueError('--timeout must be positive')
    with termination_signals():
        return Flow(options).execute()


if __name__ == '__main__':
    raise SystemExit(main(parse_options(Options)))
