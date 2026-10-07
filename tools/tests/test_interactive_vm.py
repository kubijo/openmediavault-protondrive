"""Persistent VM lifecycle tests using temporary disks and a fake local QMP server."""

import contextlib
import io
import json
import os
import pty
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Unpack, cast
from unittest.mock import Mock, patch

from rich.console import Console
from tests.integration import interactive_guest as guest_module

import interactive_vm
import vm_control
import vm_runtime
from process_output import ProcessOutput
from tool_data import decode, decode_value, items, string
from vm_control import VMState
from vm_runtime import RunOptions

MONIT_RELOAD_ERROR = """Failed to execute omv-salt deploy run nginx:
----------
          ID: monitor_nginx_service
    Function: module.run
      Result: False
     Comment: 'monit.monitor': False
Summary for protondrive-test
Succeeded: 21 (changed=12)
Failed:     1
[ERROR   ] stdout: There is no service named "nginx"
"""


class GuestMock:
    def __init__(self, ssh: list[str] | None = None) -> None:
        self.run = Mock()
        self.copy = Mock()
        self.python = Mock()
        self.ensure_idle = Mock()
        self.ssh = ssh or []


class OutputMock(ProcessOutput):
    def __init__(self, animate: bool = False) -> None:
        super().__init__(in_clanker=True)
        self.run = Mock()
        self.run_mock = Mock()
        self.stage_mock = Mock()
        self.run = self.run_mock
        self.stage = self.stage_mock
        self.animate = animate


def failed_apply(message: str) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        1, ['omv-rpc'], output=json.dumps({'response': None, 'error': {'message': message}})
    )


class InteractiveVMTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='omv-interactive-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = VMState(self.root / 'state')
        self.options = interactive_vm.Options('up', state_dir=self.state.root)
        self.output = ProcessOutput(in_clanker=True)
        self.buffer = io.StringIO()
        self.output.console = Console(file=self.buffer, color_system=None)

    def seed_state(self):
        self.state.instance.mkdir(parents=True)
        self.state.write({'initialized': True, 'ssh_port': 2222, 'http_port': 8080, 'remote_folder': '/my-files/test'})
        (self.state.instance / 'disk.qcow2').write_bytes(b'persistent login state')
        (self.state.instance / 'admin-password').write_text('fixture-password\n')

    def test_existing_state_does_not_resolve_new_base_or_replace_disk(self):
        self.seed_state()
        with patch.object(vm_runtime, 'resolve_base') as resolve:
            interactive_vm.create(self.options, self.state, self.output)
        resolve.assert_not_called()
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_real_persistent_overlay_survives_base_cache_removal(self):
        base = self.root / 'cached-base.qcow2'
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(base), '16M'], check=True, capture_output=True)
        package = self.root / 'plugin.deb'
        package.touch()
        self.options.image = base
        self.options.package = package
        runtime_run = vm_runtime.run

        def run(*args: str, text: bool = False, input: str | bytes | None = None, **kwargs: Unpack[RunOptions]):
            if args[0] == 'dpkg-deb':
                return Mock(stdout='python3')
            if text:
                assert input is None or isinstance(input, str)
                return runtime_run(*args, text=True, input=input, **kwargs)
            assert input is None or isinstance(input, bytes)
            return runtime_run(*args, input=input, **kwargs)

        with (
            self.state.lock(),
            patch.object(vm_runtime, 'resolve_base', return_value=base),
            patch.object(vm_runtime, 'run', side_effect=run),
        ):
            interactive_vm.create(self.options, self.state, self.output)
        base.unlink()
        result = subprocess.run(
            ['qemu-img', 'info', '--output=json', str(self.state.instance / 'disk.qcow2')],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(decode(result.stdout)['full-backing-filename'], str(self.state.instance / 'base.qcow2'))
        subprocess.run(['qemu-img', 'check', str(self.state.instance / 'disk.qcow2')], check=True, capture_output=True)
        self.assertEqual(self.state.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state.instance / 'admin-password').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state.instance / 'admin-password').read_text(), 'admin\n')
        configuration = decode((self.state.instance / 'interactive-init.json').read_text())
        self.assertEqual(configuration['admin_password'], 'admin')
        self.assertEqual(configuration['remote_folder'], '/my-files/open-media-vault-proton-backup-development')
        self.assertFalse(self.state.read()['initialized'])
        self.assertTrue((self.state.instance / 'key').exists())

    def test_reset_requires_confirmation_and_stopped_vm(self):
        self.seed_state()
        with self.assertRaisesRegex(ValueError, 'deletes'):
            interactive_vm.reset(self.options, self.state, self.output)
        self.options.yes = True
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            self.assertRaisesRegex(RuntimeError, 'Stop'),
        ):
            interactive_vm.reset(self.options, self.state, self.output)
        self.assertTrue(self.state.metadata.exists())
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.reset(self.options, self.state, self.output)
        self.assertFalse(self.state.instance.exists())

    def test_snapshot_restore_round_trip_requires_stopped_initialized_vm(self):
        self.seed_state()
        disk = self.state.instance / 'disk.qcow2'
        disk.unlink()
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '16M'], check=True, capture_output=True)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'write -P 0x41 0 512', str(disk)], check=True, capture_output=True
        )
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.snapshot(self.options, self.state, self.output)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'write -P 0x42 0 512', str(disk)], check=True, capture_output=True
        )
        with patch.object(self.state, 'running', return_value=None):
            interactive_vm.snapshot(self.options, self.state, self.output, restore=True)
        subprocess.run(
            ['qemu-io', '-f', 'qcow2', '-c', 'read -P 0x41 0 512', str(disk)], check=True, capture_output=True
        )
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            self.assertRaisesRegex(RuntimeError, 'Stop the VM'),
        ):
            interactive_vm.snapshot(self.options, self.state, self.output, restore=True)

    def test_lifecycle_lock_blocks_second_owner_and_reset(self):
        self.seed_state()
        self.options.yes = True
        with self.state.lock(), self.assertRaisesRegex(RuntimeError, 'already active'):
            interactive_vm.reset(self.options, VMState(self.state.root), self.output)
        self.assertTrue(self.state.metadata.exists())

    def test_stop_requests_graceful_shutdown_and_preserves_files(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            self.state.stop()
        qmp.assert_called_once_with(self.state.monitor, 'system_powerdown')
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_interactive_stop_uses_systemd_instead_of_ignored_power_button(self):
        guest: GuestMock = GuestMock()
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            interactive_vm.stop(self.state)
        guest.run.assert_called_once_with('systemctl', 'poweroff', '--no-block', timeout=15)
        qmp.assert_not_called()

    def test_force_stop_is_explicit_and_timeout_does_not_force_poweroff(self):
        with (
            patch.object(self.state, 'running', side_effect=[{'status': 'running'}, None]),
            patch.object(vm_control, 'qmp') as qmp,
        ):
            self.state.stop(force=True)
        qmp.assert_called_once_with(self.state.monitor, 'quit')
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(vm_control, 'qmp') as qmp,
            patch.object(vm_control.time, 'monotonic', side_effect=[0, 121]),
            self.assertRaisesRegex(RuntimeError, 'still running'),
        ):
            self.state.stop()
        qmp.assert_called_once_with(self.state.monitor, 'system_powerdown')

    def test_install_on_initialized_guest_does_not_send_initial_settings(self):
        self.seed_state()
        package = self.root / 'plugin.deb'
        package.touch()
        self.options.package = package
        (self.state.root / 'logs').mkdir()
        guest: GuestMock = GuestMock()
        guest.run.return_value = Mock(stdout='{"instanceuuid":"example-instance"}')
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(interactive_vm, 'setup_shell') as setup_shell,
        ):
            interactive_vm.install(self.options, self.state, self.output)
        setup_shell.assert_called_once_with(self.options, self.state)
        self.assertEqual(guest.copy.call_count, 1)
        assert guest.python.call_args is not None
        self.assertNotIn('/root/interactive-init.json', cast(tuple[object, ...], guest.python.call_args.args))
        self.assertEqual(guest.python.call_args.args[1], f'{interactive_vm.GUEST_TOOLS}/interactive_guest.py')
        self.assertEqual(self.state.read()['proton_instance_uuid'], 'example-instance')
        self.assertEqual((self.state.instance / 'disk.qcow2').read_bytes(), b'persistent login state')

    def test_ports_are_only_forwarded_on_localhost(self):
        command = vm_runtime.qemu_command(self.root, self.root / 'serial.log', {22: 2222, 80: 8080})
        network = command[command.index('-netdev') + 1]
        self.assertEqual(network, 'user,id=net0,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8080-:80')

    def test_up_reuses_running_vm_and_releases_lifecycle_lock(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'create') as create,
            patch.object(interactive_vm, 'install') as install,
        ):
            interactive_vm.up(self.options, self.state, self.output)
            create.assert_not_called()
            install.assert_not_called()
            with self.state.lock():
                pass

    def test_running_vm_refuses_port_changes_and_retries_unfinished_install(self):
        self.seed_state()
        metadata = self.state.read()
        metadata['initialized'] = False
        self.state.write(metadata)
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'install') as install,
        ):
            interactive_vm.up(self.options, self.state, self.output)
            install.assert_called_once()
            self.options.ssh_port = 2223
            with self.assertRaisesRegex(ValueError, 'Stop the VM'):
                interactive_vm.up(self.options, self.state, self.output)

    def test_failed_bringup_leaves_daemon_available_for_diagnosis(self):
        self.seed_state()
        with (
            patch.object(self.state, 'running', return_value=None),
            patch.object(interactive_vm.subprocess, 'run') as run,
            patch.object(vm_runtime, 'connect', side_effect=RuntimeError('SSH unavailable')),
            patch.object(interactive_vm, 'stop') as stop,
            self.assertRaisesRegex(RuntimeError, 'SSH unavailable'),
        ):
            interactive_vm.up(self.options, self.state, self.output)
        assert run.call_args is not None
        self.assertIn('-daemonize', items(cast(object, run.call_args.args[0])))
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        stop.assert_not_called()

    def test_ssh_exit_status_is_preserved_without_stopping_vm(self):
        self.seed_state()
        guest: GuestMock = GuestMock(ssh=['ssh', 'root@localhost'])
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'setup_shell'),
            patch.object(interactive_vm, 'describe'),
            patch.object(interactive_vm, 'connection', return_value=guest),
            patch.object(interactive_vm.subprocess, 'run', return_value=Mock(returncode=7)),
            patch.object(interactive_vm, 'stop') as stop,
        ):
            self.assertEqual(interactive_vm.open_shell(self.options, self.state, self.output), 7)
        stop.assert_not_called()

    def test_up_no_shell_returns_without_opening_ssh(self):
        self.options.no_shell = True
        with (
            patch.object(interactive_vm, 'up') as up,
            patch.object(interactive_vm, 'describe') as describe,
            patch.object(interactive_vm, 'open_shell') as shell,
        ):
            interactive_vm.main(self.options)
        up.assert_called_once()
        describe.assert_called_once()
        shell.assert_not_called()

    def test_guest_shell_installs_tools_without_reinstalling_plugin(self):
        self.seed_state()
        bundle = self.root / 'omv-protondrive-vm-tools.tar'
        bundle.touch()
        self.options.guest_bundle = bundle
        guest: GuestMock = GuestMock()
        directory = f'{interactive_vm.GUEST_TOOLS}.{bundle.parent.name}.test'

        def run(*args: str, text: bool = False, input: str | bytes | None = None, **kwargs: Unpack[RunOptions]):
            return Mock(returncode=0, stdout=directory + '\n' if args[0] == 'mktemp' else '')

        guest.run.side_effect = run
        with patch.object(interactive_vm, 'connection', return_value=guest):
            interactive_vm.setup_shell(self.options, self.state)
        self.assertEqual(guest.run.call_args_list[0].args, ('test', '-f', '/var/lib/protondrive-interactive-vm'))
        guest.copy.assert_called_once_with(bundle)
        guest.run.assert_any_call('tar', '-xf', f'/root/{bundle.name}', '-C', directory, '--strip-components=1')
        guest.run.assert_any_call('chmod', '0755', directory)
        guest.run.assert_any_call('ln', '-sfn', directory, f'{interactive_vm.GUEST_TOOLS}.next')
        guest.run.assert_any_call('mv', '-Tf', f'{interactive_vm.GUEST_TOOLS}.next', interactive_vm.GUEST_TOOLS)
        guest.run.assert_any_call('ln', '-sfn', f'{interactive_vm.GUEST_TOOLS}/just', '/usr/local/bin/just')
        guest.run.assert_any_call('ln', '-sfn', f'{interactive_vm.GUEST_TOOLS}/guest.just', '/root/justfile')
        guest.run.assert_any_call(
            'tee',
            f'{interactive_vm.GUEST_TOOLS}/.bundle-id',
            input=bundle.parent.name + '\n',
            text=True,
            capture_output=True,
        )
        self.assertEqual(guest.run.call_args.args, ('tee', f'{interactive_vm.GUEST_TOOLS}/.bundle-id'))
        guest.python.assert_not_called()

    def test_guest_shell_moves_legacy_directory_before_activation(self):
        self.seed_state()
        bundle = self.root / 'omv-protondrive-vm-tools.tar'
        bundle.touch()
        self.options.guest_bundle = bundle
        guest: GuestMock = GuestMock()

        def run(*args: str, text: bool = False, input: str | bytes | None = None, **kwargs: Unpack[RunOptions]):
            if args[0] == 'mktemp':
                return Mock(returncode=0, stdout=f'{interactive_vm.GUEST_TOOLS}.new\n')
            if args == ('test', '-L', interactive_vm.GUEST_TOOLS):
                return Mock(returncode=1, stdout='')
            return Mock(returncode=0, stdout='')

        guest.run.side_effect = run
        with patch.object(interactive_vm, 'connection', return_value=guest):
            interactive_vm.setup_shell(self.options, self.state)
        guest.run.assert_any_call('mv', '-T', interactive_vm.GUEST_TOOLS, f'{interactive_vm.GUEST_TOOLS}.legacy')

    def test_guest_shell_reuses_matching_built_bundle(self):
        self.seed_state()
        bundle = self.root / 'omv-protondrive-vm-tools.tar'
        bundle.touch()
        self.options.guest_bundle = bundle
        guest: GuestMock = GuestMock()
        guest.run.side_effect = [Mock(returncode=0), Mock(returncode=0, stdout=bundle.parent.name + '\n')]
        with patch.object(interactive_vm, 'connection', return_value=guest):
            interactive_vm.setup_shell(self.options, self.state)
        guest.copy.assert_not_called()
        self.assertEqual(guest.run.call_count, 2)

    def test_describe_does_not_expose_saved_credentials(self):
        self.seed_state()
        interactive_vm.describe(self.state, self.output, 'STOPPED')
        self.assertNotIn('fixture-password', self.buffer.getvalue())

    def test_interactive_guest_refuses_host_and_upgrade_preserves_settings(self):
        guest = guest_module
        with (
            patch.object(guest.Path, 'exists', return_value=False),
            self.assertRaisesRegex(SystemExit, 'interactive VM'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'initialize') as initialize,
            patch.object(guest, 'wait_for_monit') as wait,
            patch.object(guest, 'brand_web_ui'),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        initialize.assert_not_called()
        self.assertIn('--reinstall', run.call_args_list[0].args)
        wait.assert_called_once()

    def test_monit_readiness_retries_then_succeeds_and_timeout_fails(self):
        guest = guest_module
        failed = Mock(returncode=1, stdout='', stderr='socket unavailable')
        with (
            patch.object(guest.subprocess, 'run', side_effect=[failed, Mock(returncode=0), Mock(returncode=0)]) as run,
            patch.object(guest.time, 'sleep') as sleep,
        ):
            guest.wait_for_monit()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ['monit', 'status', 'nginx'],
                ['monit', 'status', 'nginx'],
                ['monit', 'status', 'php-fpm'],
            ],
        )
        sleep.assert_called_once_with(1)
        with (
            patch.object(guest.subprocess, 'run', return_value=failed),
            self.assertRaisesRegex(RuntimeError, 'socket unavailable'),
        ):
            guest.wait_for_monit(timeout=0)

    def test_web_deployment_waits_for_monit_and_does_not_ignore_failure(self):
        guest = guest_module
        steps: list[tuple[str, ...]] = []

        def record_step(*args: str, **kwargs: object) -> None:
            steps.append(args)

        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run', side_effect=record_step),
            patch.object(guest, 'wait_for_monit', side_effect=lambda: steps.append(('ready',))),
            patch.object(guest, 'brand_web_ui'),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        readiness = steps.index(('ready',))
        self.assertEqual(steps[readiness - 1], ('omv-salt', 'deploy', 'run', 'monit'))
        apply = steps[readiness + 1]
        self.assertEqual(apply[:-1], ('omv-rpc', '-u', 'admin', 'Config', 'applyChanges'))
        self.assertEqual(decode(apply[-1]), {'modules': list(guest.INSTALL_MODULES), 'force': True})
        self.assertEqual(sum('applyChanges' in step for step in steps), 1)
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'wait_for_monit', side_effect=RuntimeError('not ready')),
            patch.object(guest, 'check_pending_changes'),
            patch.dict(guest.os.environ),
            self.assertRaisesRegex(RuntimeError, 'not ready'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        self.assertEqual(run.call_args.args, ('omv-salt', 'deploy', 'run', 'monit'))

    def test_apply_retries_only_after_monit_readiness_and_pending_change_check(self):
        guest = guest_module
        for service, state in (('nginx', 'monitor_nginx_service'), ('php-fpm', 'monitor_phpfpm_service')):
            message = MONIT_RELOAD_ERROR.replace('monitor_nginx_service', state).replace('"nginx"', f'"{service}"')
            for failure in (
                message,
                message.replace(
                    f'There is no service named "{service}"',
                    'Unix socket /run/monit.sock connection error -- No such file or directory',
                ),
            ):
                with (
                    self.subTest(service=service, failure=failure),
                    patch.object(guest, 'run', side_effect=[failed_apply(failure), None]) as run,
                    patch.object(guest, 'wait_for_monit') as wait,
                    patch.object(guest, 'check_pending_changes') as check,
                ):
                    guest.apply_configuration(*guest.INSTALL_MODULES)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0], run.call_args_list[1])
                wait.assert_called_once()
                check.assert_called_once()

    def test_apply_does_not_retry_unrelated_or_multiple_failures(self):
        guest = guest_module
        for message in (
            'nginx configuration is invalid',
            MONIT_RELOAD_ERROR.replace('monitor_nginx_service', 'test_nginx_service_config'),
            MONIT_RELOAD_ERROR.replace('Failed:     1', 'Failed:     2'),
        ):
            with (
                self.subTest(message=message),
                patch.object(guest, 'run', side_effect=failed_apply(message)) as run,
                patch.object(guest, 'wait_for_monit') as wait,
                self.assertRaisesRegex(RuntimeError, 'OMV configuration apply failed'),
            ):
                guest.apply_configuration(*guest.INSTALL_MODULES)
            run.assert_called_once()
            wait.assert_not_called()

    def test_apply_retry_limit_and_new_pending_changes_still_fail(self):
        guest = guest_module
        for pending_error, attempts in ((None, 3), (RuntimeError('unrelated pending change'), 1)):
            with (
                self.subTest(pending_error=pending_error),
                patch.object(guest, 'run', side_effect=failed_apply(MONIT_RELOAD_ERROR)) as run,
                patch.object(guest, 'wait_for_monit'),
                patch.object(guest, 'check_pending_changes', side_effect=pending_error),
                self.assertRaises(RuntimeError),
            ):
                guest.apply_configuration(*guest.INSTALL_MODULES)
            self.assertEqual(run.call_count, attempts)

    def test_failed_configuration_apply_stops_installation(self):
        guest = guest_module
        failure = subprocess.CalledProcessError(1, ['omv-rpc', 'Config', 'applyChanges'])
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'apply_configuration', side_effect=failure),
            patch.object(guest, 'check_pending_changes'),
            patch.object(guest, 'brand_web_ui') as branding,
            patch.dict(guest.os.environ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        branding.assert_not_called()
        self.assertFalse(any(call.args[0] == 'omv-mkworkbench' for call in run.call_args_list))

    def test_install_without_dirty_modules_file_reaches_configuration_apply(self):
        guest = guest_module
        pending = self.root / 'dirtymodules.json'
        with (
            patch.object(guest, 'DIRTY_MODULES', pending),
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run'),
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'apply_configuration') as apply,
            patch.object(guest, 'brand_web_ui'),
            patch.dict(guest.os.environ),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        apply.assert_called_once_with(*guest.INSTALL_MODULES)
        self.assertFalse(pending.exists())

    def test_pending_state_accepts_empty_list_but_rejects_malformed_content(self):
        guest = guest_module
        pending = self.root / 'dirtymodules.json'
        with patch.object(guest, 'DIRTY_MODULES', pending):
            pending.write_text('[]')
            guest.check_pending_changes()
            for content in ('', '{', '{}', '[null]'):
                with self.subTest(content=content):
                    pending.write_text(content)
                    with self.assertRaises((ValueError, RuntimeError)):
                        guest.check_pending_changes()

    def test_unreadable_pending_state_is_not_treated_as_clean(self):
        guest = guest_module
        with (
            patch.object(guest.Path, 'read_text', side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            guest.check_pending_changes()

    def test_unrelated_pending_changes_block_install_before_mutation(self):
        guest = guest_module
        pending = self.root / 'dirtymodules.json'
        pending.write_text('["samba", "protondrive"]')
        with (
            patch.object(guest, 'DIRTY_MODULES', pending),
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run') as run,
            self.assertRaisesRegex(RuntimeError, 'samba'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        run.assert_not_called()
        self.assertEqual(decode_value(pending.read_text()), ['samba', 'protondrive'])
        pending.write_text('["protondrive"]')
        with patch.object(guest, 'DIRTY_MODULES', pending):
            guest.check_pending_changes()

    def test_pending_changes_are_rechecked_before_final_apply(self):
        guest = guest_module
        with (
            patch.object(guest.Path, 'exists', return_value=True),
            patch.object(guest, 'run'),
            patch.object(guest, 'wait_for_monit'),
            patch.object(guest, 'check_pending_changes', side_effect=[None, RuntimeError('samba')]),
            patch.object(guest, 'apply_configuration') as apply,
            patch.dict(guest.os.environ),
            self.assertRaisesRegex(RuntimeError, 'samba'),
        ):
            guest.main(Path('/tmp/plugin.deb'))
        apply.assert_not_called()

    def test_retry_cannot_overwrite_files_until_remote_cancellation_is_confirmed(self):
        self.seed_state()
        self.options.package = self.root / 'plugin.deb'
        self.options.package.touch()
        guest: GuestMock = GuestMock()
        guest.ensure_idle.side_effect = RuntimeError('remote job still running')
        with (
            patch.object(self.state, 'running', return_value={'status': 'running'}),
            patch.object(interactive_vm, 'connection', return_value=guest),
            self.assertRaisesRegex(RuntimeError, 'remote job still running'),
        ):
            interactive_vm.install(self.options, self.state, self.output)
        guest.copy.assert_not_called()

    def test_branding_is_idempotent_and_preserves_packaged_html(self):
        guest = guest_module
        webroot = self.root / 'html'
        snippets = self.root / 'snippets'
        webroot.mkdir()
        index = webroot / 'index.html'
        index.write_text('packaged OMV entry point')
        with patch.object(guest, 'run'):
            guest.brand_web_ui(webroot, snippets)
            first = (snippets / '90-interactive-vm.conf').read_text()
            guest.brand_web_ui(webroot, snippets)
        self.assertEqual(index.read_text(), 'packaged OMV entry point')
        self.assertEqual((snippets / '90-interactive-vm.conf').read_text(), first)
        self.assertEqual(
            (webroot / 'interactive-vm.css').read_text(),
            Path(string(guest.__file__)).with_name('interactive-vm.css').read_text(),
        )
        source = Path(__file__).resolve().parents[2]
        shutil.copyfile(source / 'src/nginx/90-protondrive.conf', snippets / '90-protondrive.conf')
        parameters = Path(os.environ.get('PROTONDRIVE_NGINX_FASTCGI_PARAMS', '/etc/nginx/fastcgi_params'))
        shutil.copyfile(parameters, self.root / 'fastcgi_params')
        configuration = self.root / 'nginx.conf'
        shutil.copyfile(Path(__file__).parent / 'fixtures/nginx.conf', configuration)
        result = subprocess.run(
            ['nginx', '-t', '-e', 'stderr', '-p', str(self.root), '-c', str(configuration)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_remote_pty_has_closed_input_and_plain_mode_does_not_request_pty(self):
        guest = vm_runtime.guest_connection(self.root, self.root / 'key', 2222)
        fixture = Path(__file__).parent / 'fixtures/process_output.py'
        output: OutputMock = OutputMock(animate=True)
        guest.python(output, str(fixture), self.root / 'log', 10, 'remote-environment')
        assert output.run_mock.call_args is not None
        command = [string(part) for part in items(cast(object, output.run_mock.call_args.args[0]))]
        self.assertEqual(command[1], '-tt')
        self.assertIn('--property=KillMode=control-group', command[-1])
        # Exercise the payload's terminal and stdin locally; supervision is tested separately.
        words = shlex.split(command[-1])
        payload = shlex.join(words[words.index('env') : words.index('<')]) + ' < /dev/null'
        master, slave = pty.openpty()
        try:
            with patch.dict(os.environ, {'PATH': f'{Path(sys.executable).parent}:{os.environ["PATH"]}'}):
                result = subprocess.run(
                    ['sh', '-c', payload],
                    input=b'input must not reach guest',
                    stdout=slave,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'input disconnected', os.read(master, 4096))
        finally:
            os.close(master)
            os.close(slave)
        output.animate = False
        guest.python(output, str(fixture), self.root / 'log', 10)
        assert output.run_mock.call_args is not None
        command = [string(part) for part in items(cast(object, output.run_mock.call_args.args[0]))]
        self.assertEqual(command[1], '-T')
        self.assertIn('TERM=dumb', command[-1])

    def test_interactive_connection_does_not_enable_disposable_test_scripts(self):
        guest: GuestMock = GuestMock()
        guest.ssh = ['ssh', 'fixture-guest']
        output: OutputMock = OutputMock()

        def stage_context(title: str):
            return contextlib.nullcontext()

        output.stage_mock.side_effect = stage_context
        runtime = vm_runtime.Options(image=self.root, package=self.root, reports=self.root)
        with (
            patch.object(vm_runtime, 'guest_connection', return_value=guest),
            patch.object(vm_runtime.subprocess, 'run', return_value=Mock(returncode=0)),
        ):
            vm_runtime.connect(runtime, self.root, self.root / 'key', 2222, Mock(), output, '', disposable=False)
        guest.run.assert_not_called()


class MonitorTests(unittest.TestCase):
    def test_monitor_disconnection_during_shutdown_is_treated_as_stopped(self) -> None:
        state = VMState(Path('/unused'))
        with patch.object(vm_control, 'qmp', side_effect=BrokenPipeError):
            self.assertIsNone(state.running())
        with patch.object(vm_control, 'qmp', side_effect=[{'status': 'running'}, BrokenPipeError(), BrokenPipeError()]):
            state.stop(force=True)

    def test_qmp_negotiates_capabilities_and_ignores_events(self):
        with tempfile.TemporaryDirectory(prefix='omv-qmp-') as temporary, socket.socket(socket.AF_UNIX) as listener:
            path = Path(temporary) / 'qmp.sock'
            listener.bind(str(path))
            listener.listen(1)
            listener.settimeout(5)

            def server():
                with listener.accept()[0] as connection, connection.makefile('rwb', buffering=0) as stream:
                    connection.settimeout(5)
                    stream.write(json.dumps({'QMP': {'version': {}, 'capabilities': []}}).encode() + b'\r\n')
                    for name in ('qmp_capabilities', 'query-status'):
                        request = decode(stream.readline())
                        self.assertEqual(request['execute'], name)
                        stream.write(json.dumps({'event': 'RESUME'}).encode() + b'\r\n')
                        response = {'return': {'status': 'running'}, 'id': request['id']}
                        stream.write(json.dumps(response).encode() + b'\r\n')

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(server)
                self.assertEqual(vm_control.qmp(path, 'query-status'), {'status': 'running'})
                future.result(timeout=5)
