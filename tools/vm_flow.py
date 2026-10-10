"""Own disposable VM regression scenarios, evidence, deadlines, and teardown."""

import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from encodings.utf_8 import IncrementalDecoder
from pathlib import Path
from typing import NotRequired, TypedDict

from rich import box
from rich.console import Console, Group
from rich.constrain import Constrain
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from cli_options import parse_options
from console import live_output, new_console, path_text
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
    no_color: bool = False
    in_clanker: bool = False


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
    diagnostics_log: NotRequired[str]
    instance_uuids: NotRequired[dict[str, str]]


@dataclass
class StepView:
    name: str
    started: float
    stage: str = ''
    elapsed: float = 0
    exit: int | None = None


class FlowDisplay:
    def __init__(
        self,
        directory: Path,
        *,
        console: Console | None = None,
        animate: bool | None = None,
        no_color: bool = False,
        in_clanker: bool = False,
    ) -> None:
        self.directory = directory
        self.console = console if console is not None else new_console(no_color=no_color, in_clanker=in_clanker)
        self.animate = (
            live_output(self.console, no_color=no_color, in_clanker=in_clanker) if animate is None else animate
        )
        self.steps: list[StepView] = []
        self.spinner = Spinner('dots', text='RUNNING', style='cyan')
        self.live: Live | None = None

    def render(self) -> Group:
        detail_width = min(120, self.console.width)
        table = Table(
            title=Text('Regression steps', style='bold'),
            title_justify='left',
            box=box.SQUARE,
            border_style='grey35',
            row_styles=['', 'on grey15'],
        )
        table.add_column('Step')
        table.add_column('Status', width=9, no_wrap=True)
        table.add_column('Time', width=8, no_wrap=True, justify='right')
        for step in self.steps:
            if step.exit is None:
                status = self.spinner
            else:
                status = Text('PASS' if step.exit == 0 else 'FAIL', style='green' if step.exit == 0 else 'bold red')
            elapsed = time.monotonic() - step.started if step.exit is None else step.elapsed
            table.add_row(Text(step.name), status, Text(f'{elapsed:.1f}s', style='dim'))
        current = self.steps[-1] if self.steps and self.steps[-1].exit is None else None
        details = (
            [
                Panel(
                    Group(
                        Text(current.stage or 'Starting', style='cyan'),
                        Text.assemble(
                            ('Log: ', 'dim'), path_text(self.directory / f'{current.name}.log', self.console)
                        ),
                    ),
                    title=current.name,
                    border_style='cyan',
                    width=detail_width,
                )
            ]
            if current
            else []
        )
        return Group(
            Constrain(table, width=120),
            *details,
            Text.assemble(('Artifacts: ', 'dim'), path_text(self.directory, self.console)),
        )

    def open(self) -> None:
        if self.animate:
            self.live = Live(self.render(), console=self.console, refresh_per_second=8, transient=True)
            self.live.start()
        else:
            self.console.print(Text.assemble(('Artifacts: ', 'dim'), path_text(self.directory, self.console)))

    def refresh(self) -> None:
        if self.live is not None:
            self.live.update(self.render())

    def begin(self, name: str) -> None:
        self.steps.append(StepView(name=name, started=time.monotonic()))
        if self.animate:
            self.refresh()
        else:
            self.console.print(f'{name} …')

    def progress(self, name: str, stage: str) -> None:
        current = self.steps[-1]
        if stage == current.stage:
            return
        current.stage = stage
        if self.animate:
            self.refresh()
        else:
            self.console.print(f'{name}: {stage}')

    def heartbeat(self, name: str, log: Path, elapsed: int, size: int) -> None:
        if self.animate:
            self.refresh()
        else:
            stage = self.steps[-1].stage or 'starting'
            self.console.print(
                Text.assemble(f'{name}: {elapsed}s in {stage}; log ', path_text(log, self.console), f' ({size} bytes)')
            )

    def finish(self, name: str, code: int) -> None:
        current = self.steps[-1]
        current.exit = code
        current.elapsed = time.monotonic() - current.started
        if self.animate:
            self.refresh()
        else:
            self.console.print(
                Text.assemble(
                    (f'{name}: ', 'dim'), ('PASS' if code == 0 else 'FAIL', 'green' if code == 0 else 'bold red')
                )
            )

    def close(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            self.console.print(self.render())


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
        self.report = FlowReport(
            url=self.url,
            state=str(self.state),
            steps=[],
            ok=False,
            backend='filesystem fixture (no Proton account)',
        )
        self.display = FlowDisplay(self.directory, no_color=options.no_color, in_clanker=options.in_clanker)

    def save_report(self) -> None:
        path = self.directory / 'report.json'
        temporary = path.with_suffix('.next')
        temporary.write_text(json.dumps(self.report, indent=2) + '\n')
        temporary.replace(path)

    def capture_diagnostics(self) -> None:
        try:
            result = subprocess.run(
                [
                    str(self.options.vm_command),
                    '--state-dir',
                    str(self.state),
                    'exec',
                    '--',
                    'journalctl',
                    '--unit=omv-protondrive-controller',
                    '--unit=openmediavault-engined',
                    '--no-pager',
                    '--output=short-iso',
                    '--lines=300',
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if result.returncode == 0:
            path = self.directory / 'failure-journal.log'
            path.write_text(result.stdout)
            self.report['diagnostics_log'] = str(path)

    def wait_for_step(self, process: subprocess.Popen[bytes], name: str, log: Path, timeout: int) -> int:
        started = time.monotonic()
        deadline = started + timeout
        heartbeat = started + 30
        refresh = started + 1
        decoder = IncrementalDecoder(errors='replace')
        pending = ''
        with log.open('rb') as stream:
            while True:
                while data := stream.read(65536):
                    lines = (pending + decoder.decode(data)).split('\n')
                    pending = lines.pop()
                    for line in lines:
                        update = self.progress_line(line)
                        if update is not None:
                            self.display.progress(name, update)
                if process.poll() is not None:
                    pending += decoder.decode(b'', final=True)
                    update = self.progress_line(pending)
                    if update is not None:
                        self.display.progress(name, update)
                    return process.wait()
                now = time.monotonic()
                if now >= deadline:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                if now >= heartbeat:
                    self.display.heartbeat(name, log, int(now - started), stream.tell())
                    heartbeat = now + 30
                if self.display.animate and now >= refresh:
                    self.display.refresh()
                    refresh = now + 1
                time.sleep(0.05)

    @staticmethod
    def progress_line(line: str) -> str | None:
        value = line.rsplit('\r', 1)[-1].strip()
        if value.startswith("RUN ('apt-get', 'update'"):
            return 'Refreshing guest package lists'
        if value.startswith(("RUN ('apt-get', 'install'", "RUN ('apt-get', 'upgrade'")):
            return 'Installing guest packages'
        if value.startswith("RUN ('omv-rpc',"):
            return 'Applying OMV configuration'
        if value.startswith('Setting up openmediavault'):
            return value
        if value.startswith(
            (
                'Resolve ',
                'Prepare ',
                'Boot ',
                'Provision ',
                'Install ',
                'Seal ',
                'Transfer ',
                'Stop ',
                'CACHE ',
                'INSTALLED CACHE ',
                'Waiting for ',
                'DONE:',
                'FAILED:',
                'PASS:',
            )
        ):
            return value
        return None

    def run(self, name: str, *command: str, timeout: int | None = None) -> Path:
        log = self.directory / f'{name}.log'
        self.display.begin(name)
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
                    code = self.wait_for_step(process, name, log, timeout or self.options.timeout)
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
            self.report['steps'].append(StepRecord(name=name, command=command, exit=code, log=str(log)))
            self.save_report()
            self.display.finish(name, code)
        if code:
            raise RuntimeError(f'{name} failed with exit {code}; see {log}')
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
            'failure-reporting',
            str(self.options.vm_command),
            '--state-dir',
            str(self.state),
            'exec',
            '--',
            'env',
            'PYTHONPATH=/usr/share/openmediavault-protondrive:/usr/lib/openmediavault-protondrive/api',
            'python3',
            '/usr/local/lib/omv-protondrive-vm/failure_reporting_guest.py',
        )
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
        self.display.open()
        try:
            self.scenarios()
            code = 0
        except KeyboardInterrupt as error:
            code = 128 + (error.signum if isinstance(error, TerminationRequested) else signal.SIGINT)
            self.report['error'] = str(error) or 'Cancelled'
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            self.report['error'] = str(error)
            self.capture_diagnostics()
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
            self.display.close()
        if code:
            diagnostic = new_console(stderr=True, no_color=self.options.no_color, in_clanker=self.options.in_clanker)
            diagnostic.print(
                Text.assemble(('ERROR: ', 'bold red'), self.report.get('error', 'Regression teardown failed'))
            )
            if 'cleanup_error' in self.report:
                diagnostic.print(Text.assemble(('TEARDOWN FAILED: ', 'bold red'), self.report['cleanup_error']))
            if 'diagnostics_log' in self.report:
                diagnostic.print(
                    Text.assemble(
                        ('Guest journal: ', 'dim'), path_text(Path(self.report['diagnostics_log']), diagnostic)
                    )
                )
        self.display.console.print(
            Text.assemble(
                ('PASS: ' if code == 0 else 'FAIL: ', 'bold green' if code == 0 else 'bold red'),
                'regression artifacts: ',
                path_text(self.directory, self.display.console),
            )
        )
        return code


def main(options: Options) -> int:
    if options.timeout <= 0:
        raise ValueError('--timeout must be positive')
    with termination_signals():
        return Flow(options).execute()


if __name__ == '__main__':
    raise SystemExit(main(parse_options(Options)))
